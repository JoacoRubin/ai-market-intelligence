"""Chunking, embeddings e índice vectorial con FAISS.

**Modelo de embeddings: `intfloat/multilingual-e5-small`.** Elegido midiendo,
no por reputación. Contra `paraphrase-multilingual-MiniLM-L12-v2` sobre una
consulta real del dominio:

    consulta: "por que se dispararon las devoluciones del producto"

    MiniLM  ->  #1 proveedor  #2 ACTA DE REUNIÓN (ruido)  #3 stock
    e5      ->  #1 proveedor  #2 stock  #3 política  #4 acta (ruido, último)

Con `top_k=3`, MiniLM mete un distractor en el contexto del sintetizador. e5 no.
Y encima es más rápido: 52 ms/texto contra 62 en esta CPU.

**e5 exige prefijos** (`query:` y `passage:`). Sin ellos rinde por debajo de lo
que puede: en la primera medición, mal configurado, ordenaba peor. Comparar un
modelo mal configurado contra otro bien configurado no es medir.

**Consecuencia de usar e5: sus similitudes viven comprimidas** entre 0,80 y 0,90.
No sirven como umbral absoluto de relevancia — "score > 0,7" incluiría cualquier
cosa. El corte por relevancia se hace por posición relativa, en la tool.

El índice usa producto interno sobre vectores normalizados, que equivale a
similitud coseno.
"""

from __future__ import annotations

import json
import os
import pickle
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from rag.corpus import Documento

MODELO_EMBEDDINGS = "intfloat/multilingual-e5-small"
PREFIJO_CONSULTA = "query: "
PREFIJO_PASAJE = "passage: "

MAX_CHARS_CHUNK = 900
SOLAPAMIENTO = 150

_modelo = None


def _activar_modo_offline() -> None:
    """Evita que la carga del modelo consulte el Hub de Hugging Face.

    `sentence-transformers` le pega a la red para ver si el modelo cambió, y
    recién después lee el cache local. Eso contradice el principio de correr sin
    servicios de terceros, y sin conexión esa consulta puede colgarse antes de
    caer al cache — justo en una demo con wifi malo, que es cuando importa.

    Se usa `setdefault` y no una asignación: quien exporte `HF_HUB_OFFLINE=0`
    está pidiendo descargar, y es la única salida que tiene una instalación
    nueva para bajar el modelo la primera vez.

    **Tiene que correr antes de que se importe `huggingface_hub`**: la librería
    lee esta variable al importarse y la congela en una constante. Por eso se
    invoca al importar este módulo y no dentro de `obtener_modelo()`. La garantía
    fuerte igual la da `tasks.ps1`, que la exporta antes de arrancar Python.
    """
    os.environ.setdefault("HF_HUB_OFFLINE", "1")


_activar_modo_offline()


def _mensaje_sin_cache(error: Exception) -> str:
    """Traduce un fallo de carga en modo offline a algo accionable.

    En offline, un modelo ausente y un fallo de red se ven casi igual. Sin esta
    traducción el mensaje habla de conexiones y de rutas de cache, y no dice lo
    único que hace falta saber: que el modelo no está bajado todavía.
    """
    return (
        f"No se pudo cargar '{MODELO_EMBEDDINGS}' desde el cache local y el modo "
        f"offline está activo, así que no se intentó descargarlo.\n"
        f"Si es la primera vez en esta máquina, bajalo una vez con "
        f"'.\\tasks.ps1 rag-descargar' (o exportando HF_HUB_OFFLINE=0).\n"
        f"Causa original: {type(error).__name__}: {error}"
    )


def obtener_modelo() -> Any:
    """Carga perezosa y única del modelo.

    Cargarlo tarda unos segundos; hacerlo una sola vez por proceso evita
    pagarlo en cada búsqueda.
    """
    global _modelo
    if _modelo is None:
        import warnings

        warnings.filterwarnings("ignore")
        from sentence_transformers import SentenceTransformer

        try:
            _modelo = SentenceTransformer(MODELO_EMBEDDINGS, device="cpu")
        except Exception as e:
            raise RuntimeError(_mensaje_sin_cache(e)) from e
    return _modelo


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    texto: str
    tipo: str
    titulo: str
    seccion: str
    fecha: date
    product_id: str | None = None


@dataclass
class Resultado:
    chunk: Chunk
    score: float


