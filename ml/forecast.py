"""Pronóstico de ventas con versionado y registro en MLflow.

Cada pronóstico deja registrado en MLflow: los parámetros, las métricas del
backtest, la comparación contra el baseline y la versión del modelo. Sin eso,
"el forecast dio 1.470 unidades" es una afirmación irreproducible — dentro de
dos semanas nadie puede decir con qué datos ni con qué código se generó.

**El pronóstico nunca se devuelve solo.** Viaja con su MAPE de backtest y con el
MAPE del baseline, porque un número de predicción sin margen de error se lee
como si fuera un hecho. El modelo `Report` genera una advertencia automática
cuando el modelo pierde contra el baseline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

from core.report import Prediccion
from ml.backtest import ResultadoBacktest, _predecir_recursivo, backtest, entrenar_modelo
from ml.baseline import baseline_naive

VERSION_MODELO = "ridge_lags_v1"
# MLflow deprecó el backend de filesystem ("in maintenance mode"): hay que usar
# una base. SQLite alcanza para un tracking local y no agrega servicios.
BASE_MLFLOW = Path(__file__).resolve().parent.parent / "data" / "mlflow.db"
EXPERIMENTO = "forecast_ventas"

# Último fallo de tracking, consultable para diagnóstico.
_ULTIMO_ERROR_MLFLOW: list[str] = []


def ultimo_error_mlflow() -> str | None:
    return _ULTIMO_ERROR_MLFLOW[-1] if _ULTIMO_ERROR_MLFLOW else None


@dataclass
class ResultadoForecast:
    product_id: str
    horizonte: int
    valor: float
    backtest: ResultadoBacktest
    version: str = VERSION_MODELO
    run_id: str | None = None
    uso_baseline: bool = False

    def a_prediccion(self) -> Prediccion:
        """Convierte al modelo del informe, con su calidad medida al lado."""
        return Prediccion(
            product_id=self.product_id,
            horizonte_dias=self.horizonte,
            valor=round(self.valor, 1),
            mape_backtest=(round(self.backtest.mape_modelo, 1)
                           if self.backtest.mape_modelo is not None else None),
            mape_baseline=(round(self.backtest.mape_baseline, 1)
                           if self.backtest.mape_baseline is not None else None),
            modelo_version=(f"{self.version} (baseline)" if self.uso_baseline
                            else self.version),
        )


def _registrar_en_mlflow(resultado: ResultadoForecast, desde: date,
                         hasta: date, puntos: int) -> str | None:
    """Deja constancia del pronóstico. Si MLflow falla, el forecast sigue.

    El tracking importa pero no es el producto: que el registro de experimentos
    no esté disponible no puede impedir que el usuario reciba su análisis.

    Pero el fallo NO se traga en silencio. La primera versión de esta función
    devolvía `None` sin decir nada, y ocultó durante un diagnóstico entero un
    error que el mensaje de MLflow explicaba con todas las letras. Un `except`
    que descarta la excepción convierte un error en un misterio.
    """
    try:
        import mlflow

        BASE_MLFLOW.parent.mkdir(parents=True, exist_ok=True)
        mlflow.set_tracking_uri(f"sqlite:///{BASE_MLFLOW.as_posix()}")
        mlflow.set_experiment(EXPERIMENTO)

        with mlflow.start_run() as run:
            mlflow.log_params({
                "product_id": resultado.product_id,
                "horizonte_dias": resultado.horizonte,
                "modelo": resultado.version,
                "desde": desde.isoformat(),
                "hasta": hasta.isoformat(),
                "puntos_serie": puntos,
                "ventanas_backtest": resultado.backtest.ventanas,
                "uso_baseline": resultado.uso_baseline,
            })
            metricas = {
                "prediccion": resultado.valor,
                "mae": resultado.backtest.mae_modelo,
                "rmse": resultado.backtest.rmse_modelo,
                "mape": resultado.backtest.mape_modelo,
                "mape_baseline": resultado.backtest.mape_baseline,
                "mape_estacional": resultado.backtest.mape_estacional,
            }
            mlflow.log_metrics({k: v for k, v in metricas.items() if v is not None})
            # La comparación contra el baseline es la métrica que decide si el
            # modelo merece existir. Se registra como tal.
            mlflow.set_tag("supera_al_baseline",
                           str(resultado.backtest.supera_al_baseline))
            return run.info.run_id
    except Exception as e:
        # Se deja rastro del motivo aunque el forecast siga adelante.
        _ULTIMO_ERROR_MLFLOW.append(f"{type(e).__name__}: {str(e)[:200]}")
        return None


def pronosticar(
    product_id: str,
    serie: np.ndarray,
    horizonte: int,
    desde: date,
    hasta: date,
    registrar: bool = True,
) -> ResultadoForecast:
    """Entrena, valida por backtesting y proyecta.

    Si el modelo no supera al baseline, **se devuelve el baseline**. Entregar
    una predicción peor que "repetir el último valor" solo porque salió de un
    modelo entrenado es preferir la apariencia de sofisticación al resultado.
    """
    resultado_bt = backtest(serie, horizonte=horizonte, n_ventanas=3)

    modelo = entrenar_modelo(serie, range(len(serie)))
    if modelo is None:
        valor = float(baseline_naive(serie, 1)[0]) * horizonte
        forecast = ResultadoForecast(
            product_id=product_id, horizonte=horizonte, valor=valor,
            backtest=resultado_bt, uso_baseline=True,
        )
    else:
        proyeccion = _predecir_recursivo(modelo, serie, horizonte, len(serie))
        # El pronóstico es el TOTAL del horizonte, que es lo que se reporta:
        # "1.470 unidades en los próximos 30 días".
        valor = float(np.sum(np.maximum(proyeccion, 0)))

        usa_baseline = (
            resultado_bt.mape_modelo is not None
            and resultado_bt.mape_baseline is not None
            and not resultado_bt.supera_al_baseline
        )
        if usa_baseline:
            valor = float(np.sum(baseline_naive(serie, horizonte)))

        forecast = ResultadoForecast(
            product_id=product_id, horizonte=horizonte, valor=valor,
            backtest=resultado_bt, uso_baseline=usa_baseline,
        )

    if registrar and not os.getenv("SIN_MLFLOW"):
        forecast.run_id = _registrar_en_mlflow(forecast, desde, hasta, len(serie))
    return forecast
