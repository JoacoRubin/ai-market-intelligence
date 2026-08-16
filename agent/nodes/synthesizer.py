"""Nodo Synthesizer: el único que le pide al modelo que escriba.

Redactar es lo único que un LLM hace mejor que el software, así que es lo único
que se le pide. No calcula, no decide qué consultar, no elige herramientas: le
llegan los KPIs ya calculados y produce prosa.

**El respaldo determinístico es la pieza clave.** Si el modelo falla, tarda de
más o devuelve algo inservible, el nodo cae en `core.conclusiones`, que genera
un resumen más seco pero correcto, derivado de los mismos números.

Eso convierte al modelo en una mejora y no en una dependencia. El sistema sin
LLM sigue produciendo un informe válido; con LLM produce uno mejor redactado. Un
sistema que sin el modelo no produce nada es un sistema que depende del modelo
para tener razón.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from agent.llm import ClienteLLM
from agent.state import AnalysisState
from core.conclusiones import _alertas_de_devolucion, _conclusiones
from core.kpis import FUENTE
from core.report import Afirmacion, Fuente, MetricaProducto, Prediccion, Report

MAX_CONCLUSIONES = 5

ESQUEMA = {
    "type": "object",
    "properties": {
        "conclusiones": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "texto": {"type": "string"},
                    # El modelo declara de qué documento salió cada afirmación.
                    # Una cita que no se puede abrir es decoración: parece rigor
                    # y no lo es.
                    "fuente": {"type": "string"},
                },
                "required": ["texto"],
            },
        }
    },
    "required": ["conclusiones"],
}

SISTEMA = """\
Sos un analista comercial. Recibís métricas ya calculadas y escribís
conclusiones ejecutivas. Devolvés SOLO JSON.

REGLAS INNEGOCIABLES:
1. NO inventes ningún número. Usá SOLO los valores que aparecen en los datos.
2. NO calcules nada: si un número no está en los datos, no existe.
3. Cada conclusión es una oración corta y afirmativa sobre lo que muestran los
   datos, no una recomendación de qué hacer.
4. Escribí entre 3 y 5 conclusiones. La regla 6 y la regla 7 se cumplen en
   conclusiones DISTINTAS: una dice cuánto, otra dice por qué. No alcanza con
   una sola que intente las dos cosas.
5. Los porcentajes usan coma decimal (31,2%) y los miles, punto (1.243).

6. AL MENOS UNA conclusión tiene que decir las UNIDADES y el REVENUE tal como
   figuran en los datos. Un porcentaje sin la cantidad de la que sale no se
   puede verificar: "creció 18,4%" puede ser de 10 a 12 unidades o de 10.000 a
   11.840, y son dos historias distintas.

7. Si el bloque incluye "Evidencia documental disponible", AL MENOS UNA de tus
   conclusiones tiene que explicar POR QUÉ pasó lo que muestran los números,
   usando esa evidencia, y poner el identificador del documento en "fuente"
   (por ejemplo "doc_prov_XXX").
8. La regla 7 vale IGUAL cuando el número mejoró. Una suba de ventas también
   tiene una causa, y suele estar en un documento de campaña o promoción.
9. Las conclusiones que salen de las métricas llevan "fuente" vacío.
10. NUNCA inventes un identificador de documento: usá solo los que aparecen
    entre corchetes en la evidencia.

Los números dicen QUÉ pasó. Los documentos dicen POR QUÉ. Un informe que solo
repite métricas no es un análisis.

Ejemplos de conclusiones correctas:
  {"texto": "Alfa lidera en unidades con 1.243, frente a las 981 de Beta", "fuente": ""}
  {"texto": "Alfa facturó USD 87.010 con 1.243 unidades vendidas", "fuente": ""}
  {"texto": "El proveedor reportó defectos de costura en el lote", "fuente": "doc_prov_XXX"}
  {"texto": "La campaña de descuento del 20% explica el salto de unidades",
   "fuente": "doc_promo_XXX"}

