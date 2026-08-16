"""Evaluación del retrieval contra el ground truth de recuperación.

El corpus sabe qué documento explica cada evento sembrado en el dataset. Eso
convierte al retrieval en algo medible: no "el índice devolvió algo" sino "el
índice devolvió LO QUE CORRESPONDE".

Métrica principal: **hit rate @ k** — en qué proporción de los eventos el
documento que los explica aparece entre los k primeros resultados.

Umbrales fijados ANTES de medir, misma regla que en el resto del proyecto:

    hit rate @1  >= 0.60
    hit rate @3  >= 0.80
    MRR          >= 0.70

El @1 es más bajo a propósito: con un corpus donde varios documentos hablan del
mismo producto, que el correcto salga primero es exigente. Lo que importa para
el informe es que entre en el contexto, y para eso alcanza el @3.
"""

from __future__ import annotations

import pytest

from rag.corpus import Corpus, generar_corpus
from rag.indice import IndiceVectorial, chunkear
from seeds.generate import DatasetConfig, generar_dataset

pytestmark = pytest.mark.rag

MIN_HIT_1 = 0.60
MIN_HIT_3 = 0.80
MIN_MRR = 0.70

# Cómo preguntaría un usuario por cada tipo de evento.
CONSULTAS = {
    "pico_devoluciones": "por que aumentaron tanto las devoluciones de {pid}",
    "caida_ventas": "por que se desplomaron las ventas de {pid}",
    "pico_ventas": "a que se debio el pico de ventas de {pid}",
}


@pytest.fixture(scope="module")
def escenario() -> tuple[Corpus, IndiceVectorial]:
    dataset = generar_dataset(DatasetConfig())
    corpus = generar_corpus(dataset, seed=42)
    indice = IndiceVectorial().construir(chunkear(corpus.documentos))
    return corpus, indice


@pytest.fixture(scope="module")
def posiciones(escenario: tuple[Corpus, IndiceVectorial]) -> list[int | None]:
    """Posición (1-based) del documento correcto en los resultados, o None."""
    corpus, indice = escenario
    salida: list[int | None] = []
    for e in corpus.explicaciones:
        consulta = CONSULTAS[e.tipo_evento].format(pid=e.product_id)
        resultados = indice.buscar(consulta, top_k=5)
        pos = next(
            (i for i, r in enumerate(resultados, 1) if r.chunk.doc_id == e.doc_id),
            None,
        )
        salida.append(pos)
    return salida


def _hit_rate(posiciones: list[int | None], k: int) -> float:
    aciertos = sum(1 for p in posiciones if p is not None and p <= k)
    return aciertos / len(posiciones)


def test_hit_rate_at_1(posiciones: list[int | None]) -> None:
    valor = _hit_rate(posiciones, 1)
    print(f"\n  HIT RATE @1: {valor:.0%}")
    assert valor >= MIN_HIT_1, f"hit rate @1 de {valor:.0%}, mínimo {MIN_HIT_1:.0%}"


def test_hit_rate_at_3(
    posiciones: list[int | None],
    escenario: tuple[Corpus, IndiceVectorial],
) -> None:
    """La métrica que decide si el informe va a tener la evidencia correcta.

    El agente recupera top_k=4, así que un documento que entra en el top-3
    llega al contexto del sintetizador.
    """
    corpus, _ = escenario
    valor = _hit_rate(posiciones, 3)
    fallos = [
        f"{e.doc_id} ({e.tipo_evento}, {e.product_id})"
        for e, p in zip(corpus.explicaciones, posiciones, strict=True)
        if p is None or p > 3
    ]
    print(f"\n  HIT RATE @3: {valor:.0%}")
    for f in fallos:
        print(f"    FALLO  {f}")
    assert valor >= MIN_HIT_3, f"hit rate @3 de {valor:.0%}, mínimo {MIN_HIT_3:.0%}"


def test_mean_reciprocal_rank(posiciones: list[int | None]) -> None:
    """Premia que el documento correcto esté arriba, no solo que esté."""
    mrr = sum(1 / p for p in posiciones if p is not None) / len(posiciones)
    print(f"\n  MRR: {mrr:.2f}")
    assert mrr >= MIN_MRR, f"MRR de {mrr:.2f}, mínimo {MIN_MRR}"


def test_el_retrieval_funciona_para_los_tres_tipos_de_evento(
    escenario: tuple[Corpus, IndiceVectorial],
    posiciones: list[int | None],
) -> None:
    """Un promedio alto puede esconder un tipo de evento que nunca se recupera.

    Medir por tipo evita que el buen desempeño en el caso frecuente tape la
    ceguera total en otro.
    """
    corpus, _ = escenario
    por_tipo: dict[str, list[int | None]] = {}
    for e, p in zip(corpus.explicaciones, posiciones, strict=True):
        por_tipo.setdefault(e.tipo_evento, []).append(p)

    print()
    for tipo, ps in sorted(por_tipo.items()):
        valor = _hit_rate(ps, 3)
        print(f"  {tipo:<20} hit@3 {valor:.0%}  ({len(ps)} casos)")
        assert valor >= 0.5, f"{tipo}: hit rate @3 de solo {valor:.0%}"


def test_el_filtro_por_producto_mejora_la_precision(
    escenario: tuple[Corpus, IndiceVectorial],
) -> None:
    """Acotar al producto correcto debería subir el acierto.

    Si no lo hiciera, el filtro sería decorativo y convendría sacarlo.
    """
    corpus, indice = escenario
    sin_filtro = con_filtro = 0

    for e in corpus.explicaciones:
        consulta = CONSULTAS[e.tipo_evento].format(pid=e.product_id)
        libres = indice.buscar(consulta, top_k=3)
        acotados = indice.buscar(consulta, top_k=3, product_id=e.product_id)
        sin_filtro += any(r.chunk.doc_id == e.doc_id for r in libres)
        con_filtro += any(r.chunk.doc_id == e.doc_id for r in acotados)

    total = len(corpus.explicaciones)
    print(f"\n  hit@3 sin filtro: {sin_filtro/total:.0%} · "
          f"con filtro por producto: {con_filtro/total:.0%}")
    assert con_filtro >= sin_filtro
