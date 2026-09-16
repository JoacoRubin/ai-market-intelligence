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

import hmac
import os
import uuid
from datetime import date, datetime
from io import BytesIO

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Security,
)
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader

from agent.llm import ClienteLLM, crear_cliente
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
from apps.jobs.cola import MAX_ANALISIS_EN_VUELO, despachar, hay_capacidad
from core.db import cursor_lectura, hay_base_disponible

# En runtime, y no bajo `TYPE_CHECKING`. `metricas_de_un_producto` no declara
# `response_model=`: FastAPI arma el modelo de respuesta LEYENDO la anotación de
# retorno, y con `from __future__ import annotations` esa anotación es el string
# "MetricaProducto". Escondido tras el guardia, el nombre no existía en runtime y
# pydantic no podía resolverlo:
#
#     PydanticUserError: `TypeAdapter[Annotated[ForwardRef('MetricaProducto')]]`
#     is not fully defined
#
# Con eso caían `GET /products/{id}/metrics` y `GET /openapi.json` — y con el
# segundo, `/docs`, que es la primera puerta por la que alguien entra a la API.
#
# El guardia buscaba diferir `core.kpis`, y eso SIGUE INTACTO: ese import vive
# adentro del endpoint. Pero `core.report` es otro módulo y ya está cargado igual
# —lo trae `core.report_pdf` acá abajo—, así que traerlo acá no difiere nada.
from core.report import MetricaProducto
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


# --- credencial --------------------------------------------------------------

NOMBRE_HEADER_CLAVE = "X-API-Key"

# `auto_error=False`: sin esto FastAPI rechaza con 403 cuando el header falta,
# antes de que este módulo pueda decidir si la clave hace falta. La decisión es
# de `requiere_clave`, no del esquema.
_esquema_clave = APIKeyHeader(name=NOMBRE_HEADER_CLAVE, auto_error=False)


def requiere_clave(clave: str | None = Security(_esquema_clave)) -> None:
    """Exige `X-API-Key` **solo si** `API_KEY` está configurada.

    Con `API_KEY` sin definir la API se comporta exactamente como antes: es el
    modo local, donde el único control es el bind a `127.0.0.1` del compose y
    agregar una credencial obligatoria solo rompería `tasks.ps1`, la suite y el
    dashboard sin proteger nada que ya no esté protegido.

    En cuanto la variable existe, el control es real y aplica a todo lo que no
    sea un health check. Los health quedan afuera a propósito: el `HEALTHCHECK`
    del Dockerfile le pega a `/health` sin credencial, y pedírsela dejaría el
    container `unhealthy` para siempre.

    La comparación es de tiempo constante. Un `==` sobre un secreto filtra por
    cuánto tarda en fallar cuántos caracteres del prefijo eran correctos, y
    escribir la versión insegura acá invita a copiarla a un lugar donde sí
    importe.
    """
    esperada = os.getenv("API_KEY", "").strip()
    if not esperada:
        return
    if clave is None or not hmac.compare_digest(clave, esperada):
        raise HTTPException(
            401,
            f"credencial inválida o ausente: se espera el header {NOMBRE_HEADER_CLAVE}",
        )


# Se declara ruta por ruta y no como dependencia global de la app: una global
# alcanzaría también a los health, y la excepción quedaría escondida en un
# `if` sobre el path. Así la asimetría se ve en el decorador de cada endpoint
# —mismo criterio que `core/db.py` con las dos conexiones.
PROTEGIDO = [Depends(requiere_clave)]


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

def _estado_readiness() -> Salud:
    disponible = hay_base_disponible()
    return Salud(
        estado="ok" if disponible else "degradado",
        base_de_datos="disponible" if disponible else "no disponible",
        version=VERSION,
    )


@app.get("/health/live", response_model=Salud, tags=["operación"])
def liveness() -> Salud:
    """El proceso atiende HTTP; no consulta dependencias externas."""
    return Salud(
        estado="ok",
        base_de_datos="no verificada",
        version=VERSION,
    )


@app.get(
    "/health/ready",
    response_model=Salud,
    tags=["operación"],
    responses={503: {"model": Salud}},
)
def readiness(respuesta: Response) -> Salud:
    """Indica si la API está lista para servir operaciones con datos."""
    estado = _estado_readiness()
    if estado.estado != "ok":
        respuesta.status_code = 503
    return estado


@app.get("/health", response_model=Salud, tags=["operación"])
def salud() -> Salud:
    """Compatibilidad: conserva el 200 histórico aun estando degradado."""
    return _estado_readiness()


# --- recurso: products -------------------------------------------------------

@app.get("/products", response_model=ListaProductos, tags=["productos"],
         dependencies=PROTEGIDO)
def listar_productos(
    limite: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    categoria: str | None = Query(None),
) -> ListaProductos:
    filtro = "WHERE category = ?" if categoria else ""
    params: tuple[str, ...] = (categoria,) if categoria else ()

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


@app.get("/products/{product_id}", response_model=Producto, tags=["productos"],
         dependencies=PROTEGIDO)
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


@app.get("/products/{product_id}/metrics", tags=["productos"],
         dependencies=PROTEGIDO)
def metricas_de_un_producto(
    product_id: str,
    rango: tuple[date, date] = Depends(rango_validado),
) -> MetricaProducto:
    """KPIs del producto en el período. Todos calculados por SQL."""
    if not _producto_existe(product_id):
        raise HTTPException(404, f"no existe el producto {product_id}")
    from core.kpis import metricas_de_producto
    return metricas_de_producto(product_id, *rango)


