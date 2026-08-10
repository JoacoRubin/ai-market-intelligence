"""Baselines: el competidor que el modelo tiene que vencer.

Un MAPE de 8% no dice nada por sí solo. Si repetir el último valor conocido da
6%, el modelo está de más: cuesta entrenarlo, versionarlo, monitorearlo y
mantenerlo, para empeorar el resultado.

Por eso ningún pronóstico de este sistema se reporta sin su baseline al lado, y
por eso el modelo `Report` genera una advertencia automática cuando el modelo
pierde. Presentar como predicción algo peor que "repetí el último valor" es
engañoso aunque los números estén bien calculados.

Los baselines no se entrenan. Ese es exactamente su valor: costo cero, sin
riesgo de degradación y sin nada que pueda romperse.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def baseline_naive(serie: Sequence[float], horizonte: int) -> np.ndarray:
    """Repite el último valor observado. El competidor mínimo."""
    s = np.asarray(serie, dtype=float)
    if s.size == 0:
        raise ValueError("no se puede proyectar desde una serie vacía")
    return np.full(horizonte, s[-1], dtype=float)


def baseline_media_movil(
    serie: Sequence[float], horizonte: int, ventana: int = 7
) -> np.ndarray:
    """Repite el promedio de los últimos `ventana` valores.

    Más robusto que el naïve frente a un último día atípico, que es
    justamente el caso en que el naïve se equivoca feo.
    """
    s = np.asarray(serie, dtype=float)
    if s.size == 0:
        raise ValueError("no se puede proyectar desde una serie vacía")
    return np.full(horizonte, float(np.mean(s[-min(ventana, s.size):])), dtype=float)


def baseline_estacional(
    serie: Sequence[float], horizonte: int, periodo: int = 7
) -> np.ndarray:
    """Repite el mismo día de la semana anterior.

    Sobre series con estacionalidad semanal marcada —como las ventas de
    retail— suele ser un rival mucho más duro que el naïve, y por eso conviene
    medirlo: un modelo que solo le gana al naïve puede no estar aportando nada.
    """
    s = np.asarray(serie, dtype=float)
    if s.size == 0:
        raise ValueError("no se puede proyectar desde una serie vacía")
    if s.size < periodo:
        return baseline_naive(s, horizonte)
    ciclo = s[-periodo:]
    return np.array([ciclo[i % periodo] for i in range(horizonte)], dtype=float)
