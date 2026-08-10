"""Verificación semántica de afirmaciones comparativas.

Tercera capa de defensa del informe:

  1. ¿Los números salieron de una herramienta?   -> validator.py
  2. **¿La relación que afirma el texto es cierta?** -> este módulo
  3. ¿Está separado hecho de recomendación?      -> core/report.py

La segunda capa existe por un caso real: el modelo escribió *"lidera en unidades
con 242, frente a las 257"*. Ambos números salían de SQL, así que la primera
capa lo aprobó. La aritmética de la frase era falsa.

**El criterio central es la prudencia.** Un validador que rechaza de más borra
afirmaciones verdaderas y vacía informes correctos, que es tan dañino como
dejar pasar mentiras. Por eso solo se rechaza cuando la contradicción es
inequívoca: un comparativo claro, exactamente dos cifras y una relación que no
se cumple. En cualquier otro caso el veredicto es `NO_APLICA` y la afirmación
sigue su camino.
"""

from __future__ import annotations

import re
from enum import StrEnum

from agent.nodes.numeros import (
    NUMERO,
    REFERENCIAS,
    _a_float,
    extraer_numeros_de_negocio,
)


class Veredicto(StrEnum):
    CORRECTA = "correcta"
    CONTRADICTORIA = "contradictoria"
    NO_APLICA = "no_aplica"


# Comparativos de superioridad: se espera primer número MAYOR que el segundo.
SUPERIORIDAD = re.compile(
    r"\b(lidera|encabeza|supera|superando|super[oó]|"
    r"m[aá]s alt[oa]|m[aá]s grande|mayor|por encima|domina)\b",
    re.IGNORECASE,
)

# Comparativos de inferioridad: se espera primer número MENOR que el segundo.
# Se evalúan PRIMERO porque "más baja" contiene "baja" y "menor" puede
# solaparse con otras formas.
INFERIORIDAD = re.compile(
    r"\b(m[aá]s baj[oa]|m[aá]s chic[oa]|menor|por debajo|inferior|"
    r"menos que|cae por debajo)\b",
    re.IGNORECASE,
)

# Sin un conector de contraste no hay dos magnitudes enfrentadas: "vendió 1.243
# unidades" no compara nada aunque mencione un número.
# Ojo con el límite de palabra final: "frente al 30,1%" no matchea `frente a\b`
# porque la 'l' de "al" continúa la palabra. Por eso `frente a l?…` explícito.
CONTRASTE = re.compile(
    r"\b(frente al?\b|contra\b|versus\b|vs\.?|superando\b|mientras que\b|"
    r"respecto de\b|comparado con\b|en comparaci[oó]n\b)",
    re.IGNORECASE,
)


def _numeros_en_orden(texto: str) -> list[float]:
    """Cifras de negocio en su orden de aparición.

    Se limpian antes los identificadores (`P002`, `doc_112`, `§3.2`): contarlos
    inflaría la cantidad de números y haría que la afirmación quedara sin
    evaluar. Es el mismo falso positivo que tenía el auditor prototipo.
    """
    limpio = REFERENCIAS.sub(" ", texto)
    valores = [_a_float(m.group()) for m in NUMERO.finditer(limpio)]
    return [v for v in valores if v is not None]


def verificar_comparacion(texto: str) -> Veredicto:
    """Determina si una afirmación comparativa es aritméticamente coherente."""
    if not texto or not texto.strip():
        return Veredicto.NO_APLICA

    if not CONTRASTE.search(texto):
        return Veredicto.NO_APLICA

    # La inferioridad se chequea primero: "más baja" también contendría
    # coincidencias parciales con otras formas.
    m_menor = INFERIORIDAD.search(texto)
    m_mayor = SUPERIORIDAD.search(texto)
    comparativo = m_menor or m_mayor
    if comparativo is None:
        return Veredicto.NO_APLICA
    espera_menor = m_menor is not None

    # El comparativo tiene que aparecer ANTES del primer número para que ese
    # número sea el del sujeto que compara. Si aparece en el medio, el sujeto
    # puede ser el otro:
    #
    #   "Alfa crece 18,4% mientras que Beta queda POR DEBAJO con -3,1%"
    #
    # ahí quien queda por debajo es Beta, con la segunda cifra. Determinar eso
    # requiere análisis sintáctico; asumir lo contrario produciría un falso
    # positivo, y un falso positivo borra una afirmación verdadera del informe.
    primer_numero = NUMERO.search(REFERENCIAS.sub(" ", texto))
    if primer_numero and comparativo.start() > primer_numero.start():
        return Veredicto.NO_APLICA

    numeros = _numeros_en_orden(texto)
    if len(numeros) != 2:
        # Con una sola cifra no hay comparación; con tres o más no se sabe
        # cuáles se enfrentan. Adivinar produciría falsos positivos, y un falso
        # positivo borra una afirmación verdadera.
        return Veredicto.NO_APLICA

    primero, segundo = numeros
    if primero == segundo:
        # Discutible, pero no es una contradicción aritmética.
        return Veredicto.NO_APLICA

    cumple = primero < segundo if espera_menor else primero > segundo
    return Veredicto.CORRECTA if cumple else Veredicto.CONTRADICTORIA


def explicar(texto: str) -> str:
    """Mensaje para las advertencias del informe."""
    numeros = _numeros_en_orden(texto)
    detalle = f" ({numeros[0]:g} vs {numeros[1]:g})" if len(numeros) == 2 else ""
    return (
        f"{texto!r} — la comparación es aritméticamente falsa{detalle}: los "
        "números son correctos pero la relación que afirma el texto no se cumple."
    )


__all__ = [
    "Veredicto",
    "explicar",
    "extraer_numeros_de_negocio",
    "verificar_comparacion",
]
