"""Tests del contrato del puerto `ClienteLLM` sobre el cliente httpx.

El foco está en la frontera: lo que entra al sistema desde el modelo es texto
sin garantías, y `estructurado` promete un `dict`. Entre esas dos cosas hay una
suposición, y las suposiciones en las fronteras son donde se cuelan los bugs.

`ClienteLangChain` ya defiende esa frontera (ver `test_agent_llm_langchain.py`).
Estos tests exigen lo mismo del cliente httpx: si los dos adaptadores del mismo
puerto se comportan distinto ante una respuesta rara, el golden set deja de
medir lo que cree medir según cuál esté configurado.
"""

import json
from typing import Any

import httpx
import pytest

from agent.llm import ClienteOllama

ESQUEMA = {"type": "object", "properties": {"intencion": {"type": "string"}}}


def _cliente_que_responde(monkeypatch: pytest.MonkeyPatch, contenido: str) -> ClienteOllama:
    """Cliente real con el POST interceptado. No sale a la red."""

    def _post_falso(*_args: Any, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"content": contenido}},
            request=httpx.Request("POST", "http://localhost:11434/api/chat"),
        )

    monkeypatch.setattr(httpx, "post", _post_falso)
    return ClienteOllama(modelo="modelo:test")


# --- estructurado: la frontera ------------------------------------------------

def test_estructurado_devuelve_el_objeto_json_del_modelo(monkeypatch: pytest.MonkeyPatch) -> None:
    cliente = _cliente_que_responde(monkeypatch, json.dumps({"intencion": "x"}))
    assert cliente.estructurado("sis", "usr", ESQUEMA) == {"intencion": "x"}


def test_estructurado_rechaza_un_array_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """El puerto promete `dict`. Un array es JSON válido y no cumple.

    Sin este chequeo la lista viaja hacia arriba y revienta más adelante, en un
    nodo que no tiene forma de saber que el problema nació en el modelo.
    """
    cliente = _cliente_que_responde(monkeypatch, json.dumps([{"intencion": "x"}]))
    with pytest.raises(TypeError, match="dict"):
        cliente.estructurado("sis", "usr", ESQUEMA)


def test_estructurado_rechaza_un_escalar_json(monkeypatch: pytest.MonkeyPatch) -> None:
    cliente = _cliente_que_responde(monkeypatch, json.dumps("solo texto"))
    with pytest.raises(TypeError, match="dict"):
        cliente.estructurado("sis", "usr", ESQUEMA)


def test_estructurado_propaga_el_json_invalido(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un JSON roto es un error del modelo, no del adaptador.

    Se deja pasar `JSONDecodeError` en vez de convertirlo: el grafo distingue
    "el modelo falló" de "el modelo respondió otra cosa", y necesita saber cuál.
    """
    cliente = _cliente_que_responde(monkeypatch, "{esto no cierra")
    with pytest.raises(json.JSONDecodeError):
        cliente.estructurado("sis", "usr", ESQUEMA)


# --- redactar ------------------------------------------------------------------

def test_redactar_devuelve_el_texto(monkeypatch: pytest.MonkeyPatch) -> None:
    cliente = _cliente_que_responde(monkeypatch, "Las ventas cayeron.")
    assert cliente.redactar("sis", "usr") == "Las ventas cayeron."


def test_redactar_devuelve_cadena_vacia_si_no_vino_contenido(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redactar no tiene contrato de forma: vacío es un resultado, no un error.

    Es la diferencia con `estructurado`. Acá el que decide qué hacer con un
    texto vacío es el validador del informe, que tiene el contexto para hacerlo.
    """

    def _post_falso(*_args: Any, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(
            200, json={},
            request=httpx.Request("POST", "http://localhost:11434/api/chat"),
        )

    monkeypatch.setattr(httpx, "post", _post_falso)
    assert ClienteOllama(modelo="m").redactar("sis", "usr") == ""
