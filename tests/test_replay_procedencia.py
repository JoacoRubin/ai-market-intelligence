"""Procedencia reproducible del manifiesto del replay."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from replay.captura import Captura, Manifiesto
from replay.procedencia import capturar_procedencia

AHORA = datetime(2026, 8, 28, 12, 0, 0)


def test_captura_commit_dirty_state_lock_e_indice(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_bytes(b"lock reproducible")
    indice = tmp_path / "data" / "indice"
    indice.mkdir(parents=True)
    (indice / "meta.json").write_bytes(b'{"modelo":"demo"}')
    (indice / "vectores.faiss").write_bytes(b"vectores")

    comandos = {
        ("rev-parse", "HEAD"): "a" * 40,
        ("status", "--porcelain"): " M replay/captura.py\n",
    }

    procedencia = capturar_procedencia(
        tmp_path,
        ejecutar_git=lambda *args: comandos[args],
    )

    assert procedencia.commit == "a" * 40
    assert procedencia.arbol_limpio is False
    assert procedencia.uv_lock_sha256 == hashlib.sha256(b"lock reproducible").hexdigest()
    assert procedencia.indice_archivos_sha256 == {
        "meta.json": hashlib.sha256(b'{"modelo":"demo"}').hexdigest(),
        "vectores.faiss": hashlib.sha256(b"vectores").hexdigest(),
    }


def test_la_procedencia_degrada_a_none_si_git_o_artefactos_no_estan_disponibles(
    tmp_path: Path,
) -> None:
    def git_no_disponible(*_args: str) -> str:
        raise OSError("git no está instalado")

    procedencia = capturar_procedencia(tmp_path, ejecutar_git=git_no_disponible)

    assert procedencia.commit is None
    assert procedencia.arbol_limpio is None
    assert procedencia.uv_lock_sha256 is None
    assert procedencia.indice_archivos_sha256 == {}


def test_el_manifiesto_serializa_la_procedencia_sin_inventarla(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_bytes(b"lock")
    procedencia = capturar_procedencia(
        tmp_path,
        ejecutar_git=lambda *args: "b" * 40 if args[0] == "rev-parse" else "",
    )
    captura = Captura(
        id="out-03",
        consulta="Borrá todo",
        capturada_en=AHORA,
        modelo_llm="qwen3:4b",
    )

    manifiesto = Manifiesto.desde_capturas(
        [captura], capturado_en=AHORA, procedencia=procedencia
    )

    assert manifiesto.procedencia == procedencia
    assert manifiesto.procedencia.arbol_limpio is True


def test_un_manifiesto_legado_puede_declarar_procedencia_desconocida() -> None:
    captura = Captura(
        id="out-03",
        consulta="Borrá todo",
        capturada_en=AHORA,
        modelo_llm="qwen3:4b",
    )

    manifiesto = Manifiesto.desde_capturas([captura], capturado_en=AHORA)

    assert manifiesto.procedencia is None
