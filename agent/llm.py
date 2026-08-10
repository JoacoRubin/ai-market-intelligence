"""Acceso al modelo de lenguaje, detrás de una interfaz mínima.

El motivo de que exista este archivo, y no llamadas a Ollama esparcidas por los
nodos, es poder **testear el grafo sin el modelo**.

En esta máquina una llamada al LLM tarda entre 12 y 41 segundos (ver ADR-003).
Si cada test invocara el modelo, la suite pasaría de segundos a media hora — y
una suite que tarda media hora es una suite que nadie corre. Ahí se termina el
TDD, y con él la red que sostiene todo lo demás.

Con la interfaz inyectada, la lógica del grafo —ramas, límites, validaciones,
manejo de errores— se prueba en milisegundos con un doble determinístico. El
modelo real se prueba aparte, en unos pocos tests marcados `slow` que sí valen
la espera porque miden otra cosa: si el modelo entiende.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Protocol, runtime_checkable

import httpx

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
TIMEOUT_SEGUNDOS = 300


@runtime_checkable
class ClienteLLM(Protocol):
    """Lo mínimo que los nodos necesitan de un modelo."""

    nombre: str

    def estructurado(
        self, sistema: str, usuario: str, esquema: dict[str, Any]
    ) -> dict[str, Any]:
        """Devuelve un JSON que cumple `esquema`."""
        ...

    def redactar(self, sistema: str, usuario: str, max_tokens: int = 700) -> str:
        """Devuelve texto libre."""
        ...


class ClienteOllama:
    """Cliente real contra un Ollama local."""

    def __init__(self, modelo: str = OLLAMA_MODEL, host: str = OLLAMA_HOST) -> None:
        self.nombre = modelo
        self._host = host

    def _chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        r = httpx.post(f"{self._host}/api/chat", json=payload,
                       timeout=TIMEOUT_SEGUNDOS)
        r.raise_for_status()
        return r.json()

    def estructurado(
        self, sistema: str, usuario: str, esquema: dict[str, Any]
    ) -> dict[str, Any]:
        respuesta = self._chat({
            "model": self.nombre,
            "messages": [
                {"role": "system", "content": sistema},
                {"role": "user", "content": usuario},
            ],
            "format": esquema,
            "stream": False,
            # temperatura 0: clasificar no es una tarea creativa, y la
            # variabilidad acá solo agrega ruido a los evals.
            "options": {"temperature": 0},
        })
        contenido = respuesta.get("message", {}).get("content", "")
        return json.loads(contenido)

    def redactar(self, sistema: str, usuario: str, max_tokens: int = 700) -> str:
        respuesta = self._chat({
            "model": self.nombre,
            "messages": [
                {"role": "system", "content": sistema},
                {"role": "user", "content": usuario},
            ],
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": max_tokens},
        })
        return respuesta.get("message", {}).get("content", "")

    def disponible(self) -> bool:
        try:
            httpx.get(f"{self._host}/api/tags", timeout=5).raise_for_status()
            return True
        except Exception:
            return False


class ClienteFalso:
    """Doble determinístico para los tests.

    Devuelve respuestas preprogramadas y registra las llamadas recibidas, lo que
    permite verificar QUÉ se le pidió al modelo — no solo qué devolvió. Un
    prompt que dejó de incluir los ejemplos es un bug invisible de otro modo.
    """

    def __init__(
        self,
        respuestas_estructuradas: list[dict[str, Any]] | None = None,
        textos: list[str] | None = None,
        nombre: str = "falso:test",
    ) -> None:
        self.nombre = nombre
        self._estructuradas = list(respuestas_estructuradas or [])
        self._textos = list(textos or [])
        self.llamadas: list[dict[str, Any]] = []

    def estructurado(
        self, sistema: str, usuario: str, esquema: dict[str, Any]
    ) -> dict[str, Any]:
        self.llamadas.append({"tipo": "estructurado", "sistema": sistema,
                              "usuario": usuario, "esquema": esquema})
        if not self._estructuradas:
            raise AssertionError(
                "ClienteFalso recibió más llamadas estructuradas que respuestas "
                "programadas: el test está ejercitando un camino que no previó"
            )
        return self._estructuradas.pop(0)

    def redactar(self, sistema: str, usuario: str, max_tokens: int = 700) -> str:
        self.llamadas.append({"tipo": "texto", "sistema": sistema,
                              "usuario": usuario})
        if not self._textos:
            raise AssertionError("ClienteFalso se quedó sin textos programados")
        return self._textos.pop(0)


class ClienteQueFalla:
    """Doble que siempre falla. Para probar la degradación del grafo.

    Un sistema que solo se prueba con el modelo respondiendo bien es un sistema
    del que no se sabe qué hace cuando el modelo no responde. Y en algún momento
    no responde.
    """

    nombre = "falso:caido"

    def __init__(self, excepcion: Exception | None = None) -> None:
        self._excepcion = excepcion or RuntimeError("el modelo no está disponible")

    def estructurado(self, *_args, **_kwargs) -> dict[str, Any]:
        raise self._excepcion

    def redactar(self, *_args, **_kwargs) -> str:
        raise self._excepcion


class ClienteLento:
    """Doble que tarda. Para probar timeouts sin esperar de verdad."""

    nombre = "falso:lento"

    def __init__(self, demora_s: float = 0.2) -> None:
        self._demora = demora_s

    def estructurado(self, *_args, **_kwargs) -> dict[str, Any]:
        time.sleep(self._demora)
        return {}

    def redactar(self, *_args, **_kwargs) -> str:
        time.sleep(self._demora)
        return ""
