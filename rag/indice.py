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
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any, TypeGuard

import numpy as np

from rag.corpus import Documento

MODELO_EMBEDDINGS = "intfloat/multilingual-e5-small"
DIMENSION_EMBEDDINGS = 384
SCHEMA_INDICE = 1
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


class IndiceIncompatibleError(RuntimeError):
    """El artefacto existe, pero no cumple el contrato seguro vigente."""


_CAMPOS_CHUNK = {
    "chunk_id",
    "doc_id",
    "texto",
    "tipo",
    "titulo",
    "seccion",
    "fecha",
    "product_id",
}


def _serializar_chunk(chunk: Chunk) -> dict[str, str | None]:
    return {
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "texto": chunk.texto,
        "tipo": chunk.tipo,
        "titulo": chunk.titulo,
        "seccion": chunk.seccion,
        "fecha": chunk.fecha.isoformat(),
        "product_id": chunk.product_id,
    }


def _deserializar_chunk(datos: object, posicion: int) -> Chunk:
    if not isinstance(datos, dict) or set(datos) != _CAMPOS_CHUNK:
        raise IndiceIncompatibleError(
            f"chunk {posicion} no cumple el schema {SCHEMA_INDICE}"
        )

    campos_texto = _CAMPOS_CHUNK - {"fecha", "product_id"}
    if any(not isinstance(datos[campo], str) for campo in campos_texto):
        raise IndiceIncompatibleError(
            f"chunk {posicion} contiene campos de texto invalidos"
        )
    product_id = datos["product_id"]
    if product_id is not None and not isinstance(product_id, str):
        raise IndiceIncompatibleError(
            f"chunk {posicion} contiene un product_id invalido"
        )
    fecha_cruda = datos["fecha"]
    if not isinstance(fecha_cruda, str):
        raise IndiceIncompatibleError(f"chunk {posicion} contiene una fecha invalida")
    try:
        fecha = date.fromisoformat(fecha_cruda)
    except ValueError as error:
        raise IndiceIncompatibleError(
            f"chunk {posicion} contiene una fecha invalida: {fecha_cruda!r}"
        ) from error

    return Chunk(
        chunk_id=datos["chunk_id"],
        doc_id=datos["doc_id"],
        texto=datos["texto"],
        tipo=datos["tipo"],
        titulo=datos["titulo"],
        seccion=datos["seccion"],
        fecha=fecha,
        product_id=product_id,
    )


