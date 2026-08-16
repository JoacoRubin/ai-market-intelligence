"""Backtesting temporal walk-forward.

**El punto entero de este módulo es no hacer trampa.**

En una serie temporal, un `train_test_split` aleatorio entrena con datos de
junio para predecir marzo. Se llama *data leakage temporal*, y su peligro es que
no falla ruidosamente: falla dando resultados demasiado buenos. El modelo
muestra un MAPE de 3%, va a producción, y ahí se descubre que el 3% dependía de
haber visto el futuro.

Walk-forward reproduce la única condición que importa: **en cada corte, el
modelo solo conoce el pasado**. Entrena hasta el día N, predice N+1..N+h, se
compara contra lo que realmente pasó, y avanza. Es lo que ocurriría en
producción, medido antes de llegar a producción.

Cada ventana evalúa además los baselines. Un modelo que no le gana a "repetir el
último valor" no justifica su costo de entrenamiento ni de mantenimiento.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ml.baseline import baseline_estacional, baseline_naive
from ml.metricas import mae, mape, rmse
from ml.tipos import SerieNumerica

MIN_ENTRENAMIENTO = 60


def ventanas_walk_forward(
    n: int, tamano_test: int, n_ventanas: int, minimo_train: int = MIN_ENTRENAMIENTO
) -> list[tuple[list[int], list[int]]]:
    """Genera los cortes temporales del backtest.

    Cada corte devuelve (índices de entrenamiento, índices de prueba) con la
    garantía de que **todo índice de entrenamiento es anterior a todo índice de
    prueba**. Esa garantía es la razón de ser de la función.

    Devuelve lista vacía si la serie no alcanza: antes que inventar un backtest
    sobre datos insuficientes, no hacerlo. Un backtest de una ventana chica no
    mide nada y da una confianza que no corresponde.
    """
    ventanas: list[tuple[list[int], list[int]]] = []
    for i in range(n_ventanas, 0, -1):
        fin_train = n - i * tamano_test
        if fin_train < minimo_train:
            continue
        ventanas.append((
            list(range(fin_train)),
            list(range(fin_train, fin_train + tamano_test)),
        ))
    return ventanas


def construir_features(serie: np.ndarray, indices: Sequence[int]) -> np.ndarray:
    """Features temporales para cada punto: solo con información pasada.

    Cada fila usa exclusivamente valores anteriores al punto que describe. Si
    una feature mirara el presente o el futuro, el leakage volvería por la
    ventana de atrás aunque el split temporal fuera correcto.
    """
    filas = []
    for i in indices:
        lag_1 = serie[i - 1] if i >= 1 else serie[0]
        lag_7 = serie[i - 7] if i >= 7 else lag_1
        media_7 = np.mean(serie[max(0, i - 7):i]) if i > 0 else serie[0]
        media_28 = np.mean(serie[max(0, i - 28):i]) if i > 0 else serie[0]
        filas.append([
            lag_1, lag_7, media_7, media_28,
            i % 7,          # día de la semana
            i,              # tendencia
        ])
    return np.asarray(filas, dtype=float)


def _predecir_recursivo(modelo: Any, historia: np.ndarray, horizonte: int,
                        inicio: int) -> np.ndarray:
    """Proyecta paso a paso, realimentando las propias predicciones.

    Es lo que pasa en producción: para predecir el día N+2 no se dispone del
    valor real de N+1, solo de lo que el modelo predijo. Usar los valores reales
    acá inflaría las métricas y volvería a ser leakage, más sutil.
    """
    serie = list(historia)
    salida = []
    for paso in range(horizonte):
        idx = inicio + paso
        features = construir_features(np.asarray([*serie, 0.0]), [len(serie)])
        features[0, -1] = idx
        pred = float(modelo.predict(features)[0])
        salida.append(pred)
        serie.append(pred)
    return np.asarray(salida)


@dataclass
class ResultadoBacktest:
    ventanas: int = 0
    mae_modelo: float | None = None
    rmse_modelo: float | None = None
    mape_modelo: float | None = None
    mape_baseline: float | None = None
    mape_estacional: float | None = None
    motivo: str = ""
    detalle: list[dict[str, Any]] = field(default_factory=list)

    @property
    def supera_al_baseline(self) -> bool:
        if self.mape_modelo is None or self.mape_baseline is None:
            return False
        return self.mape_modelo < self.mape_baseline


def entrenar_modelo(serie: np.ndarray, indices: Sequence[int]) -> Any:
    """Entrena el modelo sobre los índices dados.

    Ridge y no algo más sofisticado: con series cortas y features simples, un
    modelo lineal regularizado es difícil de superar y mucho más fácil de
    explicar. La complejidad se agrega cuando el baseline la exige, no antes.
    """
    from sklearn.linear_model import Ridge

    utiles = [i for i in indices if i >= 7]
    if len(utiles) < 20:
        return None
    X = construir_features(serie, utiles)
    y = serie[utiles]
    modelo = Ridge(alpha=1.0)
    modelo.fit(X, y)
    return modelo


def backtest(
    serie: SerieNumerica,
    horizonte: int = 14,
    n_ventanas: int = 3,
) -> ResultadoBacktest:
    """Evalúa el modelo contra los baselines con validación temporal."""
    s = np.asarray(serie, dtype=float)
    ventanas = ventanas_walk_forward(len(s), horizonte, n_ventanas)

    if not ventanas:
        return ResultadoBacktest(
            motivo=(
                f"histórico insuficiente: {len(s)} días para un horizonte de "
                f"{horizonte} y {n_ventanas} ventanas de validación"
            )
        )

    reales: list[float] = []
    pred_modelo: list[float] = []
    pred_naive: list[float] = []
    pred_estacional: list[float] = []
    detalle: list[dict[str, Any]] = []

    for train, test in ventanas:
        modelo = entrenar_modelo(s, train)
        if modelo is None:
            continue

        historia = s[:len(train)]
        p_modelo = _predecir_recursivo(modelo, historia, horizonte, len(train))
        p_naive = baseline_naive(historia, horizonte)
        p_estacional = baseline_estacional(historia, horizonte)
        y = s[test]

        reales.extend(y)
        pred_modelo.extend(p_modelo)
        pred_naive.extend(p_naive)
        pred_estacional.extend(p_estacional)
        detalle.append({
            "desde": test[0], "hasta": test[-1],
            "mape_modelo": mape(y, p_modelo),
            "mape_naive": mape(y, p_naive),
        })

    if not reales:
        return ResultadoBacktest(motivo="no se pudo entrenar en ninguna ventana")

    return ResultadoBacktest(
        ventanas=len(detalle),
        mae_modelo=mae(reales, pred_modelo),
        rmse_modelo=rmse(reales, pred_modelo),
        mape_modelo=mape(reales, pred_modelo),
        mape_baseline=mape(reales, pred_naive),
        mape_estacional=mape(reales, pred_estacional),
        detalle=detalle,
    )
