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
import time
from typing import Any

import httpx
import pytest

from agent.llm import MAX_INTENTOS_LLM, ClienteOllama

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


# --- reintento del transporte ------------------------------------------------
#
# El caso que esto cubre es real y medido: Ollama carga el modelo a memoria en
# la primera consulta después de estar ocioso, y mientras tanto la conexión
# puede cortarse. Un reintento lo tapa.
#
# Lo que NO se reintenta importa igual: un 4xx es un bug de este código —modelo
# inexistente, payload mal formado— y volver a mandarlo son minutos de espera
# para llegar al mismo error.

class _PostFalso:
    """Falla las primeras `fallos` veces y después responde bien."""

    def __init__(self, fallos: int, excepcion: Exception) -> None:
        self.intentos = 0
        self._fallos = fallos
        self._excepcion = excepcion

    def __call__(self, *_args: object, **_kwargs: object) -> object:
        self.intentos += 1
        if self.intentos <= self._fallos:
            raise self._excepcion

        class Respuesta:
            @staticmethod
            def raise_for_status() -> None:
                return None

            @staticmethod
            def json() -> dict[str, object]:
                return {"message": {"content": "{}"}}

        return Respuesta()


@pytest.mark.parametrize("excepcion", [
    httpx.TimeoutException("se acabó el tiempo"),
    httpx.ConnectError("conexión rechazada"),
    httpx.ReadError("se cortó la lectura"),
    httpx.RemoteProtocolError("el servidor cerró de golpe"),
])
def test_un_fallo_transitorio_se_reintenta_y_sale_bien(
    excepcion: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    post = _PostFalso(fallos=1, excepcion=excepcion)
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    assert ClienteOllama()._chat({"model": "x"}) == {"message": {"content": "{}"}}
    assert post.intentos == 2


def test_agotados_los_intentos_la_excepcion_se_propaga(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """No se traga el error.

    Los nodos ya saben degradar cuando el modelo no responde —el router cae a
    `fuera_de_alcance`, el sintetizador arma el informe determinístico—, y
    devolver una respuesta vacía desde acá les sacaría la información que
    necesitan para hacerlo.
    """
    post = _PostFalso(fallos=99, excepcion=httpx.ConnectError("Ollama no está"))
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    with pytest.raises(httpx.ConnectError):
        ClienteOllama()._chat({"model": "x"})

    assert post.intentos == MAX_INTENTOS_LLM


def test_un_error_del_servidor_no_se_reintenta(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un 4xx es determinístico: reintentarlo da exactamente el mismo 4xx.

    Con corridas de 60 a 120 segundos, insistir sobre un error que no va a
    cambiar es la diferencia entre fallar rápido y fallar tarde.
    """
    intentos = 0

    class Respuesta:
        @staticmethod
        def raise_for_status() -> None:
            raise httpx.HTTPStatusError(
                "404 model not found", request=None, response=None  # type: ignore[arg-type]
            )

    def post(*_args: object, **_kwargs: object) -> object:
        nonlocal intentos
        intentos += 1
        return Respuesta()

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    with pytest.raises(httpx.HTTPStatusError):
        ClienteOllama()._chat({"model": "inexistente"})

    assert intentos == 1


def test_sin_fallos_no_hay_reintento_ni_espera(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guardián del camino feliz: el retry no agrega latencia cuando todo anda."""
    post = _PostFalso(fallos=0, excepcion=httpx.ConnectError("nunca"))
    monkeypatch.setattr(httpx, "post", post)

    def dormir_prohibido(_s: float) -> None:
        raise AssertionError("no debe esperar si la primera respuesta sale bien")

    monkeypatch.setattr(time, "sleep", dormir_prohibido)

    ClienteOllama()._chat({"model": "x"})

    assert post.intentos == 1
