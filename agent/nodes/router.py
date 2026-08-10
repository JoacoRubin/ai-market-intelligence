"""Nodo IntentRouter: entiende qué se está pidiendo.

Es el primer nodo del grafo y el que el spike señaló como más frágil:
`llama3.2:3b` clasificó mal la intención en 3 de 3 casos, siempre como
`company_research`. El JSON era válido contra el esquema y el contenido estaba
mal — validez de esquema no implica correctitud semántica.

Dos correcciones, y ninguna es "usar un modelo más grande":

1. **Few-shots en el prompt.** El modelo no falla por falta de capacidad sino
   por falta de contexto sobre qué significa cada categoría en ESTE dominio.
   Los ejemplos son más baratos y más efectivos que cualquier explicación.

2. **Normalización defensiva de la salida.** Todo lo que devuelve el modelo se
   valida acá: intenciones desconocidas, identificadores mal formados, períodos
   absurdos, campos faltantes. El nodo asume que la respuesta puede ser
   cualquier cosa, porque puede serlo.

Si el modelo falla del todo, el estado queda en `fuera_de_alcance` con el error
registrado. Propagar la excepción dejaría al usuario con un 500 y sin
explicación; el grafo prefiere seguir y contar qué pasó.
"""

from __future__ import annotations

import re
import time
from datetime import date, timedelta

from agent.llm import ClienteLLM
from agent.state import AnalysisState, Intencion, Periodo

# Fecha de referencia por defecto: el último día del dataset sintético.
# No se usa `date.today()` para que los resultados sean reproducibles — un test
# que depende del reloj del sistema falla solo un martes cualquiera.
HOY_POR_DEFECTO = date(2026, 6, 30)

DIAS_POR_DEFECTO = 30
MAX_DIAS = 366 * 3
MAX_ENTIDADES = 10
PATRON_PRODUCTO = re.compile(r"^P\d{1,6}$")

ESQUEMA = {
    "type": "object",
    "properties": {
        "intencion": {
            "type": "string",
            "enum": [i.value for i in Intencion],
        },
        "product_ids": {"type": "array", "items": {"type": "string"}},
        "dias": {"type": "integer"},
    },
    "required": ["intencion", "product_ids", "dias"],
}

# Los ejemplos atacan de frente la confusión medida en el spike: el modelo
# tomaba comparaciones de productos por investigación de empresas.
SISTEMA = """\
Sos un clasificador de consultas de análisis comercial. Devolvés SOLO JSON.

Categorías de intención:

- product_performance: la consulta es sobre PRODUCTOS del catálogo interno
  (identificadores como P001). Ventas, unidades, revenue, margen, crecimiento,
  devoluciones, comparaciones entre productos, proyecciones de productos.

- company_research: la consulta es sobre EMPRESAS externas (Apple, Tesla,
  una competidora). Contexto de mercado, situación financiera, riesgos.

- hybrid: mezcla las dos. Pregunta por productos internos Y pide contexto
  externo que lo explique.

- fuera_de_alcance: no es una consulta de análisis comercial.

DISTINCIÓN CRÍTICA: comparar dos PRODUCTOS entre sí es product_performance,
NO company_research. Una empresa es una organización externa; un producto es
un artículo del catálogo. Si aparecen identificadores tipo P001, es
product_performance.

Ejemplo 1
  Consulta: "Compará Producto A y Producto B en los últimos 30 días"
  Respuesta: {"intencion": "product_performance", "product_ids": ["P001", "P002"], "dias": 30}

Ejemplo 2
  Consulta: "¿Cuál fue el margen del P012 durante enero?"
  Respuesta: {"intencion": "product_performance", "product_ids": ["P012"], "dias": 31}

Ejemplo 3
  Consulta: "Compará Empresa X vs Empresa Y: crecimiento y riesgos"
  Respuesta: {"intencion": "company_research", "product_ids": [], "dias": 365}

Ejemplo 4
  Consulta: "¿Por qué el P003 acelera y qué contexto de mercado lo explica?"
  Respuesta: {"intencion": "hybrid", "product_ids": ["P003"], "dias": 90}

Ejemplo 5
  Consulta: "Contame un chiste"
  Respuesta: {"intencion": "fuera_de_alcance", "product_ids": [], "dias": 0}

Ejemplo 6
  Consulta: "P007 vs P011: unidades, revenue y devoluciones del último trimestre"
  Respuesta: {"intencion": "product_performance", "product_ids": ["P007", "P011"], "dias": 90}

Extraé los identificadores de producto tal como aparecen (P seguido de dígitos).
Si la consulta menciona productos por nombre y no por identificador, devolvé
product_ids vacío.
"""


