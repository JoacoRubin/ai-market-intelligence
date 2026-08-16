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


def ollama_responde(host: str = OLLAMA_HOST) -> bool:
    """Indica si hay un Ollama escuchando en `host`.

    Vive suelta y no dentro de un cliente porque la pregunta no depende de CÓMO
    se le hable al modelo: la contestan igual el cliente httpx y el de LangChain.
    """
    try:
        httpx.get(f"{host}/api/tags", timeout=5).raise_for_status()
        return True
    except Exception:
        return False


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
        # /api/chat siempre devuelve un objeto; la anotación deja escrita la
        # suposición, que si no queda implícita en un `Any` que se propaga.
        cuerpo: dict[str, Any] = r.json()
        return cuerpo

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
        # `format` le pide al modelo que cumpla el esquema, no lo garantiza: lo
        # que vuelve es texto y `json.loads` acepta arrays y escalares. El
        # puerto promete `dict`, así que la frontera se verifica acá y no tres
        # nodos más arriba, donde el error ya no sabe de dónde vino.
        datos = json.loads(contenido)
        if not isinstance(datos, dict):
            raise TypeError(
                f"ClienteOllama esperaba un dict del modelo y recibió "
                f"{type(datos).__name__}. El modelo no respetó el esquema."
            )
        return datos

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
        texto: str = respuesta.get("message", {}).get("content", "")
        return texto

    def disponible(self) -> bool:
        return ollama_responde(self._host)


@runtime_checkable
class ClienteLLMConSalud(ClienteLLM, Protocol):
    """Un cliente que además sabe decir si su backend está vivo.

    Va aparte de `ClienteLLM` y no adentro porque los nodos del grafo no
    necesitan la salud del modelo: la piden `replay` y `demo`, que cortan
    temprano en vez de gastar minutos de CPU produciendo capturas vacías. Meter
    `disponible()` en el puerto obligaría a implementarlo a cada doble de test
    sin que ningún test lo use.
    """

    def disponible(self) -> bool:
        """Indica si el backend responde."""
        ...


def crear_cliente(
    modelo: str = OLLAMA_MODEL, host: str = OLLAMA_HOST
) -> ClienteLLMConSalud:
    """Construye el cliente del modelo según `LLM_BACKEND`.

    Existen dos adaptadores del mismo puerto (ver ADR-007) y esta es la única
    función que elige entre ellos. Que la decisión viva en un solo lugar es lo
    que permite que el resto del código —nodos, API, replay— no sepa cuál está
    corriendo, que es todo el punto de tener un puerto.

    El default es `httpx`: menos dependencias para el mismo resultado, y es el
    camino que el golden set tiene medido.
    """
    backend = os.getenv("LLM_BACKEND", "httpx").strip().lower()
    if backend == "httpx":
        return ClienteOllama(modelo=modelo, host=host)
    if backend == "langchain":
        # Import diferido: importar langchain_ollama cuesta cerca de un segundo,
        # y no hay razón para pagarlo cuando no se lo eligió.
        from agent.llm_langchain import ClienteLangChain

        return ClienteLangChain(modelo=modelo, host=host)
    raise ValueError(
        f"LLM_BACKEND={backend!r} no existe. Valores válidos: 'httpx', 'langchain'."
    )


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


class ClientePredecible:
    """Doble que responde siempre lo mismo, sin agotarse.

    `ClienteFalso` exige programar cada respuesta, lo que es ideal para
    verificar un flujo concreto. Este sirve para el caso opuesto: tests que
    ejercitan OTRA cosa —la API, el almacén, los códigos de estado— y solo
    necesitan que el modelo no sea el cuello de botella ni el objeto de estudio.

    Sin él, cualquier test de la API terminaría invocando a Ollama y esperando
    los minutos que tarda la inferencia en CPU.
    """

    nombre = "falso:predecible"

    def __init__(self, intencion: str = "product_performance", dias: int = 90,
                 conclusiones: list[str] | None = None) -> None:
        self._intencion = intencion
        self._dias = dias
        self._conclusiones = conclusiones or [
            "El período analizado muestra actividad comercial registrada"
        ]
        self.llamadas: list[dict[str, Any]] = []

    def estructurado(
        self, sistema: str, usuario: str, esquema: dict[str, Any]
    ) -> dict[str, Any]:
        self.llamadas.append({"tipo": "estructurado", "sistema": sistema,
                              "usuario": usuario, "esquema": esquema})
        propiedades = esquema.get("properties", {})
        if "conclusiones" in propiedades:
            return {"conclusiones": list(self._conclusiones)}
        return {"intencion": self._intencion, "dias": self._dias}

    def redactar(self, sistema: str, usuario: str, max_tokens: int = 700) -> str:
        self.llamadas.append({"tipo": "texto", "sistema": sistema,
                              "usuario": usuario})
        return " ".join(self._conclusiones)


class ClienteQueFalla:
    """Doble que siempre falla. Para probar la degradación del grafo.

    Un sistema que solo se prueba con el modelo respondiendo bien es un sistema
    del que no se sabe qué hace cuando el modelo no responde. Y en algún momento
    no responde.
    """

    nombre = "falso:caido"

    def __init__(self, excepcion: Exception | None = None) -> None:
        self._excepcion = excepcion or RuntimeError("el modelo no está disponible")

    def estructurado(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise self._excepcion

    def redactar(self, *_args: Any, **_kwargs: Any) -> str:
        raise self._excepcion


class ClienteLento:
    """Doble que tarda. Para probar timeouts sin esperar de verdad."""

    nombre = "falso:lento"

    def __init__(self, demora_s: float = 0.2) -> None:
        self._demora = demora_s

    def estructurado(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        time.sleep(self._demora)
        return {}

    def redactar(self, *_args: Any, **_kwargs: Any) -> str:
        time.sleep(self._demora)
        return ""