def _sha256(archivo: Path) -> str:
    digest = sha256()
    with archivo.open("rb") as contenido:
        for bloque in iter(lambda: contenido.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def _leer_json(archivo: Path, descripcion: str) -> object:
    try:
        return json.loads(archivo.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise IndiceIncompatibleError(f"falta {descripcion}: {archivo.name}") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IndiceIncompatibleError(
            f"{descripcion} no contiene JSON valido: {archivo.name}"
        ) from error


def _entero_estricto(valor: object) -> TypeGuard[int]:
    return isinstance(valor, int) and not isinstance(valor, bool)


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

        if self._indice is None:
            raise RuntimeError("no se puede guardar un indice que no fue construido")
        dimension = getattr(self._indice, "d", None)
        if dimension != DIMENSION_EMBEDDINGS:
            raise IndiceIncompatibleError(
                "la dimension del indice no coincide con el modelo configurado: "
                f"esperada {DIMENSION_EMBEDDINGS}, recibida {dimension!r}"
            )
        total = getattr(self._indice, "ntotal", None)
        if total != len(self._chunks):
            raise IndiceIncompatibleError(
                "la cantidad de vectores no coincide con la cantidad de chunks: "
                f"{total!r} contra {len(self._chunks)}"
            )

        destino = Path(destino)
        destino.mkdir(parents=True, exist_ok=True)
        archivo_vectores = destino / "vectores.faiss"
        archivo_chunks = destino / "chunks.json"
        faiss.write_index(self._indice, str(archivo_vectores))
        archivo_chunks.write_text(
            json.dumps(
                {
                    "schema_version": SCHEMA_INDICE,
                    "chunks": [_serializar_chunk(chunk) for chunk in self._chunks],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        meta = {
            "schema_version": SCHEMA_INDICE,
            "modelo": MODELO_EMBEDDINGS,
            "dimension": DIMENSION_EMBEDDINGS,
            "chunks": len(self._chunks),
            "checksums": {
                "vectores.faiss": _sha256(archivo_vectores),
                "chunks.json": _sha256(archivo_chunks),
            },
        }
        (destino / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (destino / "chunks.pkl").unlink(missing_ok=True)
        return destino

    def cargar(self, origen: str | Path) -> IndiceVectorial:
        import faiss

        origen = Path(origen)
        archivo = origen / "vectores.faiss"
        if not archivo.exists():
            raise FileNotFoundError(f"no hay un índice en {origen}")

        archivo_chunks = origen / "chunks.json"
        archivo_meta = origen / "meta.json"
        if (origen / "chunks.pkl").exists() and not archivo_chunks.exists():
            raise IndiceIncompatibleError(
                "el indice usa el formato legado chunks.pkl, que no se carga porque "
                "pickle puede ejecutar codigo; reconstruí el indice con 'rag-build'"
            )

        meta = _leer_json(archivo_meta, "la metadata del indice")
        if not isinstance(meta, dict):
            raise IndiceIncompatibleError("la metadata del indice debe ser un objeto JSON")
        schema = meta.get("schema_version")
        if schema != SCHEMA_INDICE:
            raise IndiceIncompatibleError(
                "version de schema del indice incompatible: "
                f"esperada {SCHEMA_INDICE}, recibida {schema!r}; reconstruí el indice"
            )
        modelo = meta.get("modelo")
        if modelo != MODELO_EMBEDDINGS:
            raise IndiceIncompatibleError(
                "modelo de embeddings incompatible: "
                f"esperado {MODELO_EMBEDDINGS!r}, recibido {modelo!r}"
            )
        dimension = meta.get("dimension")
        if dimension != DIMENSION_EMBEDDINGS:
            raise IndiceIncompatibleError(
                "dimension de embeddings incompatible: "
                f"esperada {DIMENSION_EMBEDDINGS}, recibida {dimension!r}"
            )
        cantidad = meta.get("chunks")
        if not _entero_estricto(cantidad) or cantidad < 0:
            raise IndiceIncompatibleError("la cantidad de chunks de la metadata es invalida")

        checksums = meta.get("checksums")
        nombres = ("vectores.faiss", "chunks.json")
        if not isinstance(checksums, dict) or set(checksums) != set(nombres):
            raise IndiceIncompatibleError("la metadata no declara todos los checksums")
        for nombre in nombres:
            esperado = checksums[nombre]
            if not isinstance(esperado, str) or len(esperado) != 64:
                raise IndiceIncompatibleError(f"checksum invalido para {nombre}")
            archivo_contenido = origen / nombre
            try:
                recibido = _sha256(archivo_contenido)
            except FileNotFoundError as error:
                raise IndiceIncompatibleError(
                    f"falta un archivo del indice: {nombre}"
                ) from error
            if recibido != esperado:
                raise IndiceIncompatibleError(
                    f"checksum invalido para {nombre}: el indice fue modificado o esta corrupto"
                )

        payload = _leer_json(archivo_chunks, "los chunks del indice")
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_INDICE:
            raise IndiceIncompatibleError("los chunks no cumplen el schema del indice")
        chunks_crudos = payload.get("chunks")
        if not isinstance(chunks_crudos, list):
            raise IndiceIncompatibleError("el archivo de chunks no contiene una lista")
        if len(chunks_crudos) != cantidad:
            raise IndiceIncompatibleError(
                "la cantidad de chunks no coincide con la metadata: "
                f"{len(chunks_crudos)} contra {cantidad}"
            )
        chunks = [
            _deserializar_chunk(datos, posicion)
            for posicion, datos in enumerate(chunks_crudos)
        ]

        indice = faiss.read_index(str(archivo))
        dimension_real = getattr(indice, "d", None)
        if dimension_real != dimension:
            raise IndiceIncompatibleError(
                "la dimension real del indice FAISS no coincide con la metadata: "
                f"{dimension_real!r} contra {dimension}"
            )
        total_real = getattr(indice, "ntotal", None)
        if total_real != cantidad:
            raise IndiceIncompatibleError(
                "la cantidad real de vectores no coincide con los chunks: "
                f"{total_real!r} contra {cantidad}"
            )

        self._indice = indice
        self._chunks = chunks
        return self
