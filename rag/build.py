"""Construcción y carga del índice documental.

El índice se construye una vez y se persiste. Recalcular los embeddings en cada
arranque costaría segundos de CPU que ya se pagaron, y en una máquina sin GPU
esos segundos importan.

Se ejecuta con:  .\\tasks.ps1 rag-build
"""

from __future__ import annotations

from pathlib import Path

from rag.corpus import generar_corpus
from rag.indice import IndiceVectorial, chunkear
from seeds.generate import DatasetConfig, generar_dataset

DESTINO = Path(__file__).resolve().parent.parent / "data" / "indice"

_cache: IndiceVectorial | None = None


def construir(destino: Path = DESTINO, verbose: bool = True) -> IndiceVectorial:
    """Genera el corpus, calcula los embeddings y guarda el índice."""
    if verbose:
        print("  Generando el dataset y el corpus documental...")
    dataset = generar_dataset(DatasetConfig())
    corpus = generar_corpus(dataset)

    chunks = chunkear(corpus.documentos)
    if verbose:
        print(f"  {len(corpus.documentos)} documentos -> {len(chunks)} chunks")
        print("  Calculando embeddings (CPU, puede tardar)...")

    indice = IndiceVectorial().construir(chunks)
    indice.guardar(destino)

    if verbose:
        explicativos = len(corpus.explicaciones)
        print(f"  Índice guardado en {destino}")
        print(f"  {explicativos} documentos explican eventos del ground truth; "
              f"{len(corpus.documentos) - explicativos} son distractores")
    return indice


def cargar_indice(destino: Path = DESTINO) -> IndiceVectorial | None:
    """Carga el índice desde disco, o None si todavía no fue construido.

    Devolver None en vez de fallar es deliberado: sin índice el sistema degrada
    a un análisis sin evidencia documental, que sigue siendo un informe válido.
    Tirar una excepción convertiría una capacidad ausente en un error fatal.

    Esa degradación aplica solamente cuando el artefacto no existe. Un índice
    presente pero incompatible, corrupto o con checksum inválido sí falla: tomar
    manipulación o drift por "RAG opcional" ocultaría un problema operativo y,
    peor, podría asociar evidencia documental a los vectores equivocados. Los
    índices legados con ``chunks.pkl`` se migran reconstruyéndolos; nunca se
    deserializan.
    """
    global _cache
    if _cache is not None:
        return _cache
    try:
        _cache = IndiceVectorial().cargar(destino)
        return _cache
    except FileNotFoundError:
        return None


if __name__ == "__main__":
    construir()
