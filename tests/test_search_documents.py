"""Tests de la tool `search_documents`, con foco en el filtro temporal.

El filtro `hasta` existía sin un solo test de ejecución, y por ahí se coló el
defecto que este módulo fija: excluía el documento que explicaba el evento.

La justificación escrita del filtro era esta:

    "Sí se excluye lo posterior: nada fechado después puede haber causado lo
    que pasó antes."

El razonamiento causal es impecable y confunde la causa con el **reporte** de la
causa. Un reporte de quiebre de stock fechado el 1 de julio no causó nada:
describe un quiebre del 25 de junio. Los documentos se escriben después de los
hechos que narran, siempre — es lo que los hace documentos.
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import pytest

from agent.state import AnalysisState, Periodo
from agent.tools.search_documents import (
    VENTANA_DE_REPORTE,
    EntradaSearchDocuments,
    ejecutar_search_documents,
)
from rag.indice import Chunk, IndiceVectorial, Resultado


class IndiceEspia:
    """Doble del índice que registra con qué filtros lo llamaron.

    Lo que se está probando es el criterio de búsqueda, no el vecino más
    cercano: si acá se usara el índice real, un cambio de embeddings haría
    fallar un test sobre fechas.
    """

    def __init__(self, resultados: list[Resultado] | None = None) -> None:
        self.resultados = resultados or []
        self.llamadas: list[dict[str, Any]] = []

    def buscar(self, consulta: str, top_k: int = 5,
               product_id: str | None = None, desde: date | None = None,
               hasta: date | None = None) -> list[Resultado]:
        self.llamadas.append({
            "consulta": consulta, "top_k": top_k, "product_id": product_id,
            "desde": desde, "hasta": hasta,
        })
        return [
            r for r in self.resultados
            if (hasta is None or r.chunk.fecha <= hasta)
            and (desde is None or r.chunk.fecha >= desde)
        ][:top_k]


def _chunk(doc_id: str, fecha: date, texto: str = "Quiebre de stock") -> Chunk:
    return Chunk(
        chunk_id=f"{doc_id}#1", doc_id=doc_id, texto=texto,
        tipo="reporte_operativo", titulo=f"Reporte — {doc_id}",
        seccion="§2.2", fecha=fecha, product_id="P010",
    )


def _estado(periodo: Periodo | None = None) -> AnalysisState:
    return AnalysisState(
        request_id="test-search",
        consulta="Analizá el desempeño de P010 durante 2025-06",
        entidades=["P010"],
        periodo=periodo,
    )


PERIODO_JUNIO = Periodo(desde=date(2025, 6, 1), hasta=date(2025, 6, 30))
ENTRADA = EntradaSearchDocuments(consulta="por qué cayeron las ventas",
                                 product_id="P010", top_k=4)


# --- el defecto: el reporte llega después del hecho ---------------------------

def test_recupera_el_reporte_fechado_pocos_dias_despues_del_periodo() -> None:
    """El caso P010 del eval, exacto.

    Evento el 2025-06-25; `doc_stock_006`, el reporte que lo explica, fechado
    el 2025-07-01. El filtro `hasta = 2025-06-30` lo excluía por UN DÍA, y el
    informe salía sin la única evidencia que decía por qué.
    """
    indice = IndiceEspia([Resultado(chunk=_chunk("doc_stock_006", date(2025, 7, 1)),
                                    score=0.878)])

    evidencia = ejecutar_search_documents(
        ENTRADA, _estado(PERIODO_JUNIO), cast(IndiceVectorial, indice))

    assert [e["doc_id"] for e in evidencia] == ["doc_stock_006"]


def test_el_margen_se_aplica_sobre_el_fin_del_periodo() -> None:
    indice = IndiceEspia()

    ejecutar_search_documents(ENTRADA, _estado(PERIODO_JUNIO), cast(IndiceVectorial, indice))

    assert indice.llamadas[0]["hasta"] == date(2025, 6, 30) + VENTANA_DE_REPORTE


def test_sigue_excluyendo_lo_que_no_puede_explicar_el_periodo() -> None:
    """El margen no es lo mismo que no filtrar. Un documento de septiembre para
    un análisis de junio no lo explica: lo recuerda."""
    indice = IndiceEspia([Resultado(chunk=_chunk("doc_stock_099", date(2025, 9, 15)),
                                    score=0.88)])

    evidencia = ejecutar_search_documents(
        ENTRADA, _estado(PERIODO_JUNIO), cast(IndiceVectorial, indice))

    assert evidencia == []


def test_la_ventana_cubre_como_se_construye_el_corpus() -> None:
    """`rag/corpus.py` fecha cada documento explicativo entre 1 y 7 días después
    de su evento. La ventana tiene que cubrir ese rango con margen, y además el
    ancho de un mes: un evento del día 1 con reporte al día 7 ya entraba, pero
    uno del día 30 no."""
    assert VENTANA_DE_REPORTE.days >= 7


# --- lo que no cambia ---------------------------------------------------------

def test_sin_periodo_no_se_filtra_por_fecha() -> None:
    indice = IndiceEspia()

    ejecutar_search_documents(ENTRADA, _estado(None), cast(IndiceVectorial, indice))

    assert indice.llamadas[0]["hasta"] is None


def test_no_se_filtra_por_desde() -> None:
    """Un documento anterior al período puede explicarlo igual: una política
    vigente, un lote despachado antes. Eso ya era correcto y sigue igual."""
    indice = IndiceEspia()

    ejecutar_search_documents(ENTRADA, _estado(PERIODO_JUNIO), cast(IndiceVectorial, indice))

    assert indice.llamadas[0]["desde"] is None


def test_el_producto_y_el_top_k_se_pasan_tal_cual() -> None:
    indice = IndiceEspia()

    ejecutar_search_documents(ENTRADA, _estado(PERIODO_JUNIO), cast(IndiceVectorial, indice))

    assert indice.llamadas[0]["product_id"] == "P010"
    assert indice.llamadas[0]["top_k"] == 4


def test_sin_evidencia_relevante_queda_una_advertencia_en_el_informe() -> None:
    """Un informe sin evidencia es válido; uno que no avisa que no la tuvo, no."""
    estado = _estado(PERIODO_JUNIO)

    ejecutar_search_documents(ENTRADA, estado, cast(IndiceVectorial, IndiceEspia([])))

    assert any("evidencia documental" in a for a in estado.advertencias)


def test_la_evidencia_queda_en_el_estado_con_su_doc_id() -> None:
    estado = _estado(PERIODO_JUNIO)
    indice = IndiceEspia([Resultado(chunk=_chunk("doc_stock_006", date(2025, 7, 1)),
                                    score=0.878)])

    ejecutar_search_documents(ENTRADA, estado, cast(IndiceVectorial, indice))

    assert [e["doc_id"] for e in estado.evidencia] == ["doc_stock_006"]


def test_sin_presupuesto_de_tools_no_se_busca() -> None:
    estado = _estado(PERIODO_JUNIO)
    while estado.puede_llamar_tool():
        estado.registrar_llamada_tool()
    indice = IndiceEspia([Resultado(chunk=_chunk("doc_stock_006", date(2025, 7, 1)),
                                    score=0.878)])

    assert ejecutar_search_documents(ENTRADA, estado, cast(IndiceVectorial, indice)) == []
    assert indice.llamadas == []


@pytest.mark.parametrize("malicioso", ["P0; DROP", "../P001", "PPP", ""])
def test_rechaza_identificadores_que_no_son_identificadores(malicioso: str) -> None:
    with pytest.raises(ValueError):
        EntradaSearchDocuments(consulta="por qué", product_id=malicioso)
