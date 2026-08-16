"""Tests del corpus documental sintético.

El corpus no es decorativo: cada documento **explica un evento que el dataset ya
contiene**. El `ground_truth` sabe que P002 tuvo un pico de devoluciones por un
lote defectuoso; el corpus incluye la comunicación del proveedor que lo reporta.

Eso es lo que hace que el RAG aporte algo real. SQL puede mostrar QUÉ pasó — las
devoluciones se dispararon el 18 de enero. Solo los documentos pueden decir POR
QUÉ. Un informe que junta las dos cosas es un análisis; uno que solo tiene
números es una tabla con prosa alrededor.

Y como se sabe qué documento explica qué evento, el retrieval queda **evaluable**:
hay un ground truth de recuperación, no solo de detección.

**Los distractores son obligatorios.** Si todos los documentos explicaran algún
evento, cualquier búsqueda acertaría por descarte y la métrica de retrieval no
mediría nada.
"""

import pandas as pd
import pytest

from rag.corpus import ROLES_DISTRACTOR, Corpus, generar_corpus
from seeds.generate import DatasetConfig, generar_dataset


@pytest.fixture(scope="module")
def dataset() -> dict[str, pd.DataFrame]:
    return generar_dataset(DatasetConfig())


@pytest.fixture(scope="module")
def corpus(dataset: dict[str, pd.DataFrame]) -> Corpus:
    return generar_corpus(dataset, seed=42)


# --- Reproducibilidad --------------------------------------------------------

def test_el_corpus_es_reproducible(dataset: dict[str, pd.DataFrame]) -> None:
    a = generar_corpus(dataset, seed=42)
    b = generar_corpus(dataset, seed=42)
    assert [d.id for d in a.documentos] == [d.id for d in b.documentos]
    assert [d.texto for d in a.documentos] == [d.texto for d in b.documentos]


def test_semillas_distintas_producen_corpus_distintos(dataset: dict[str, pd.DataFrame]) -> None:
    a = generar_corpus(dataset, seed=42)
    b = generar_corpus(dataset, seed=99)
    assert [d.texto for d in a.documentos] != [d.texto for d in b.documentos]


# --- Cobertura de los eventos ------------------------------------------------

def test_cada_evento_del_ground_truth_tiene_su_documento(
    dataset: dict[str, pd.DataFrame],
    corpus: Corpus,
) -> None:
    """Sin esto, el RAG no puede explicar nada de lo que el SQL detecta."""
    eventos = len(dataset["ground_truth"])
    explicados = {e.evento_idx for e in corpus.explicaciones}
    assert len(explicados) == eventos, (
        f"{eventos - len(explicados)} eventos del ground truth quedaron sin "
        "documento que los explique"
    )


def test_cada_explicacion_apunta_a_un_documento_existente(corpus: Corpus) -> None:
    ids = {d.id for d in corpus.documentos}
    huerfanas = [e.doc_id for e in corpus.explicaciones if e.doc_id not in ids]
    assert not huerfanas, f"explicaciones que apuntan a documentos inexistentes: {huerfanas}"


def test_el_documento_menciona_el_producto_que_explica(
    dataset: dict[str, pd.DataFrame],
    corpus: Corpus,
) -> None:
    """Un documento que explica un evento de P002 tiene que nombrar a P002.

    Si no, el retrieval por producto nunca lo encontraría y la explicación sería
    inalcanzable en la práctica.
    """
    por_id = {d.id: d for d in corpus.documentos}
    for e in corpus.explicaciones:
        doc = por_id[e.doc_id]
        assert doc.product_id == e.product_id
        assert e.product_id in doc.texto, (
            f"{doc.id} explica un evento de {e.product_id} pero no lo menciona"
        )


def test_la_fecha_del_documento_es_coherente_con_el_evento(
    dataset: dict[str, pd.DataFrame],
    corpus: Corpus,
) -> None:
    """Un reporte fechado dos meses después del evento no lo explica: lo
    recuerda. La cercanía temporal es parte de la evidencia."""
    gt = dataset["ground_truth"]
    por_id = {d.id: d for d in corpus.documentos}
    for e in corpus.explicaciones:
        doc = por_id[e.doc_id]
        fecha_evento = gt.iloc[e.evento_idx]["fecha"]
        assert abs((doc.fecha - fecha_evento).days) <= 30, (
            f"{doc.id} está fechado {doc.fecha}, muy lejos del evento "
            f"{fecha_evento}"
        )


# --- Distractores ------------------------------------------------------------

