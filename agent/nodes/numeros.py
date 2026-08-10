"""Extracción de magnitudes de negocio a partir de texto.

Vive aparte porque lo usan tanto el validador de groundedness como el
verificador de comparaciones, y tenerlo dentro de uno de los dos creaba un
ciclo de imports.

La limpieza previa de referencias es lo que evita el falso positivo que tenía el
auditor prototipo: `doc_112` aportaba un 112 que nunca fue un dato, y eso
inflaba la métrica de groundedness cerca del doble.
"""

from __future__ import annotations

import re

# Patrones que se eliminan del texto ANTES de buscar números, porque contienen
# dígitos que no son magnitudes de negocio.
REFERENCIAS = re.compile(
    r"""
      doc_\d+                  # identificadores de documento
    | §\s*\d+(?:\.\d+)*        # números de sección
    | \bP\d{1,6}\b             # identificadores de producto
    | \b\w+_v\d+\b             # versiones de modelo (sales_v3)
    | \b[a-z]+\d+(?:\.\d+)*:\S+  # nombres de modelo (llama3.2:3b)
    | \bsql:\S+ | \bml:\S+     # identificadores de fuente
    """,
    re.VERBOSE | re.IGNORECASE,
)

NUMERO = re.compile(r"-?\d[\d.,]*")

# Tolerancia relativa para diferencias de redondeo: el modelo puede escribir
# 31,2% donde la métrica dice 31,23%. Eso es redondeo, no invención, y
# rechazarlo vaciaría informes correctos.
TOLERANCIA_RELATIVA = 0.02
TOLERANCIA_ABSOLUTA = 0.5


def _a_float(token: str) -> float | None:
    """Interpreta un número en formato español (1.243 -> 1243 ; 31,2 -> 31.2)."""
    token = token.strip(".,")
    if not token or not any(c.isdigit() for c in token):
        return None
    negativo = token.startswith("-")
    token = token.lstrip("-")
    try:
        if "," in token:
            valor = float(token.replace(".", "").replace(",", "."))
        else:
            partes = token.split(".")
            if len(partes) > 1 and all(len(p) == 3 for p in partes[1:]):
                valor = float("".join(partes))
            else:
                valor = float(token)
    except ValueError:
        return None
    return -valor if negativo else valor


def extraer_numeros_de_negocio(texto: str) -> set[float]:
    """Devuelve las magnitudes del texto, ignorando referencias e identificadores.

    Limpiar primero y extraer después es lo que evita el falso positivo que
    tenía el prototipo: `doc_112` aportaba un 112 que nunca fue un dato.
    """
    limpio = REFERENCIAS.sub(" ", texto)
    valores = {_a_float(m.group()) for m in NUMERO.finditer(limpio)}
    return {v for v in valores if v is not None}
