"""Métricas de error de pronóstico.

Las tres miden cosas distintas y por eso se reportan las tres:

- **MAE**: error promedio en unidades. Se interpreta directo — "se equivoca por
  12 unidades" — pero no se puede comparar entre productos de escalas distintas.
- **RMSE**: penaliza más los errores grandes. Un error de 10 pesa más que diez
  errores de 1. Importa cuando equivocarse mucho una vez es peor que
  equivocarse poco muchas veces, que en stock suele ser el caso.
- **MAPE**: error porcentual. Es el único comparable entre productos, y por eso
  es el que va al informe.

**MAPE tiene una trampa**: si el valor real es cero, el denominador es cero. Un
producto sin ventas un día hace explotar la métrica a infinito y contamina el
promedio de toda la ventana. Acá esos puntos se excluyen, y si no queda ninguno
la función devuelve `None` en vez de un número inventado.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def _validar(reales: Sequence[float], predichos: Sequence[float]) -> tuple:
    r, p = np.asarray(reales, dtype=float), np.asarray(predichos, dtype=float)
    if r.shape != p.shape:
        raise ValueError(
            f"las series tienen longitudes distintas: {r.shape} vs {p.shape}"
        )
    if r.size == 0:
        raise ValueError("no hay valores para comparar")
    return r, p


def mae(reales: Sequence[float], predichos: Sequence[float]) -> float:
    """Error absoluto medio, en las unidades de la serie."""
    r, p = _validar(reales, predichos)
    return float(np.mean(np.abs(r - p)))


def rmse(reales: Sequence[float], predichos: Sequence[float]) -> float:
    """Raíz del error cuadrático medio. Castiga los desvíos grandes."""
    r, p = _validar(reales, predichos)
    return float(np.sqrt(np.mean((r - p) ** 2)))


def mape(reales: Sequence[float], predichos: Sequence[float]) -> float | None:
    """Error porcentual absoluto medio.

    Excluye los puntos donde el valor real es cero: no existe error porcentual
    sobre cero. Si todos lo son, devuelve `None` — decir "0% de error" ahí
    afirmaría una precisión perfecta que nadie midió.
    """
    r, p = _validar(reales, predichos)
    mascara = r != 0
    if not mascara.any():
        return None
    return float(np.mean(np.abs((r[mascara] - p[mascara]) / r[mascara])) * 100)