def _normalizar_intencion(valor, estado: AnalysisState) -> Intencion:
    try:
        return Intencion(valor)
    except ValueError:
        estado._advertir(
            f"El modelo devolvió una intención desconocida ({valor!r}). "
            "La consulta se trata como fuera de alcance."
        )
        return Intencion.FUERA_DE_ALCANCE


def _normalizar_entidades(valores, estado: AnalysisState) -> list[str]:
    if not isinstance(valores, list):
        return []

    validos, descartados = [], []
    for v in valores:
        texto = str(v).strip()
        (validos if PATRON_PRODUCTO.match(texto) else descartados).append(texto)

    if descartados:
        estado._advertir(
            f"Se descartaron identificadores con formato inválido: {descartados}"
        )

    unicos = list(dict.fromkeys(validos))
    if len(unicos) > MAX_ENTIDADES:
        estado._advertir(
            f"Se recortó la lista a {MAX_ENTIDADES} productos "
            f"(el modelo propuso {len(unicos)})."
        )
        unicos = unicos[:MAX_ENTIDADES]
    return unicos


def _normalizar_periodo(dias, hoy: date) -> Periodo:
    try:
        dias = int(dias)
    except (TypeError, ValueError):
        dias = DIAS_POR_DEFECTO
    dias = max(1, min(dias, MAX_DIAS))
    return Periodo(desde=hoy - timedelta(days=dias - 1), hasta=hoy)


def enrutar(
    estado: AnalysisState,
    cliente: ClienteLLM,
    hoy: date = HOY_POR_DEFECTO,
) -> AnalysisState:
    """Clasifica la consulta y completa intención, entidades y período."""
    inicio = time.perf_counter()

    try:
        respuesta = cliente.estructurado(SISTEMA, estado.consulta, ESQUEMA)
    except Exception as e:
        estado.error = f"{type(e).__name__}: {e}"
        estado.intencion = Intencion.FUERA_DE_ALCANCE
        estado._advertir(
            "No se pudo consultar el modelo de lenguaje para interpretar la "
            "consulta. El análisis no puede continuar."
        )
        estado.registrar_paso("router", int((time.perf_counter() - inicio) * 1000))
        return estado

    if not isinstance(respuesta, dict):
        respuesta = {}

    intencion = _normalizar_intencion(
        respuesta.get("intencion", Intencion.FUERA_DE_ALCANCE), estado
    )
    entidades = _normalizar_entidades(respuesta.get("product_ids", []), estado)

    # Una intención sobre productos sin ningún producto identificado no es
    # accionable: no hay qué consultar. Seguir adelante llevaría al sintetizador
    # a redactar sobre la nada, que es como nacen los informes inventados.
    if intencion in (Intencion.PRODUCT_PERFORMANCE, Intencion.HYBRID) and not entidades:
        estado._advertir(
            "No se identificó ningún producto en la consulta. Indicá los "
            "identificadores (por ejemplo P001) para poder analizarlos."
        )
        intencion = Intencion.FUERA_DE_ALCANCE

    estado.intencion = intencion
    estado.entidades = entidades
    estado.periodo = _normalizar_periodo(respuesta.get("dias", DIAS_POR_DEFECTO), hoy)
    estado.registrar_paso("router", int((time.perf_counter() - inicio) * 1000))
    return estado
