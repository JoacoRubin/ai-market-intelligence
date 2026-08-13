"""Extracción determinística de entidades y corrección de intención.

Este módulo existe por un hallazgo concreto del diagnóstico del caso hyb-02.

**El problema.** El router le pedía al modelo dos cosas en una sola llamada:
clasificar la intención y extraer los identificadores de producto. Cuando la
clasificación fallaba, la extracción fallaba con ella:

    company_research    ->  product_ids = []      SIEMPRE
    product_performance ->  product_ids = ['P010']
    hybrid              ->  product_ids = ['P010']

El identificador estaba escrito en la consulta, y el modelo lo ignoraba porque
ya había decidido que la pregunta era sobre empresas. Un error contaminaba al
otro por viajar juntos.

**La solución.** Un identificador de producto es un patrón `P` + dígitos. Un
regex lo extrae perfecto, gratis y sin equivocarse jamás. Pedírselo a un modelo
probabilístico que corre a 8 tokens por segundo era delegar al LLM algo que el
software clásico hace mejor — exactamente lo que la regla de oro del proyecto
prohíbe, y que sin embargo se coló en el primer nodo escrito.

El modelo queda con una sola tarea: clasificar. La extracción es imposible de
fallar, y además se puede usar para **verificar** la clasificación: una consulta
etiquetada `company_research` que nombra productos del catálogo interno es una
contradicción que el software detecta y el modelo no.
"""

from __future__ import annotations

import calendar
import re
from datetime import date

from agent.state import Intencion, Periodo

# \b evita capturar dentro de otra palabra (REPO01, XP001Y no son productos).
# Hasta 6 dígitos: el mismo límite que valida la tool.
PATRON = re.compile(r"\bP(\d{1,6})\b", re.IGNORECASE)
MAX_ENTIDADES = 10


def extraer_product_ids(consulta: str) -> list[str]:
    """Devuelve los identificadores de producto presentes en el texto.

    Conserva el orden de aparición: el primero mencionado suele ser el producto
    sobre el que se pregunta, y ese orden se refleja después en el informe.
    """
    encontrados = [f"P{m.group(1)}".upper() for m in PATRON.finditer(consulta)]
    unicos = list(dict.fromkeys(encontrados))
    return unicos[:MAX_ENTIDADES]


MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

# "2025-06" y "2025/06". El `(?!-?\d)` final evita comerse la primera mitad de
# una fecha completa: en "2025-03-15" esto no debe leer el mes 03 y descartar
# el día. Ese caso lo resuelve RANGO.
MES_NUMERICO = re.compile(r"\b(\d{4})[-/](0?[1-9]|1[0-2])\b(?!-?\d)")

# "junio de 2025", "junio 2025", "marzo del 2026".
MES_EN_LETRAS = re.compile(
    r"\b(" + "|".join(MESES) + r")\s+(?:de\s+|del\s+)?(\d{4})\b", re.IGNORECASE
)

# "entre 2025-03-15 y 2025-04-10".
RANGO = re.compile(
    r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b\s*(?:y|a|hasta)\s*"
    r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", re.IGNORECASE
)


def _mes_completo(anio: int, mes: int) -> Periodo:
    """El período que va del día 1 al último día real de ese mes.

    `calendar.monthrange` y no `timedelta(30)`: febrero tiene 28 o 29, y un mes
    calculado a ojo produce un rango que no cubre lo que dice cubrir.
    """
    return Periodo(
        desde=date(anio, mes, 1),
        hasta=date(anio, mes, calendar.monthrange(anio, mes)[1]),
    )


def extraer_periodo(consulta: str, hoy: date) -> Periodo | None:
    """Devuelve el período explícito de la consulta, o `None` si no lo hay.

    **Por qué es determinístico**, igual que `extraer_product_ids`: una fecha es
    un patrón fijo, y el software la resuelve perfecto, gratis y sin
    equivocarse. Es el mismo argumento que ya está escrito en `router.ESQUEMA`
    para los identificadores de producto.

    El costo de no hacerlo se midió el 2026-08-12. El router le pedía al modelo
    un número de DÍAS y el período se armaba siempre como `[hoy - dias, hoy]`,
    así que el agente **no podía representar un rango histórico cerrado**. La
    consulta "Analizá el desempeño de P010 durante 2025-06" se resolvía como
    2025-12-30 → 2026-06-30: el mes preguntado quedaba fuera del período
    analizado, las métricas describían otros seis meses, y ninguna métrica del
    eval lo detectaba. El few-shot del router enseñaba el error de frente
    ("durante enero" → `{"dias": 31}`).

    `None` significa "acá no hay nada que extraer", y recién ahí decide el
    modelo con su cantidad de días. No se inventa un rango: inventarlo era el
    bug.
    """
    if (m := RANGO.search(consulta)) is not None:
        try:
            desde = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            hasta = date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
        except ValueError:
            return None
        # Un rango invertido no se corrige en silencio: `Periodo` lo rechazaría
        # tres capas más adelante y con menos contexto para explicarlo.
        return None if desde > hasta else _acotar(Periodo(desde=desde, hasta=hasta), hoy)

    if (m := MES_NUMERICO.search(consulta)) is not None:
        return _acotar(_mes_completo(int(m.group(1)), int(m.group(2))), hoy)

    if (m := MES_EN_LETRAS.search(consulta)) is not None:
        return _acotar(
            _mes_completo(int(m.group(2)), MESES[m.group(1).lower()]), hoy)

    return None


def _acotar(periodo: Periodo, hoy: date) -> Periodo | None:
    """Descarta lo que el dataset no puede responder.

    Un período enteramente posterior a `hoy` no tiene datos, y arrastrarlo
    produce un informe vacío que nadie sabe explicar. Devolver `None` deja que
    el flujo siga por el camino normal.
    """
    return None if periodo.desde > hoy else periodo


def corregir_intencion_por_entidades(
    intencion: Intencion, entidades: list[str]
) -> tuple[Intencion, str | None]:
    """Corrige la clasificación usando lo que el software sí puede verificar.

    Devuelve la intención corregida y, si hubo corrección, el motivo — que va a
    parar a las advertencias del informe. Una corrección silenciosa es una
    corrección que nadie puede auditar.

    Dos reglas, ambas derivadas de la definición de las categorías:

    1. `company_research` es, por definición, sobre empresas EXTERNAS. Si la
       consulta nombra productos del catálogo interno, la clasificación está
       mal: como mínimo la consulta es híbrida.

    2. `product_performance` y `hybrid` necesitan al menos un producto. Sin
       ninguno no hay nada que consultar, y dejar seguir al grafo llevaría al
       sintetizador a redactar sobre la nada — que es como nacen los informes
       inventados.

    `fuera_de_alcance` NO se corrige: si el modelo entendió que la consulta no
    corresponde, la presencia de un identificador no lo contradice. "Borrá el
    P001" nombra un producto y sigue estando fuera de alcance.
    """
    if intencion == Intencion.FUERA_DE_ALCANCE:
        return intencion, None

    if intencion == Intencion.COMPANY_RESEARCH and entidades:
        return Intencion.HYBRID, (
            f"La consulta se clasificó como investigación de empresas pero "
            f"menciona productos del catálogo interno ({', '.join(entidades)}). "
            "Se reclasificó como análisis híbrido."
        )

    if intencion in (Intencion.PRODUCT_PERFORMANCE, Intencion.HYBRID) and not entidades:
        return Intencion.FUERA_DE_ALCANCE, (
            "No se identificó ningún producto en la consulta. Indicá los "
            "identificadores (por ejemplo P001) para poder analizarlos."
        )

    return intencion, None
