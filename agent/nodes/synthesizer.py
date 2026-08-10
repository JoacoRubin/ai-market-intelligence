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

from agent.llm import ClienteLLM
from agent.state import AnalysisState
from core.conclusiones import _alertas_de_devolucion, _conclusiones
from core.kpis import FUENTE
from core.report import Afirmacion, Fuente, MetricaProducto, Report

MAX_CONCLUSIONES = 5

ESQUEMA = {
    "type": "object",
    "properties": {
        "conclusiones": {
            "type": "array",
            "items": {"type": "string"},
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
4. Escribí entre 2 y 5 conclusiones.
5. Los porcentajes usan coma decimal (31,2%) y los miles, punto (1.243).

Un ejemplo de conclusión correcta:
  "Alfa lidera en unidades con 1.243, frente a las 981 de Beta"

Ejemplos de conclusiones INCORRECTAS:
  "Alfa vendió aproximadamente 1.300 unidades"   (redondeó: inventó un número)
  "Habría que bajar el precio de Beta"           (es una recomendación)
  "Alfa creció un 20%"                           (si el dato dice 18,4%)
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

    try:
        respuesta = cliente.estructurado(
            SISTEMA, _datos_para_el_modelo(metricas), ESQUEMA
        )
        textos = respuesta.get("conclusiones", []) if isinstance(respuesta, dict) else []
        conclusiones = [
            Afirmacion(texto=str(t).strip(), tipo="hecho", fuentes=[FUENTE])
            for t in textos[:MAX_CONCLUSIONES]
            if str(t).strip()
        ]
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
        fuentes=[Fuente(
            id=FUENTE, tipo="sql",
            referencia="dbo.order_items JOIN dbo.orders JOIN dbo.products",
            consultada_en=ahora,
        )],
        resumen_ejecutivo=conclusiones,
        metricas=metricas,
        advertencias=list(estado.advertencias) + _alertas_de_devolucion(metricas),
        trace=list(estado.trace),
        limitaciones=[
            "Los datos son sintéticos y no representan operaciones comerciales reales.",
            "Las conclusiones se derivan de métricas internas: no incluyen "
            "evidencia documental ni contexto de mercado.",
        ],
    )

    return estado
