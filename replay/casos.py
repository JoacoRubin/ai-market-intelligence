"""Qué ejecuciones se publican, y por qué esas.

Los casos salen del golden set y no de consultas inventadas para la ocasión. La
razón importa: el golden set es con lo que se mide al agente, así que lo que se
muestra en el replay es exactamente lo que se evalúa. Un demo armado con
consultas elegidas para lucirse sería otra cosa — una demo de ventas, no una
muestra de ingeniería.

La selección es corta a propósito. Cinco ejecuciones bien contadas dicen más que
veinte que nadie mira, y cada una cuesta minutos reales de CPU al capturarla.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from agent.state import Intencion

RAIZ = Path(__file__).resolve().parent.parent
GOLDEN_SET = RAIZ / "eval" / "golden_set.jsonl"

# Las tools que existen de verdad, leídas del disco y no de una lista a mano.
# `TOOLS_PERMITIDAS` en agent/state.py nombra `public_research`, que nunca se
# implementó: mirar los archivos es la única forma de que esta distinción no
# quede desactualizada en silencio.
TOOLS_IMPLEMENTADAS = sorted(
    p for p in (RAIZ / "agent" / "tools").glob("*.py") if not p.stem.startswith("__")
)

# `COMPANY_RESEARCH` se excluye del replay porque el planner la rechaza: sin la
# tool `public_research` no hay nada que ejecutar (ver agent/nodes/planner.py).
# La captura mostraría una advertencia honesta y ningún análisis — y `out-03` ya
# cubre mejor el caso de "el agente dice que no". El día que la tool exista, el
# test que vigila esta constante falla y obliga a revisar la selección.
EXCLUIDAS: set[Intencion] = {Intencion.COMPANY_RESEARCH}

# El orden es narrativo: así se ve en el sitio, de arriba hacia abajo.
#
#   cmp-01   comparación de dos productos. Es el caso que el spike original
#            falló, y el que motivó la regla de no delegar extracción al LLM.
#   perf-01  un solo producto con margen: el camino corto, para que se vea
#            que el agente no sobre-planifica cuando no hace falta.
#   hyb-02   SQL + RAG juntos. Acá aparecen las citas documentales con su
#            doc_id, que es lo que distingue este sistema de un chatbot.
#   hold-05  comparación SIN la palabra "comparar". Es holdout: no está en el
#            prompt, así que demuestra generalización y no memorización.
#   out-03   "Borrá todos los productos". Fuera de alcance Y destructivo. Es la
#            captura más importante de todas: muestra que el agente se niega, y
#            que aunque no se negara el usuario de base es read-only.
SELECCION: tuple[str, ...] = ("cmp-01", "perf-01", "hyb-02", "hold-05", "out-03")


class CasoGolden(BaseModel):
    """Una fila del golden set."""

    id: str
    consulta: str
    intencion: Intencion
    product_ids: list[str] = []
    en_prompt: bool = False
    # Explica por qué el caso es interesante. Viaja al sitio como pie de la
    # ejecución: sin esto el visitante ve una consulta cualquiera.
    nota: str | None = None


def cargar_golden_set(ruta: Path = GOLDEN_SET) -> list[CasoGolden]:
    with ruta.open(encoding="utf-8") as f:
        return [CasoGolden(**json.loads(linea)) for linea in f if linea.strip()]


def casos_para_replay(
    seleccion: tuple[str, ...] = SELECCION, ruta: Path = GOLDEN_SET
) -> list[CasoGolden]:
    """Resuelve la selección contra el golden set, en el orden declarado.

    Un id inexistente corta acá. La alternativa —saltearlo en silencio— produce
    un replay con cuatro casos donde se esperaban cinco, y nadie lo nota hasta
    que falta la ejecución que justamente se quería mostrar.
    """
    por_id = {c.id: c for c in cargar_golden_set(ruta)}

    faltantes = [i for i in seleccion if i not in por_id]
    if faltantes:
        raise ValueError(
            f"la selección nombra casos que no están en el golden set: "
            f"{faltantes}. Disponibles: {sorted(por_id)}"
        )

    return [por_id[i] for i in seleccion]
