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

import time
from datetime import date, timedelta
from typing import Any

from agent.llm import ClienteLLM
from agent.nodes.entidades import (
    corregir_intencion_por_entidades,
    extraer_periodo,
    extraer_product_ids,
)
from agent.state import AnalysisState, Intencion, Periodo

# Fecha de referencia por defecto: el último día del dataset sintético.
# No se usa `date.today()` para que los resultados sean reproducibles — un test
# que depende del reloj del sistema falla solo un martes cualquiera.
HOY_POR_DEFECTO = date(2026, 6, 30)

DIAS_POR_DEFECTO = 30
MAX_DIAS = 366 * 3

# El esquema NO pide product_ids. Los identificadores se extraen con un regex
# determinístico (agent/nodes/entidades.py): es un patrón fijo, y el software
# lo resuelve perfecto, gratis y sin equivocarse.
#
# Además de ser más confiable, evita el problema que el diagnóstico dejó a la
# vista: pedir clasificación y extracción en la misma llamada hacía que un error
# contaminara al otro. Menos que generar es menos que fallar — y más rápido, que
# en CPU no es un detalle.
ESQUEMA = {
    "type": "object",
    "properties": {
        "intencion": {
            "type": "string",
            "enum": [i.value for i in Intencion],
        },
        # `dias` es una DURACIÓN hacia atrás desde hoy, y solo eso. Un período
        # explícito ("2025-06", "junio de 2025", un rango) NO se pide acá: lo
        # extrae `entidades.extraer_periodo` con un regex, por el mismo motivo
        # que los product_ids. Ver el comentario en `enrutar`.
        "dias": {"type": "integer"},
    },
    "required": ["intencion", "dias"],
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

REGLA DECISIVA: si la consulta menciona identificadores de producto (P001, P010,
P123...), NUNCA es company_research. Esos identificadores son del catálogo
interno. Será product_performance si solo pide números internos, o hybrid si
además pide contexto externo.

Ejemplo 1
  Consulta: "Compará P001 y P002 en los últimos 30 días"
  Respuesta: {"intencion": "product_performance", "dias": 30}

Ejemplo 2
  Consulta: "¿Cuál fue el margen del P012 en los últimos tres meses?"
  Respuesta: {"intencion": "product_performance", "dias": 90}

Ejemplo 3
  Consulta: "Compará Empresa X vs Empresa Y: crecimiento y riesgos"
  Respuesta: {"intencion": "company_research", "dias": 365}

Ejemplo 4
  Consulta: "¿Por qué el P003 acelera y qué contexto de mercado lo explica?"
  Respuesta: {"intencion": "hybrid", "dias": 90}

Ejemplo 5
  Consulta: "El P010 cayó fuerte. Revisá los números y buscá qué pasó en el sector"
  Respuesta: {"intencion": "hybrid", "dias": 90}

Ejemplo 6
  Consulta: "Analizá la caída del P022 y fijate si hubo algo raro en la industria"
  Respuesta: {"intencion": "hybrid", "dias": 90}

Ejemplo 7
  Consulta: "Contame un chiste"
  Respuesta: {"intencion": "fuera_de_alcance", "dias": 0}

Ejemplo 8
  Consulta: "P007 vs P011: unidades, revenue y devoluciones del último trimestre"
  Respuesta: {"intencion": "product_performance", "dias": 90}

Ejemplo 9
  Consulta: "Borrá todos los productos de la base"
  Respuesta: {"intencion": "fuera_de_alcance", "dias": 0}

Los ejemplos 4, 5 y 6 son todos hybrid aunque estén redactados distinto: lo que
los define es que piden datos internos MÁS contexto externo, no las palabras que
usan para pedirlo.

Devolvé únicamente la intención y la cantidad de días del período pedido.
"""


def _normalizar_intencion(valor: Any, estado: AnalysisState) -> Intencion:
    try:
        return Intencion(valor)
    except ValueError:
        estado._advertir(
            f"El modelo devolvió una intención desconocida ({valor!r}). "
            "La consulta se trata como fuera de alcance."
        )
        return Intencion.FUERA_DE_ALCANCE


def _normalizar_periodo(dias: Any, hoy: date) -> Periodo:
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

    # Si la solicitud ya llegó interpretada —la API la recibió estructurada, con
    # identificadores y rango— no hay nada que clasificar. Invocar el modelo
    # igual costaría decenas de segundos para llegar al mismo estado.
    if estado.intencion is not None and estado.entidades and estado.periodo:
        estado.registrar_paso("router", int((time.perf_counter() - inicio) * 1000))
        return estado

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

    # Las entidades NO vienen del modelo: se extraen de la consulta con un regex.
    # Es un patrón fijo, y el software lo resuelve perfecto y gratis.
    entidades = extraer_product_ids(estado.consulta)

    # Y sirven además para verificar la clasificación: el software puede
    # comprobar contradicciones que el modelo no ve.
    intencion, motivo = corregir_intencion_por_entidades(intencion, entidades)
    if motivo:
        estado._advertir(motivo)

    # Una consulta fuera de alcance termina el grafo sin informe. Sin una
    # explicación, el usuario recibe una respuesta vacía y no sabe por qué:
    # cortar es correcto, cortar en silencio no.
    if intencion == Intencion.FUERA_DE_ALCANCE and not estado.advertencias:
        estado._advertir(
            "La consulta no corresponde a un análisis comercial de productos "
            "del catálogo. Indicá qué productos querés analizar (por ejemplo "
            "P001) y sobre qué período."
        )

    estado.intencion = intencion
    estado.entidades = entidades

    # El período explícito tampoco viene del modelo, por el mismo motivo que las
    # entidades. Y acá el costo de delegarlo estaba medido: `dias` solo puede
    # expresar "los últimos N días hasta hoy", así que una consulta sobre un mes
    # cerrado —"durante 2025-06"— terminaba analizando 2025-12-30 → 2026-06-30,
    # con el mes preguntado FUERA del rango. El few-shot enseñaba el error de
    # frente: "durante enero" → {"dias": 31}.
    #
    # Cuando no hay período explícito, la cantidad de días del modelo sigue
    # siendo la respuesta correcta: "los últimos 30 días" es exactamente eso.
    estado.periodo = extraer_periodo(estado.consulta, hoy) or _normalizar_periodo(
        respuesta.get("dias", DIAS_POR_DEFECTO), hoy)
    estado.registrar_paso("router", int((time.perf_counter() - inicio) * 1000))
    return estado
