"""Tool `forecast_sales`: proyección de ventas con su error medido.

La regla que gobierna esta herramienta: **el pronóstico nunca sale solo**.
Siempre viaja con el MAPE de backtest y con el MAPE del baseline.

Un número de predicción sin margen de error se lee como un hecho. "Va a vender
1.470 unidades" y "vendió 1.243 unidades" se ven igual en un informe, y son
cosas completamente distintas. El modelo `Report` marca las predicciones como
tales y advierte automáticamente cuando el modelo pierde contra el baseline.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator

from agent.tools.registry import ToolName
from core.report import Prediccion
from ml.forecast import pronosticar
from ml.series import serie_diaria

if TYPE_CHECKING:
    from agent.state import AnalysisState

NOMBRE = ToolName.FORECAST_SALES
MAX_HORIZONTE = 90
MIN_HISTORICO = 60


class EntradaForecastSales(BaseModel):
    """Argumentos válidos de la herramienta."""

    product_id: str = Field(description="Identificador del producto (P001).")
    horizonte_dias: int = Field(
        default=30, ge=1, le=MAX_HORIZONTE,
        description="Días a proyectar hacia adelante.",
    )

    @field_validator("product_id")
    @classmethod
    def _formato(cls, v: str) -> str:
        if not re.fullmatch(r"P\d{1,6}", v):
            raise ValueError(
                f"identificador inválido: {v!r}. Se espera P seguido de dígitos."
            )
        return v


def esquema_para_llm() -> dict[str, Any]:
    esquema = EntradaForecastSales.model_json_schema()
    return {
        "type": "function",
        "function": {
            "name": NOMBRE,
            "description": (
                "Proyecta las unidades que un producto va a vender en los "
                "próximos días, con el error del modelo medido por backtesting "
                "y comparado contra un baseline. Usar cuando la consulta pida "
                "proyección, pronóstico o estimación a futuro."
            ),
            "parameters": {
                "type": "object",
                "properties": esquema["properties"],
                "required": esquema.get("required", []),
            },
        },
    }


def ejecutar_forecast_sales(
    entrada: EntradaForecastSales, estado: AnalysisState
) -> list[Prediccion]:
    """Proyecta la demanda del producto y registra la corrida en MLflow."""
    if not estado.puede_llamar_tool():
        estado.registrar_llamada_tool()
        return []

    estado.registrar_llamada_tool()
    inicio = time.perf_counter()

    periodo = estado.periodo
    if periodo is None:
        estado._advertir("No hay período definido: no se puede proyectar.")
        return []

    # El histórico se toma más largo que el período analizado: entrenar sobre
    # 30 días no alcanza para aprender una estacionalidad semanal, y menos para
    # validarla con backtesting.
    from datetime import timedelta

    desde_hist = periodo.hasta - timedelta(days=365)
    _, serie = serie_diaria(entrada.product_id, desde_hist, periodo.hasta)

    if len(serie) < MIN_HISTORICO or serie.sum() == 0:
        estado._advertir(
            f"El histórico de {entrada.product_id} es insuficiente para "
            "proyectar con validación: no se generó pronóstico."
        )
        estado.registrar_paso(
            "ml_tool", int((time.perf_counter() - inicio) * 1000), tool=NOMBRE)
        return []

    resultado = pronosticar(
        entrada.product_id, serie, entrada.horizonte_dias,
        desde_hist, periodo.hasta,
    )
    prediccion = resultado.a_prediccion()

    if resultado.uso_baseline:
        # No se oculta: el informe tiene que decir que la "predicción" es en
        # realidad el baseline, porque el modelo no logró superarlo.
        estado._advertir(
            f"El modelo de forecast no superó al baseline para "
            f"{entrada.product_id}: se reporta la proyección del baseline."
        )

    estado.registrar_paso(
        "ml_tool", int((time.perf_counter() - inicio) * 1000), tool=NOMBRE)

    # Se ACUMULA, no se sobrescribe: el plan puede pedir un pronóstico por
    # producto, y guardar solo el último dejaba la mitad de las predicciones
    # fuera del informe sin que nada lo indicara.
    previas = estado.resultados_tools.get(NOMBRE) or []
    estado.registrar_resultado(NOMBRE, [*previas, prediccion])
    return [prediccion]
