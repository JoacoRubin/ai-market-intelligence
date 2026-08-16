"""Tests del modo offline del cargador de embeddings.

`sentence-transformers` consulta el Hub de Hugging Face al cargar el modelo,
aunque después lo lea del cache local. Eso tiene dos costos:

1. Contradice el principio del proyecto de correr sin servicios de terceros.
2. Sin conexión, esa consulta puede colgarse antes de caer al cache — o sea,
   justo en una demo con wifi malo, que es cuando más importa.

Lo que se prueba acá es la política: offline por defecto, respetando una
decisión explícita en contra, y con un error que diga cómo se arregla.

No se carga el modelo real: eso tarda unos veinte segundos y ya está cubierto
por los tests marcados `rag`.
"""

from __future__ import annotations

import pytest

from rag.indice import _activar_modo_offline, _mensaje_sin_cache


@pytest.fixture(autouse=True)
def sin_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cada test arranca sin la variable, para no depender del entorno real."""
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)


def test_activa_el_modo_offline_cuando_nadie_lo_configuro(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    _activar_modo_offline()

    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_respeta_una_decision_explicita_en_contra(monkeypatch: pytest.MonkeyPatch) -> None:
    """Quien pone HF_HUB_OFFLINE=0 quiere descargar. No se le pisa.

    Es el caso de la primera instalación: sin esta salida, una clonación nueva
    no tendría forma de bajar el modelo.
    """
    import os

    monkeypatch.setenv("HF_HUB_OFFLINE", "0")

    _activar_modo_offline()

    assert os.environ["HF_HUB_OFFLINE"] == "0"


def test_no_pisa_un_offline_ya_declarado(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")

    _activar_modo_offline()

    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_el_error_sin_cache_dice_como_arreglarlo() -> None:
    """Un fallo de red convertido en "no está en cache" sin instrucciones deja
    a la persona peor que antes: ahora falla y encima no sabe por qué.
    """
    mensaje = _mensaje_sin_cache(OSError("no se encontró el modelo"))

    assert "intfloat/multilingual-e5-small" in mensaje
    assert "rag-descargar" in mensaje
    assert "HF_HUB_OFFLINE" in mensaje


def test_el_error_conserva_la_causa_original() -> None:
    """Sin el error de abajo, el diagnóstico real se pierde."""
    mensaje = _mensaje_sin_cache(OSError("connection refused"))

    assert "connection refused" in mensaje


def test_el_comando_que_sugiere_el_error_existe_en_tasks() -> None:
    """Mandar a alguien a correr un comando inexistente es peor que callarse.

    Ya pasó una vez en este proyecto con el sitio del replay, y por eso el
    chequeo se repite en cada lugar que le sugiere un comando a una persona.
    """
    import re
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent
    mensaje = _mensaje_sin_cache(OSError("x"))
    tasks = (raiz / "tasks.ps1").read_text(encoding="utf-8-sig")
    declarados = set(re.findall(r'^\s*"([a-z-]+)"\s*\{', tasks, re.M))

    sugeridos = set(re.findall(r"tasks\.ps1 ([a-z-]+)", mensaje))
    assert sugeridos, "el mensaje dejó de sugerir un comando"
    assert sugeridos <= declarados, f"tasks.ps1 no declara {sugeridos - declarados}"
