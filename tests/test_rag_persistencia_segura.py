"""Contrato de persistencia segura y verificable del indice RAG."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from rag.indice import (
    DIMENSION_EMBEDDINGS,
    MODELO_EMBEDDINGS,
    SCHEMA_INDICE,
    Chunk,
    IndiceVectorial,
)


class _IndiceFaissFalso:
    def __init__(self, dimension: int = DIMENSION_EMBEDDINGS, total: int = 1) -> None:
        self.d = dimension
        self.ntotal = total


@pytest.fixture
def faiss_falso(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    modulo = SimpleNamespace()
    modulo.lecturas = 0

    def write_index(indice: _IndiceFaissFalso, ruta: str) -> None:
        Path(ruta).write_bytes(f"faiss:{indice.d}:{indice.ntotal}".encode())

    def read_index(ruta: str) -> _IndiceFaissFalso:
        modulo.lecturas += 1
        _, dimension, total = Path(ruta).read_text().split(":")
        return _IndiceFaissFalso(int(dimension), int(total))

    modulo.write_index = write_index
    modulo.read_index = read_index
    monkeypatch.setitem(sys.modules, "faiss", modulo)
    return modulo


def _chunk() -> Chunk:
    return Chunk(
        chunk_id="DOC-1#0",
        doc_id="DOC-1",
        texto="Evidencia verificable",
        tipo="informe",
        titulo="Calidad",
        seccion="Hallazgos",
        fecha=date(2026, 1, 15),
        product_id="P001",
    )


def _indice() -> IndiceVectorial:
    indice = IndiceVectorial()
    indice._indice = _IndiceFaissFalso()
    indice._chunks = [_chunk()]
    return indice


def test_guardar_usa_json_y_metadatos_verificables(
    tmp_path: Path, faiss_falso: SimpleNamespace
) -> None:
    destino = tmp_path / "indice"

    _indice().guardar(destino)

    assert (destino / "chunks.json").is_file()
    assert not (destino / "chunks.pkl").exists()
    datos = json.loads((destino / "chunks.json").read_text(encoding="utf-8"))
    assert datos["schema_version"] == SCHEMA_INDICE
    assert datos["chunks"][0]["fecha"] == "2026-01-15"

    meta = json.loads((destino / "meta.json").read_text(encoding="utf-8"))
    assert meta == {
        "schema_version": SCHEMA_INDICE,
        "modelo": MODELO_EMBEDDINGS,
        "dimension": DIMENSION_EMBEDDINGS,
        "chunks": 1,
        "checksums": {
            nombre: hashlib.sha256((destino / nombre).read_bytes()).hexdigest()
            for nombre in ("vectores.faiss", "chunks.json")
        },
    }


def test_guardar_y_cargar_preserva_chunks_sin_ejecutar_pickle(
    tmp_path: Path, faiss_falso: SimpleNamespace
) -> None:
    destino = tmp_path / "indice"
    _indice().guardar(destino)

    recuperado = IndiceVectorial().cargar(destino)

    assert len(recuperado) == 1
    assert recuperado._chunks == [_chunk()]


@pytest.mark.parametrize(
    ("campo", "valor", "mensaje"),
    [
        ("schema_version", 999, "version|schema"),
        ("modelo", "otro/modelo", "modelo"),
        ("dimension", 12, "dimension"),
    ],
)
def test_cargar_rechaza_drift_de_metadatos_antes_de_abrir_faiss(
    tmp_path: Path,
    faiss_falso: SimpleNamespace,
    campo: str,
    valor: Any,
    mensaje: str,
) -> None:
    destino = tmp_path / "indice"
    _indice().guardar(destino)
    meta = json.loads((destino / "meta.json").read_text(encoding="utf-8"))
    meta[campo] = valor
    (destino / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(RuntimeError, match=mensaje):
        IndiceVectorial().cargar(destino)

    assert faiss_falso.lecturas == 0


@pytest.mark.parametrize("archivo", ["chunks.json", "vectores.faiss"])
def test_cargar_rechaza_archivos_manipulados_antes_de_abrir_faiss(
    tmp_path: Path, faiss_falso: SimpleNamespace, archivo: str
) -> None:
    destino = tmp_path / "indice"
    _indice().guardar(destino)
    with (destino / archivo).open("ab") as f:
        f.write(b"manipulado")

    with pytest.raises(RuntimeError, match="checksum"):
        IndiceVectorial().cargar(destino)

    assert faiss_falso.lecturas == 0


@pytest.mark.parametrize(
    ("dimension", "total", "mensaje"),
    [
        (12, 1, "dimension"),
        (DIMENSION_EMBEDDINGS, 2, "cantidad|chunks"),
    ],
)
def test_cargar_valida_dimension_y_cantidad_reales_del_indice(
    tmp_path: Path,
    faiss_falso: SimpleNamespace,
    dimension: int,
    total: int,
    mensaje: str,
) -> None:
    destino = tmp_path / "indice"
    _indice().guardar(destino)
    (destino / "vectores.faiss").write_text(
        f"faiss:{dimension}:{total}", encoding="utf-8"
    )
    meta = json.loads((destino / "meta.json").read_text(encoding="utf-8"))
    meta["checksums"]["vectores.faiss"] = hashlib.sha256(
        (destino / "vectores.faiss").read_bytes()
    ).hexdigest()
    (destino / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(RuntimeError, match=mensaje):
        IndiceVectorial().cargar(destino)


def test_indice_pickle_legado_se_rechaza_con_migracion_explicita(
    tmp_path: Path, faiss_falso: SimpleNamespace
) -> None:
    destino = tmp_path / "indice"
    destino.mkdir()
    (destino / "vectores.faiss").write_bytes(b"faiss legado")
    (destino / "chunks.pkl").write_bytes(b"esto no debe deserializarse")
    (destino / "meta.json").write_text(
        json.dumps({"modelo": MODELO_EMBEDDINGS, "chunks": 1}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match=r"legado|reconstru"):
        IndiceVectorial().cargar(destino)

    assert faiss_falso.lecturas == 0