def test_hay_documentos_que_no_explican_nada(corpus: Corpus) -> None:
    """Sin ruido, cualquier búsqueda acierta por descarte.

    Un corpus donde todo documento es relevante no mide la calidad del
    retrieval: mide que el índice devuelve algo.
    """
    explicativos = {e.doc_id for e in corpus.explicaciones}
    distractores = [d for d in corpus.documentos if d.id not in explicativos]
    assert len(distractores) >= len(explicativos), (
        "el corpus tiene menos distractores que documentos explicativos: "
        "el retrieval sería trivial"
    )


def test_los_distractores_son_verosimiles(corpus: Corpus) -> None:
    """Un distractor obvio tampoco sirve: tiene que ser el tipo de documento
    que un buscador semántico podría confundir."""
    explicativos = {e.doc_id for e in corpus.explicaciones}
    distractores = [d for d in corpus.documentos if d.id not in explicativos]
    assert {d.tipo for d in distractores} & set(ROLES_DISTRACTOR)
    assert all(len(d.texto) > 200 for d in distractores)


# --- Estructura de los documentos --------------------------------------------

def test_todo_documento_tiene_metadata_completa(corpus: Corpus) -> None:
    for d in corpus.documentos:
        assert d.id and d.tipo and d.titulo and d.texto
        assert d.fecha is not None
        assert d.seccion


def test_los_identificadores_son_unicos(corpus: Corpus) -> None:
    ids = [d.id for d in corpus.documentos]
    assert len(ids) == len(set(ids))


def test_los_documentos_tienen_longitud_util(corpus: Corpus) -> None:
    """Ni tan cortos que no aporten contexto ni tan largos que un chunk pierda
    el hilo."""
    for d in corpus.documentos:
        assert 200 <= len(d.texto) <= 4000, f"{d.id}: {len(d.texto)} caracteres"


def test_el_corpus_tiene_volumen_suficiente(corpus: Corpus) -> None:
    assert len(corpus.documentos) >= 30


def test_incluye_documentos_de_politica_general(corpus: Corpus) -> None:
    """Políticas y fichas: son los que responden preguntas que no son sobre un
    evento puntual."""
    tipos = {d.tipo for d in corpus.documentos}
    assert "politica" in tipos


# --- El prompt no puede contener respuestas del examen ------------------------

def test_ningun_doc_id_de_los_ejemplos_del_prompt_existe_en_el_corpus(corpus: Corpus) -> None:
    """La regla 5 del método del proyecto, aplicada al sintetizador.

        "El conjunto de evaluación no puede contaminarse con el prompt. Un caso
        de prueba que aparece textualmente como ejemplo del prompt no mide nada."

    Medido el 2026-08-12: los ejemplos de `synthesizer.SISTEMA` usaban
    `doc_prov_009` y `doc_promo_004`, y los dos existían de verdad —explican a
    P030 y a P031, que son dos de los quince casos del eval—. Los dos casos
    pasaron citando exactamente esos documentos, y no hay forma de saber si los
    citaron porque el RAG se los trajo o porque los tenían escritos adelante.

    El daño no es que el modelo copie un identificador: cuando copia uno que no
    está en la evidencia, el guardrail lo detecta y borra la cita. El daño es
    cuando el identificador copiado SÍ corresponde al caso, porque entonces
    parece un acierto y no se puede distinguir de uno.

    Este test es determinístico y vale para siempre: si alguien agrega un
    ejemplo con un identificador real, falla acá.
    """
    import re

    from agent.nodes.synthesizer import SISTEMA

    del_prompt = set(re.findall(r"doc_[a-z]+_[A-Za-z0-9]+", SISTEMA))
    reales = {d.id for d in corpus.documentos}

    assert del_prompt, "el prompt debe mostrar el formato de una cita"
    assert not (del_prompt & reales), (
        f"los ejemplos del prompt usan documentos que existen en el corpus: "
        f"{sorted(del_prompt & reales)}. Un caso del eval cuya respuesta está "
        f"en el prompt no mide nada."
    )


def test_los_ejemplos_del_prompt_conservan_el_formato_de_un_doc_id(corpus: Corpus) -> None:
    """La contraprueba: no alcanza con que sean falsos, tienen que seguir
    pareciéndose a los reales. El ejemplo enseña la FORMA de la cita, y si deja
    de parecerse el modelo pierde la referencia de qué se espera que escriba."""
    import re

    from agent.nodes.synthesizer import SISTEMA

    prefijos_reales = {d.id.rsplit("_", 1)[0] for d in corpus.documentos}
    del_prompt = set(re.findall(r"doc_[a-z]+_[A-Za-z0-9]+", SISTEMA))

    assert all(x.rsplit("_", 1)[0] in prefijos_reales for x in del_prompt), (
        f"los ejemplos deben usar los mismos prefijos que el corpus real "
        f"({sorted(prefijos_reales)}) y diferenciarse solo en el sufijo"
    )