Ejemplos de conclusiones INCORRECTAS:
  "Alfa vendió aproximadamente 1.300 unidades"   (redondeó: inventó un número)
  "Habría que bajar el precio de Beta"           (es una recomendación)
  "Alfa creció un 20%"                           (si el dato dice 18,4%)
  "Las ventas subieron 31,2% en el período"      (porcentaje sin la magnitud
                                                  absoluta que lo respalda)
"""


def _datos_para_el_modelo(metricas: list[MetricaProducto]) -> str:
    lineas = []
    for m in metricas:
        partes = [
            f"producto: {m.nombre} ({m.product_id})",
            f"unidades: {m.unidades}",
            f"revenue: {m.revenue:.2f}",
        ]
        if m.margen_pct is not None:
            partes.append(f"margen: {m.margen_pct:.1f}%")
        if m.crecimiento_pct is not None:
            partes.append(f"crecimiento: {m.crecimiento_pct:.1f}%")
        if m.tasa_devolucion_pct is not None:
            partes.append(f"devoluciones: {m.tasa_devolucion_pct:.1f}%")
        lineas.append(" | ".join(partes))
    return "\n".join(lineas)


def _evidencia_para_el_modelo(evidencia: list[dict[str, Any]]) -> str:
    """Pasajes recuperados, cada uno con su identificador a la vista.

    El identificador va pegado al texto para que el modelo pueda citarlo sin
    inventarlo. Un prompt que muestra el contenido sin la referencia obliga al
    modelo a recordar de dónde salió, y ahí es donde se equivoca.
    """
    if not evidencia:
        return ""
    return "\n\n".join(
        f"[{e['doc_id']} {e['seccion']} - {e['fecha']}] {e['texto']}"
        for e in evidencia
    )


def _fuentes_de_modelo(predicciones: list[Prediccion], ahora: datetime) -> list[Fuente]:
    """Declara el modelo de ML como fuente citable.

    Una predicción es tan rastreable como un dato: tiene que poder decirse qué
    modelo y qué versión la produjo.
    """
    vistos: dict[str, Fuente] = {}
    for p in predicciones:
        clave = f"ml:{p.modelo_version or 'desconocido'}"
        if clave not in vistos:
            vistos[clave] = Fuente(
                id=clave, tipo="modelo_ml",
                referencia=f"forecast_sales · {p.modelo_version}",
                consultada_en=ahora,
            )
    return list(vistos.values())


def _fuentes_documentales(
    evidencia: list[dict[str, Any]], ahora: datetime
) -> list[Fuente]:
    """Declara cada documento recuperado como fuente citable del informe.

    El modelo `Report` rechaza toda cita a una fuente no declarada, así que esto
    es lo que hace válidas las citas del texto — y lo que garantiza que el
    lector pueda rastrear cada una.
    """
    vistos: dict[str, Fuente] = {}
    for e in evidencia:
        if e["doc_id"] not in vistos:
            vistos[e["doc_id"]] = Fuente(
                id=e["doc_id"], tipo="documento", referencia=e["titulo"],
                seccion=e["seccion"], consultada_en=ahora,
            )
    return list(vistos.values())


def _predicciones_del_estado(estado: AnalysisState) -> list[Prediccion]:
    crudas = estado.resultados_tools.get("forecast_sales", [])
    return [p for p in (crudas or []) if isinstance(p, Prediccion)]


def _metricas_del_estado(estado: AnalysisState) -> list[MetricaProducto]:
    resultado = estado.resultados_tools.get("product_metrics", {})
    valores = resultado.values() if isinstance(resultado, dict) else resultado
    return [m for m in (valores or []) if isinstance(m, MetricaProducto)]


def sintetizar(
    estado: AnalysisState,
    cliente: ClienteLLM,
    ahora: datetime | None = None,
) -> AnalysisState:
    """Redacta el informe a partir de los resultados de las herramientas."""
    inicio = time.perf_counter()
    ahora = ahora or datetime.now()
    metricas = _metricas_del_estado(estado)

    if not metricas:
        # Sin datos no hay informe. Dejar que el modelo redacte igual es
        # exactamente cómo se producen los informes inventados.
        estado._advertir(
            f"No se obtuvieron métricas tras {estado.reintentos + 1} "
            "intento(s): no hay información suficiente para generar un informe."
        )
        estado.registrar_paso("synthesizer",
                              int((time.perf_counter() - inicio) * 1000))
        return estado

    conclusiones: list[Afirmacion] = []
    modelo_usado = cliente.nombre

    docs_validos = {e["doc_id"] for e in estado.evidencia}
    bloque = _datos_para_el_modelo(metricas)
    if estado.evidencia:
        bloque += ("\n\nEvidencia documental disponible:\n"
                   + _evidencia_para_el_modelo(estado.evidencia))

    try:
        respuesta = cliente.estructurado(SISTEMA, bloque, ESQUEMA)
        crudas = respuesta.get("conclusiones", []) if isinstance(respuesta, dict) else []
        for c in crudas[:MAX_CONCLUSIONES]:
            if isinstance(c, dict):
                texto = str(c.get("texto", "")).strip()
                fuente = str(c.get("fuente", "")).strip()
            else:
                texto, fuente = str(c).strip(), ""
            if not texto:
                continue
            # El modelo suele copiar el encabezado entero del pasaje
            # ("doc_prov_011 §1.1 - 2026-03-12") en vez del identificador solo.
            # Rescatar el id de ahí adentro recupera una cita válida que si no
            # se descartaría por un problema de formato, no de veracidad.
            if fuente and fuente not in docs_validos:
                fuente = next(
                    (d for d in docs_validos if d in fuente), fuente
                )

            # Citar un documento que no se recuperó es una alucinación de
            # referencia: se descarta la cita, no la afirmación.
            if fuente and fuente not in docs_validos:
                estado._advertir(
                    f"El modelo citó un documento inexistente ({fuente}). La "
                    "afirmación se conserva atribuida a los datos."
                )
                fuente = ""
            conclusiones.append(Afirmacion(
                texto=texto, tipo="hecho",
                fuentes=[fuente] if fuente else [FUENTE],
            ))
    except Exception as e:
        estado.error = f"{type(e).__name__}: {e}"

    if not conclusiones:
        # Respaldo determinístico: más seco, pero correcto. El informe sale igual.
        conclusiones = _conclusiones(metricas)
        modelo_usado = f"{cliente.nombre} (respaldo determinístico)"
        estado._advertir(
            "El modelo de lenguaje no produjo conclusiones utilizables. Se "
            "generaron a partir de los datos con reglas determinísticas."
        )

    # El paso se registra ANTES de construir el informe: el informe copia el
    # trace, y hacerlo después dejaba la etapa de síntesis fuera de "Cómo se
    # obtuvo" — justo la más lenta, que es la que el lector quiere ver.
    estado.registrar_paso("synthesizer", int((time.perf_counter() - inicio) * 1000))

    estado.informe = Report(
        request_id=estado.request_id,
        consulta=estado.consulta,
        generado_en=ahora,
        modelo_llm=modelo_usado,
        fuentes=[
            Fuente(
                id=FUENTE, tipo="sql",
                referencia="dbo.order_items JOIN dbo.orders JOIN dbo.products",
                consultada_en=ahora,
            ),
            *_fuentes_documentales(estado.evidencia, ahora),
            *_fuentes_de_modelo(_predicciones_del_estado(estado), ahora),
        ],
        resumen_ejecutivo=conclusiones,
        metricas=metricas,
        predicciones=_predicciones_del_estado(estado),
        advertencias=list(estado.advertencias) + _alertas_de_devolucion(metricas),
        trace=list(estado.trace),
        limitaciones=[
            "Los datos son sintéticos y no representan operaciones comerciales reales.",
            "Las conclusiones se derivan de métricas internas: no incluyen "
            "evidencia documental ni contexto de mercado.",
        ],
    )

    return estado