def _partir(texto: str) -> list[str]:
    """Corta por párrafos y agrupa hasta el tamaño máximo.

    Se respetan los límites de párrafo porque son los límites naturales del
    sentido. Un corte a mitad de idea produce un chunk que recupera bien y
    explica mal: entra al contexto sin la parte que lo hacía comprensible.
    """
    parrafos = [p.strip() for p in texto.split("\n\n") if p.strip()]
    if not parrafos:
        return [texto.strip()] if texto.strip() else []

    partes: list[str] = []
    actual = ""
    for p in parrafos:
        if actual and len(actual) + len(p) + 2 > MAX_CHARS_CHUNK:
            partes.append(actual)
            # Solapamiento: la cola del chunk anterior encabeza el siguiente,
            # para que una idea partida al medio siga siendo recuperable.
            cola = actual[-SOLAPAMIENTO:]
            actual = f"{cola}\n\n{p}" if SOLAPAMIENTO else p
        else:
            actual = f"{actual}\n\n{p}" if actual else p
    if actual:
        partes.append(actual)
    return partes


def chunkear(documentos: list[Documento]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for doc in documentos:
        for i, parte in enumerate(_partir(doc.texto)):
            chunks.append(Chunk(
                chunk_id=f"{doc.id}#{i}", doc_id=doc.id, texto=parte,
                tipo=doc.tipo, titulo=doc.titulo, seccion=doc.seccion,
                fecha=doc.fecha, product_id=doc.product_id,
            ))
    return chunks


class IndiceVectorial:
    """Índice FAISS sobre los chunks del corpus."""

    def __init__(self) -> None:
        # `Any` y no un tipo de faiss: la librería no publica tipos, así que
        # cualquier anotación más precisa sería una ficción no verificada.
        self._indice: Any = None
        self._chunks: list[Chunk] = []

    def __len__(self) -> int:
        return len(self._chunks)

    def construir(self, chunks: list[Chunk]) -> IndiceVectorial:
        import faiss

        self._chunks = list(chunks)
        vectores = obtener_modelo().encode(
            [PREFIJO_PASAJE + c.texto for c in self._chunks],
            normalize_embeddings=True, show_progress_bar=False,
        ).astype(np.float32)

        # IndexFlatIP sobre vectores normalizados == similitud coseno. Con este
        # volumen de documentos, un índice exacto es más simple y más rápido que
        # cualquier aproximación.
        self._indice = faiss.IndexFlatIP(vectores.shape[1])
        self._indice.add(vectores)
        return self

    def buscar(
        self,
        consulta: str,
        top_k: int = 5,
        product_id: str | None = None,
        desde: date | None = None,
        hasta: date | None = None,
    ) -> list[Resultado]:
        """Recupera los chunks más cercanos, con filtros de metadata.

        Los filtros se aplican DESPUÉS de la búsqueda vectorial, sobre un
        conjunto ampliado. Con este volumen es más simple que mantener un índice
        por partición, y evita el caso en que el filtro deja el resultado vacío
        porque los k primeros vectores eran todos de otro producto.
        """
        if self._indice is None:
            raise RuntimeError(
                "el índice no fue construido: llamá a construir() o cargar()"
            )
        if not self._chunks:
            return []

        vector = obtener_modelo().encode(
            [PREFIJO_CONSULTA + consulta],
            normalize_embeddings=True, show_progress_bar=False,
        ).astype(np.float32)

        hay_filtros = product_id is not None or desde is not None or hasta is not None
        amplitud = min(len(self._chunks), top_k * 10 if hay_filtros else top_k)
        scores, indices = self._indice.search(vector, amplitud)

        salida: list[Resultado] = []
        for score, i in zip(scores[0], indices[0], strict=True):
            if i < 0:
                continue
            c = self._chunks[i]
            if product_id is not None and c.product_id != product_id:
                continue
            if desde is not None and c.fecha < desde:
                continue
            if hasta is not None and c.fecha > hasta:
                continue
            salida.append(Resultado(chunk=c, score=float(score)))
            if len(salida) == top_k:
                break
        return salida

    # --- persistencia ----------------------------------------------------

    def guardar(self, destino: str | Path) -> Path:
        import faiss

        destino = Path(destino)
        destino.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._indice, str(destino / "vectores.faiss"))
        with open(destino / "chunks.pkl", "wb") as f:
            pickle.dump(self._chunks, f)
        (destino / "meta.json").write_text(
            json.dumps({"modelo": MODELO_EMBEDDINGS, "chunks": len(self._chunks)},
                       indent=2),
            encoding="utf-8",
        )
        return destino

    def cargar(self, origen: str | Path) -> IndiceVectorial:
        import faiss

        origen = Path(origen)
        archivo = origen / "vectores.faiss"
        if not archivo.exists():
            raise FileNotFoundError(f"no hay un índice en {origen}")

        self._indice = faiss.read_index(str(archivo))
        with open(origen / "chunks.pkl", "rb") as f:
            self._chunks = pickle.load(f)
        return self
