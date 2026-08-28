"""Procedencia verificable de una captura del replay.

El manifiesto no intenta demostrar que dos ejecuciones del LLM sean idénticas:
eso sería falso. Sí registra qué código, lockfile e índice local estaban
presentes, que es la información necesaria para explicar una diferencia.
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

EjecutorGit = Callable[..., str]


class ProcedenciaReplay(BaseModel):
    """Identidad de los artefactos disponibles al iniciar la captura.

    Los campos de Git y lock son opcionales porque el harness también puede
    ejecutarse desde un source archive. ``None`` dice "no se pudo medir"; una
    cadena inventada como ``unknown`` no sería verificable.
    """

    commit: str | None = None
    arbol_limpio: bool | None = None
    uv_lock_sha256: str | None = None
    indice_archivos_sha256: dict[str, str] = Field(default_factory=dict)


def _sha256(ruta: Path) -> str:
    digest = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def _git(raiz: Path, *args: str) -> str:
    resultado = subprocess.run(
        ["git", *args],
        cwd=raiz,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return resultado.stdout


def capturar_procedencia(
    raiz: Path,
    *,
    ejecutar_git: EjecutorGit | None = None,
) -> ProcedenciaReplay:
    """Lee procedencia sin impedir una captura si un artefacto no existe."""
    raiz = raiz.resolve()
    runner = ejecutar_git or (lambda *args: _git(raiz, *args))

    commit: str | None = None
    arbol_limpio: bool | None = None
    try:
        commit = runner("rev-parse", "HEAD").strip() or None
        arbol_limpio = not bool(runner("status", "--porcelain").strip())
    except (OSError, subprocess.SubprocessError):
        # Un source archive puede no traer .git. Se conserva la ausencia en vez
        # de abortar una captura que sigue siendo útil y honesta.
        pass

    lock = raiz / "uv.lock"
    lock_sha = _sha256(lock) if lock.is_file() else None

    indice = raiz / "data" / "indice"
    hashes_indice = {
        ruta.relative_to(indice).as_posix(): _sha256(ruta)
        for ruta in sorted(indice.rglob("*"))
        if ruta.is_file()
    } if indice.is_dir() else {}

    return ProcedenciaReplay(
        commit=commit,
        arbol_limpio=arbol_limpio,
        uv_lock_sha256=lock_sha,
        indice_archivos_sha256=hashes_indice,
    )