# --- recurso: analyses -------------------------------------------------------

def obtener_cliente_llm() -> ClienteLLM:
    """Dependencia inyectable con el cliente del modelo.

    Que sea una dependencia de FastAPI y no un objeto global es lo que permite
    testear la API con un doble. Sin eso, cada test esperaría los ~2m44s que
    tarda el agente real en esta máquina.

    Cuál de los dos adaptadores se construye lo decide `LLM_BACKEND` (ADR-007).
    La API no lo sabe ni le importa: ese es el punto del puerto.
    """
    return crear_cliente()


# `estado_inicial` y `procesar_analisis` vivían acá y se mudaron a
# `apps/jobs/tareas.py` al entrar el worker (ADR-012): un proceso que solo
# ejecuta análisis no tiene por qué importar FastAPI y los handlers para
# hacerlo. Lo que queda en este módulo es lo que atiende HTTP.


@app.post("/analyses", response_model=Analisis, status_code=202, tags=["análisis"],
          dependencies=PROTEGIDO,
          responses={429: {"description": "Demasiados análisis sin terminar"}})
def crear_analisis(
    solicitud: SolicitudAnalisis,
    tareas: BackgroundTasks,
    respuesta: Response,
    cliente: ClienteLLM = Depends(obtener_cliente_llm),
) -> Analisis:
    """Crea un análisis, en forma estructurada o en lenguaje natural.

    Responde **202 Accepted**, no 201: el recurso ya existe y es consultable,
    pero el trabajo puede no haber terminado. Con el agente conectado eso pasó
    de ser previsión a ser necesidad — el análisis tarda cerca de dos minutos.
    """
    # `SolicitudAnalisis._forma_coherente` ya garantiza que viene exactamente
    # una de las dos formas, pero ese invariante vive en un validador y el
    # verificador de tipos no puede leerlo. Se liga a una variable local para
    # que la garantía quede escrita donde se usa: si algún día el validador
    # cambia, esto falla acá y no con un AttributeError en producción.
    # Control de admisión ANTES de validar productos y de crear el recurso: si
    # el sistema no tiene capacidad, no tiene sentido gastar consultas a la base
    # ni dejar un análisis pendiente que nadie va a poder atender.
    #
    # 429 y no 503: el servicio está sano, lo que falta es capacidad ahora. Un
    # 503 le diría al cliente que el sistema está caído y que reintente contra
    # otro lado; un 429 le dice la verdad — esperá y volvé a pedir.
    if not hay_capacidad(almacen):
        raise HTTPException(
            429,
            f"hay {MAX_ANALISIS_EN_VUELO} análisis o más sin terminar. "
            "Esperá a que la cola se vacíe y reintentá.",
        )

    product_ids = solicitud.product_ids
    if product_ids:
        faltantes = [p for p in product_ids if not _producto_existe(p)]
        if faltantes:
            # 422 y no 404: el recurso pedido es /analyses, que sí existe. Lo
            # inválido es el contenido de la solicitud.
            raise HTTPException(422, f"no existen estos productos: {faltantes}")
        consulta = (
            f"Comparar {', '.join(product_ids)} "
            f"entre {solicitud.desde} y {solicitud.hasta}"
        )
    else:
        consulta = (solicitud.consulta or "").strip()

    analysis_id = f"req-{uuid.uuid4().hex[:12]}"
    registro = Analisis(
        id=analysis_id,
        estado=EstadoAnalisis.PENDIENTE,
        creado_en=datetime.now(),
        consulta=consulta,
        product_ids=solicitud.product_ids or [],
        desde=solicitud.desde,
        hasta=solicitud.hasta,
    )
    almacen.guardar(registro)
    # Dónde corre el análisis lo decide `despachar` según JOBS_BACKEND: en
    # este mismo proceso o en un worker aparte (ADR-012). El handler no lo
    # sabe, que es el punto — el contrato de la respuesta es el mismo 202 en
    # los dos casos.
    despachar(analysis_id, tareas, cliente, almacen)

    respuesta.headers["Location"] = f"/analyses/{analysis_id}"
    return registro


@app.get("/analyses", response_model=ListaAnalisis, tags=["análisis"],
         dependencies=PROTEGIDO)
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
@app.get("/analyses/{analysis_id}.pdf", tags=["análisis"], dependencies=PROTEGIDO,
         response_class=Response, responses={200: {"content": {"application/pdf": {}}}})
def descargar_pdf(analysis_id: str) -> Response:
    """Atajo por extensión.

    Convive con la negociación de contenido porque un `<a href>` de navegador no
    puede mandar el header `Accept`. No es redundancia: es reconocer cómo
    funcionan los clientes reales.
    """
    return _pdf_de(_obtener_o_404(analysis_id))


@app.get("/analyses/{analysis_id}", tags=["análisis"], dependencies=PROTEGIDO,
         responses={200: {"content": {"application/json": {},
                                      "application/pdf": {}}}})
def obtener_analisis(analysis_id: str, request: Request) -> Response:
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


@app.delete("/analyses/{analysis_id}", status_code=204, tags=["análisis"],
            dependencies=PROTEGIDO)
def eliminar_analisis(analysis_id: str) -> Response:
    if not almacen.eliminar(analysis_id):
        raise HTTPException(404, f"no existe el análisis {analysis_id}")
    return Response(status_code=204)
