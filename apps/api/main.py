"""API REST.

Diseño según el nivel 2 del modelo de madurez de Richardson:

  - **Recursos como sustantivos.** `POST /analyses` crea un análisis. No existe
    `/compare` ni `/generate`: un verbo en la URL es RPC disfrazado de HTTP, y
    los verbos ya los pone el protocolo.
  - **Códigos de estado con significado.** 202 cuando el trabajo fue aceptado
    pero no terminó, 404 cuando el recurso no existe, 406 cuando existe pero no
    en el formato pedido, 422 cuando la entrada es inválida.
  - **Negociación de contenido.** El mismo análisis se sirve como JSON o como
    PDF según el header `Accept`. El PDF no es otro recurso: es otra
    representación del mismo.

La decisión de más peso es que `POST /analyses` responda **202 Accepted**. Hoy
el análisis es SQL puro y termina en milisegundos, así que un 201 con el
resultado adentro funcionaría. Pero cuando entre el LLM va a tardar cerca de dos
minutos en esta máquina, y ahí el contrato tendría que cambiar y romper a todos
los consumidores. Se fija ahora, mientras no cuesta nada.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from io import BytesIO

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from apps.api.analisis import construir_informe
from apps.api.schemas import (
    Analisis,
    EstadoAnalisis,
    ListaAnalisis,
    ListaProductos,
    Producto,
    Salud,
    SolicitudAnalisis,
)
from apps.api.store import almacen
from core.db import cursor_lectura, hay_base_disponible
from core.report_pdf import render_pdf

VERSION = "0.1.0"

app = FastAPI(
    title="AI Market & Product Intelligence",
    version=VERSION,
    description=(
        "Análisis comercial con evidencia trazable. Los KPIs se calculan por "
        "consulta SQL; ningún número proviene de un modelo de lenguaje."
    ),
)


# --- helpers -----------------------------------------------------------------

def _producto_existe(product_id: str) -> bool:
    with cursor_lectura() as cur:
        return cur.execute(
            "SELECT 1 FROM dbo.products WHERE id = ?", (product_id,)
        ).fetchone() is not None


def rango_validado(
    desde: date = Query(description="Inicio del período, inclusive"),
    hasta: date = Query(description="Fin del período, inclusive"),
) -> tuple[date, date]:
    """Valida el rango en los query params.

    FastAPI valida tipos solo; que `desde` sea anterior a `hasta` es una regla
    del dominio y hay que escribirla. Un rango invertido no es un error del
    servidor: es una entrada inválida, y le corresponde un 422.
    """
    if desde > hasta:
        raise HTTPException(
            status_code=422,
            detail=f"el rango está invertido: 'desde' ({desde}) es posterior "
                   f"a 'hasta' ({hasta})",
        )
    return desde, hasta


# --- salud -------------------------------------------------------------------

@app.get("/health", response_model=Salud, tags=["operación"])
def salud() -> Salud:
    disponible = hay_base_disponible()
    return Salud(
        estado="ok" if disponible else "degradado",
        base_de_datos="disponible" if disponible else "no disponible",
        version=VERSION,
    )


# --- recurso: products -------------------------------------------------------

@app.get("/products", response_model=ListaProductos, tags=["productos"])
def listar_productos(
    limite: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    categoria: str | None = Query(None),
) -> ListaProductos:
    filtro = "WHERE category = ?" if categoria else ""
    params: tuple = (categoria,) if categoria else ()

    with cursor_lectura() as cur:
        total = cur.execute(
            f"SELECT COUNT(*) FROM dbo.products {filtro}", params
        ).fetchone()[0]
        filas = cur.execute(
            f"""SELECT id, brand, category, price, cost, launch_date
                FROM dbo.products {filtro}
                ORDER BY id
                OFFSET ? ROWS FETCH NEXT ? ROWS ONLY""",
            (*params, offset, limite),
        ).fetchall()

    return ListaProductos(
        total=total,
        items=[Producto(id=f[0], brand=f[1], category=f[2], price=float(f[3]),
                        cost=float(f[4]), launch_date=f[5]) for f in filas],
    )


@app.get("/products/{product_id}", response_model=Producto, tags=["productos"])
def obtener_producto(product_id: str) -> Producto:
    with cursor_lectura() as cur:
        fila = cur.execute(
            """SELECT id, brand, category, price, cost, launch_date
               FROM dbo.products WHERE id = ?""", (product_id,)
        ).fetchone()
    if fila is None:
        raise HTTPException(404, f"no existe el producto {product_id}")
    return Producto(id=fila[0], brand=fila[1], category=fila[2],
                    price=float(fila[3]), cost=float(fila[4]), launch_date=fila[5])


@app.get("/products/{product_id}/metrics", tags=["productos"])
def metricas_de_un_producto(product_id: str, rango=Depends(rango_validado)):
    """KPIs del producto en el período. Todos calculados por SQL."""
    if not _producto_existe(product_id):
        raise HTTPException(404, f"no existe el producto {product_id}")
    from core.kpis import metricas_de_producto
    return metricas_de_producto(product_id, *rango)


# --- recurso: analyses -------------------------------------------------------

def _procesar(analysis_id: str) -> None:
    """Ejecuta el análisis y actualiza su estado.

    Corre como tarea de fondo. Hoy termina en milisegundos; cuando el grafo con
    LLM ocupe su lugar, esto pasará a un worker aparte sin que el contrato de la
    API cambie — que es exactamente para lo que se eligió el 202.
    """
    registro = almacen.obtener(analysis_id)
    if registro is None:
        return

    registro.estado = EstadoAnalisis.PROCESANDO
    almacen.guardar(registro)

    try:
        consulta = (
            f"Comparar {', '.join(registro.product_ids)} "
            f"entre {registro.desde} y {registro.hasta}"
        )
        registro.informe = construir_informe(
            analysis_id, consulta, registro.product_ids,
            registro.desde, registro.hasta,
        )
        registro.etapas = [p.nodo for p in registro.informe.trace]
        registro.estado = EstadoAnalisis.COMPLETADO
    except Exception as e:  # el fallo viaja en el recurso, no revienta la API
        registro.estado = EstadoAnalisis.FALLIDO
        registro.error = f"{type(e).__name__}: {e}"
    almacen.guardar(registro)


@app.post("/analyses", response_model=Analisis, status_code=202, tags=["análisis"])
def crear_analisis(
    solicitud: SolicitudAnalisis, tareas: BackgroundTasks, respuesta: Response
) -> Analisis:
    """Crea un análisis.

    Responde **202 Accepted**, no 201: el recurso ya existe y es consultable,
    pero el trabajo puede no haber terminado. El header `Location` indica dónde
    seguir su estado.
    """
    faltantes = [p for p in solicitud.product_ids if not _producto_existe(p)]
    if faltantes:
        # 422 y no 404: el recurso pedido es /analyses, que sí existe. Lo
        # inválido es el contenido de la solicitud.
        raise HTTPException(422, f"no existen estos productos: {faltantes}")

    analysis_id = f"req-{uuid.uuid4().hex[:12]}"
    registro = Analisis(
        id=analysis_id,
        estado=EstadoAnalisis.PENDIENTE,
        creado_en=datetime.now(),
        product_ids=solicitud.product_ids,
        desde=solicitud.desde,
        hasta=solicitud.hasta,
    )
    almacen.guardar(registro)
    tareas.add_task(_procesar, analysis_id)

    respuesta.headers["Location"] = f"/analyses/{analysis_id}"
    return registro


@app.get("/analyses", response_model=ListaAnalisis, tags=["análisis"])
def listar_analisis(
    limite: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> ListaAnalisis:
    total, items = almacen.listar(limite, offset)
    return ListaAnalisis(total=total, items=items)


def _obtener_o_404(analysis_id: str) -> Analisis:
    registro = almacen.obtener(analysis_id)
    if registro is None:
        raise HTTPException(404, f"no existe el análisis {analysis_id}")
    return registro


def _pdf_de(registro: Analisis) -> Response:
    if registro.estado != EstadoAnalisis.COMPLETADO or registro.informe is None:
        # 409: el recurso existe pero su estado actual no admite esta
        # representación. No es un 404 —el análisis está ahí— ni un 500.
        raise HTTPException(
            409,
            f"el análisis está en estado '{registro.estado.value}': "
            "el PDF solo existe cuando está completado",
        )
    buffer = BytesIO()
    render_pdf(registro.informe, buffer)
    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition":
                f'attachment; filename="informe-{registro.id}.pdf"'
        },
    )


# La ruta con extensión se declara ANTES que la genérica: si no, `{analysis_id}`
# capturaría "abc.pdf" como un id y nunca llegaría acá.
@app.get("/analyses/{analysis_id}.pdf", tags=["análisis"],
         response_class=Response, responses={200: {"content": {"application/pdf": {}}}})
def descargar_pdf(analysis_id: str) -> Response:
    """Atajo por extensión.

    Convive con la negociación de contenido porque un `<a href>` de navegador no
    puede mandar el header `Accept`. No es redundancia: es reconocer cómo
    funcionan los clientes reales.
    """
    return _pdf_de(_obtener_o_404(analysis_id))


@app.get("/analyses/{analysis_id}", tags=["análisis"],
         responses={200: {"content": {"application/json": {},
                                      "application/pdf": {}}}})
def obtener_analisis(analysis_id: str, request: Request):
    """Devuelve el análisis en el formato pedido por `Accept`.

    Un recurso, una URL, varias representaciones.
    """
    registro = _obtener_o_404(analysis_id)
    accept = request.headers.get("accept", "*/*").lower()

    if "application/pdf" in accept:
        return _pdf_de(registro)
    if "application/json" in accept or "*/*" in accept or accept.strip() == "":
        return JSONResponse(content=registro.model_dump(mode="json"))

    # 406: el recurso existe, pero no en el formato solicitado. Devolver JSON
    # igual sería mentirle al cliente sobre lo que está recibiendo.
    raise HTTPException(
        406, "formatos disponibles: application/json, application/pdf"
    )


@app.delete("/analyses/{analysis_id}", status_code=204, tags=["análisis"])
def eliminar_analisis(analysis_id: str) -> Response:
    if not almacen.eliminar(analysis_id):
        raise HTTPException(404, f"no existe el análisis {analysis_id}")
    return Response(status_code=204)
