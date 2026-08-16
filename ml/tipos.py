"""Alias de tipos compartidos del módulo de ML.

Existe por un desajuste concreto: las funciones de series declaraban
`Sequence[float]` pero su primera línea es `np.asarray(serie, dtype=float)`, y
casi todos los llamadores les pasan un `ndarray` — que **no** es un
`Sequence[float]` para el verificador de tipos.

La firma decía menos de lo que la función acepta. Se ensancha la firma en vez de
convertir en cada llamada, porque el `asarray` interno ya hace esa conversión y
duplicarla afuera sería trabajo y ruido para el mismo resultado.

No se usa `numpy.typing.ArrayLike` a propósito: acepta también escalares, y
`mape(5.0, 3.0)` no debería type-checkear. Lo que estas funciones reciben es una
serie, no un número.
"""

from collections.abc import Sequence
from typing import Any

import numpy.typing as npt

type SerieNumerica = Sequence[float] | npt.NDArray[Any]
