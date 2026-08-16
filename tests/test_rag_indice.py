"""Tests del chunking, el índice vectorial y la recuperación.

Estos tests cargan el modelo de embeddings y calculan vectores de verdad. Son
los más lentos de la suite sin LLM (~20 s), y se justifican: un índice que
"funciona" pero recupera el documento equivocado no falla, miente. Y un RAG que
miente es peor que no tener RAG, porque el informe cita una fuente que no dice
lo que se afirma.

La métrica que importa no es que el índice devuelva algo: es **qué** devuelve.
Por eso el corpus tiene distractores y los tests miden si el documento correcto
entra en el top-k.
"""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from rag.corpus import Corpus, generar_corpus
from rag.indice import IndiceVectorial, chunkear
from seeds.generate import DatasetConfig, generar_dataset

pytestmark = pytest.mark.rag


@pytest.fixture(scope="module")
def dataset() -> dict[str, pd.DataFrame]:
    return generar_dataset(DatasetConfig())


@pytest.fixture(scope="module")
def corpus(dataset: dict[str, pd.DataFrame]) -> Corpus:
    return generar_corpus(dataset, seed=42)


@pytest.fixture(scope="module")
def indice(corpus: Corpus) -> IndiceVectorial:
    idx = IndiceVectorial()
    idx.construir(chunkear(corpus.documentos))
    return idx


# --- Chunking ----------------------------------------------------------------

def test_todo_documento_produce_al_menos_un_chunk(corpus: Corpus) -> None:
    chunks = chunkear(corpus.documentos)
    assert {c.doc_id for c in chunks} == {d.id for d in corpus.documentos}


def test_los_chunks_heredan_la_metadata_del_documento(corpus: Corpus) -> None:
    por_id = {d.id: d for d in corpus.documentos}
    for c in chunkear(corpus.documentos):
        doc = por_id[c.doc_id]
        assert c.product_id == doc.product_id
        assert c.fecha == doc.fecha
        assert c.tipo == doc.tipo


def test_los_chunks_no_son_gigantes(corpus: Corpus) -> None:
    """Un chunk demasiado grande diluye la señal: el embedding promedia temas
    distintos y deja de parecerse a ninguna consulta en particular."""
    for c in chunkear(corpus.documentos):
        assert len(c.texto) <= 1200, f"{c.chunk_id}: {len(c.texto)} caracteres"


def test_los_chunks_no_son_minusculos(corpus: Corpus) -> None:
    """Un chunk sin contexto suficiente recupera bien y explica mal."""
    chunks = chunkear(corpus.documentos)
    cortos = [c for c in chunks if len(c.texto) < 80]
    assert len(cortos) / len(chunks) < 0.2


def test_el_chunk_id_es_unico(corpus: Corpus) -> None:
    ids = [c.chunk_id for c in chunkear(corpus.documentos)]
    assert len(ids) == len(set(ids))


# --- Recuperación ------------------------------------------------------------

def test_recupera_el_documento_que_explica_las_devoluciones(
    indice: IndiceVectorial,
    corpus: Corpus,
    dataset: dict[str, pd.DataFrame],
) -> None:
    """La pregunta que el SQL no puede responder."""
    evento = next(e for e in corpus.explicaciones
                  if e.tipo_evento == "pico_devoluciones")
    resultados = indice.buscar(
        f"por que aumentaron las devoluciones del producto {evento.product_id}",
        top_k=3,
    )
    assert evento.doc_id in {r.chunk.doc_id for r in resultados}


def test_recupera_el_documento_que_explica_una_caida(
    indice: IndiceVectorial,
    corpus: Corpus,
) -> None:
    evento = next(e for e in corpus.explicaciones
                  if e.tipo_evento == "caida_ventas")
    resultados = indice.buscar(
        f"por que cayeron las ventas del producto {evento.product_id}", top_k=3
    )
    assert evento.doc_id in {r.chunk.doc_id for r in resultados}


def test_el_filtro_por_producto_acota_los_resultados(
    indice: IndiceVectorial,
    corpus: Corpus,
) -> None:
    """Sin filtro, una consulta sobre P002 puede traer evidencia de P015.

    Citar en el informe un documento de otro producto es peor que no citar: el
    lector confía en la referencia y la referencia no corresponde.
    """
    pid = next(e.product_id for e in corpus.explicaciones)
    resultados = indice.buscar("problemas de calidad", top_k=5, product_id=pid)
    assert resultados
    assert all(r.chunk.product_id == pid for r in resultados)


def test_el_filtro_por_fecha_excluye_lo_posterior(indice: IndiceVectorial, corpus: Corpus) -> None:
    """Un documento de junio no puede explicar un análisis de enero."""
    resultados = indice.buscar("politica de devoluciones", top_k=5,
                               hasta=date(2025, 6, 30))
    assert all(r.chunk.fecha <= date(2025, 6, 30) for r in resultados)


def test_respeta_el_top_k(indice: IndiceVectorial) -> None:
    assert len(indice.buscar("devoluciones", top_k=2)) <= 2


def test_devuelve_resultados_ordenados_por_relevancia(indice: IndiceVectorial) -> None:
    resultados = indice.buscar("quiebre de stock del articulo", top_k=5)
    scores = [r.score for r in resultados]
    assert scores == sorted(scores, reverse=True)


def test_una_consulta_sin_relacion_no_rompe(indice: IndiceVectorial) -> None:
    """El índice siempre devuelve lo más cercano, aunque nada sea relevante.

    Es una limitación conocida de la búsqueda vectorial: no hay "no encontré
    nada". Por eso el filtrado de relevancia vive en la tool, no acá.
    """
    assert isinstance(indice.buscar("recetas de cocina italiana", top_k=3), list)


def test_un_filtro_sin_coincidencias_devuelve_vacio(indice: IndiceVectorial) -> None:
    assert indice.buscar("cualquier cosa", top_k=3, product_id="P999") == []


# --- Persistencia ------------------------------------------------------------

def test_el_indice_se_guarda_y_se_recupera(
    indice: IndiceVectorial,
    tmp_path: Path,
    corpus: Corpus,
) -> None:
    """Reconstruir el índice en cada arranque costaría segundos de CPU que ya
    se pagaron una vez."""
    destino = tmp_path / "indice"
    indice.guardar(destino)

    recuperado = IndiceVectorial()
    recuperado.cargar(destino)

    assert len(recuperado) == len(indice)
    consulta = "problemas de calidad en el lote"
    originales = [r.chunk.chunk_id for r in indice.buscar(consulta, top_k=3)]
    nuevos = [r.chunk.chunk_id for r in recuperado.buscar(consulta, top_k=3)]
    assert originales == nuevos


def test_cargar_un_indice_inexistente_falla_claro(tmp_path: Path) -> None:
    idx = IndiceVectorial()
    with pytest.raises(FileNotFoundError):
        idx.cargar(tmp_path / "no-existe")


def test_buscar_sin_indice_construido_falla_claro() -> None:
    idx = IndiceVectorial()
    with pytest.raises(RuntimeError, match=r"índice|indice"):
        idx.buscar("algo", top_k=3)
