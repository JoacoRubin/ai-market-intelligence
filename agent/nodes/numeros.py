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
# Todo esto se borra del texto ANTES de buscar cifras: contiene dígitos que no
# son magnitudes de negocio.
#
# La regla general es la última: **una letra pegada a dígitos es un
# identificador**, no una cantidad. Se llegó a ella después de parchear tres
# veces el mismo bug con prefijos distintos —`doc_112`, `§3.2`, `L1829`—. Cuando
# el mismo error vuelve con otra cara, el patrón está mal planteado: no faltaba
# un caso más, faltaba la regla.
REFERENCIAS = re.compile(
    r"""
      doc_\d+                    # identificadores de documento
    | §\s*\d+(?:\.\d+)*          # números de sección
    | \b\w+_v\d+\b               # versiones de modelo (sales_v3)
    | \b[a-z]+\d+(?:\.\d+)*:\S+  # nombres de modelo (llama3.2:3b)
    | \bsql:\S+ | \bml:\S+       # identificadores de fuente
    | \b[A-Za-z]+\d+[A-Za-z\d]*  # P002, L1829, REF7788: letra + dígitos
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
    """Interpreta un número escrito en formato español O inglés.

    Soportar los dos no es capricho: el modelo mezcla convenciones. Escribió
    `62,461.52` (inglés) y el parser, que asumía español, lo leyó como 62,46 —
    con lo cual el validador descartó una afirmación CORRECTA por no saber leer
    el número. Un falso positivo del validador borra información buena, que es
    justamente lo que no puede pasar.

    Regla cuando aparecen los dos separadores: **el último es el decimal**.

        1.234,56  -> la coma va última  -> español -> 1234.56
        1,234.56  -> el punto va último -> inglés  -> 1234.56

    Con un solo separador hay ambigüedad real (`1,243` puede ser mil doscientos
    cuarenta y tres o uno coma doscientos cuarenta y tres). Se resuelve por la
    forma: exactamente tres dígitos después del separador y ninguno más se lee
    como agrupación de miles.
    """
    token = token.strip(".,")
    if not token or not any(c.isdigit() for c in token):
        return None
    negativo = token.startswith("-")
    token = token.lstrip("-")

    tiene_punto, tiene_coma = "." in token, "," in token
    try:
        if tiene_punto and tiene_coma:
            decimal = "," if token.rfind(",") > token.rfind(".") else "."
            miles = "." if decimal == "," else ","
            valor = float(token.replace(miles, "").replace(decimal, "."))
        elif tiene_punto or tiene_coma:
            sep = "." if tiene_punto else ","
            partes = token.split(sep)
            # Agrupación de miles: todos los grupos posteriores al primero
            # tienen exactamente tres dígitos (1.243 · 1,234,567).
            if len(partes) > 1 and all(len(p) == 3 for p in partes[1:]):
                valor = float("".join(partes))
            else:
                valor = float(token.replace(sep, "."))
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
