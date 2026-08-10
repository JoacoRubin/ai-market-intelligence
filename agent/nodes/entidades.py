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

import re

from agent.state import Intencion

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
