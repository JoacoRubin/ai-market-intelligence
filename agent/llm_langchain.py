"""Adaptador LangChain del puerto `ClienteLLM`.

Hace exactamente el mismo trabajo que `ClienteOllama`, pero delegando en
`ChatOllama` en vez de armar el POST a mano. Que existan los dos es deliberado y
está justificado en ADR-007.

En resumen: `ClienteLLM` se diseñó como puerto, y un puerto con una sola
implementación es una hipótesis sin verificar. Este archivo la verifica. De paso
abre la salida a otros proveedores —`ChatAnthropic`, `ChatOpenAI`— sin tocar un
solo nodo del grafo, porque la traducción de `estructurado`/`redactar` a la API
de LangChain es la misma para todos.

**Cuál usar.** `ClienteOllama` sigue siendo el default: son ~40 líneas sin
dependencias de framework, y para un Ollama local no hace falta más. Este
adaptador gana cuando entra un segundo proveedor. No hay que elegir de antemano:
se elige con `LLM_BACKEND`.

Lo que este archivo NO hace es meter LangChain adentro del grafo. La
orquestación es de LangGraph y las decisiones son de los nodos (ver ADR-001).
Acá abajo solo se traduce una llamada.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from agent.llm import OLLAMA_HOST, OLLAMA_MODEL, TIMEOUT_SEGUNDOS, ollama_responde

# Clasificar no es una tarea creativa: la variabilidad solo agrega ruido a los
# evals. Redactar sí necesita algo de aire, o las conclusiones salen calcadas.
# Los dos valores son los mismos que usa ClienteOllama; si divergen, los dos
# clientes dejan de ser comparables y el golden set deja de medir lo que cree.
TEMPERATURA_ESTRUCTURADO = 0
TEMPERATURA_PROSA = 0.3


def _chat_ollama(modelo: str, host: str, temperatura: float) -> ChatOllama:
    # validate_model_on_init=False a propósito: con True, construir el cliente
    # golpea la red. Un constructor que hace I/O es un constructor que no se
    # puede instanciar en un test ni en un import.
    return ChatOllama(
        model=modelo,
        base_url=host,
        temperature=temperatura,
        client_kwargs={"timeout": TIMEOUT_SEGUNDOS},
        validate_model_on_init=False,
    )


class ClienteLangChain:
    """Cliente contra Ollama a través de `ChatOllama`.

    Los dos chat models se inyectan para poder testear la traducción sin red.
    Son dos y no uno porque clasificar y redactar corren con temperaturas
    distintas, y `with_structured_output` se pide sobre el modelo, no sobre un
    binding: `bind()` devuelve un `RunnableBinding`, que ya no lo expone.
    """

    def __init__(
        self,
        modelo: str = OLLAMA_MODEL,
        host: str = OLLAMA_HOST,
        *,
        chat_estructurado: BaseChatModel | None = None,
        chat_prosa: BaseChatModel | None = None,
    ) -> None:
        self.nombre = modelo
        self._host = host
        self.chat_estructurado = chat_estructurado or _chat_ollama(
            modelo, host, TEMPERATURA_ESTRUCTURADO
        )
        self.chat_prosa = chat_prosa or _chat_ollama(modelo, host, TEMPERATURA_PROSA)

    def _mensajes(self, sistema: str, usuario: str) -> list[Any]:
        return [SystemMessage(content=sistema), HumanMessage(content=usuario)]

    def estructurado(
        self, sistema: str, usuario: str, esquema: dict[str, Any]
    ) -> dict[str, Any]:
        # method="json_schema" usa la salida estructurada de Ollama, que obliga
        # al modelo por gramática. La alternativa, "json_mode", solo pide "un
        # JSON": el resultado es válido como JSON y puede no cumplir el esquema.
        respuesta = self.chat_estructurado.with_structured_output(
            esquema, method="json_schema"
        ).invoke(self._mensajes(sistema, usuario))

        if not isinstance(respuesta, dict):
            raise TypeError(
                f"ClienteLangChain esperaba un dict del modelo y recibió "
                f"{type(respuesta).__name__}. Revisá el esquema pasado a "
                f"with_structured_output."
            )
        return respuesta

    def redactar(self, sistema: str, usuario: str, max_tokens: int = 700) -> str:
        # El techo de tokens cambia en cada llamada, así que va por `bind` y no
        # en el constructor. Y el techo importa: en CPU cada token de más son
        # segundos reales (ADR-003).
        #
        # Va dentro de `options` y no como kwarg suelto porque `_chat_params`
        # solo intercepta `options`; cualquier otra clave cae al cliente de
        # Ollama, que la rechaza con TypeError. Y como `options` REEMPLAZA al
        # dict armado desde los campos del modelo, la temperatura hay que
        # repasarla acá o se pierde en silencio.
        #
        # Es la única línea del adaptador atada a la forma de Ollama. Con otro
        # proveedor cambia esta línea y nada más.
        respuesta = self.chat_prosa.bind(
            options={"temperature": TEMPERATURA_PROSA, "num_predict": max_tokens}
        ).invoke(self._mensajes(sistema, usuario))
        # .text y no .content: en langchain-core 1.x el contenido puede venir
        # como lista de bloques, y .content la devolvería tal cual.
        return respuesta.text

    def disponible(self) -> bool:
        return ollama_responde(self._host)
