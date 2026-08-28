"""Tests del adaptador LangChain del puerto `ClienteLLM`.

`ClienteOllama` habla HTTP crudo contra Ollama. `ClienteLangChain` hace el mismo
trabajo a través de `ChatOllama`. Que existan los dos no es indecisión: es la
prueba de que `ClienteLLM` es un puerto de verdad y no un envoltorio de una sola
implementación. Un puerto con un solo adaptador es una hipótesis; con dos, es un
hecho verificado.

Lo que se prueba acá es **la traducción**, que es todo el trabajo de un
adaptador: que la llamada del puerto se convierta en la llamada correcta de
LangChain, y que la respuesta de LangChain se convierta en lo que el puerto
promete. Si el modelo acierta o no es otra pregunta, y se mide en el golden set.

El doble se escribe a mano porque `FakeListChatModel` de langchain-core no
implementa `with_structured_output` — levanta `NotImplementedError`. Verificado
contra langchain-core 1.5.3, no supuesto.
"""

from typing import Any, cast

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from langchain_ollama import ChatOllama

from agent.llm import (
    ClienteLLM,
    ClienteLLMConSalud,
    ClienteOllama,
    crear_cliente,
    ollama_responde,
)
from agent.llm_langchain import ClienteLangChain

ESQUEMA = {
    "type": "object",
    "properties": {"intencion": {"type": "string"}, "dias": {"type": "integer"}},
    "required": ["intencion"],
}


class ChatFalso:
    """Doble de un chat model de LangChain que registra cómo lo llamaron.

    Registrar importa tanto como responder. Un adaptador que devuelve el dict
    correcto pero perdió el mensaje de sistema en el camino está roto, y sin
    inspeccionar la llamada ese bug es invisible.
    """

    def __init__(
        self,
        estructurada: dict[str, Any] | None = None,
        texto: str | Any = "",
    ) -> None:
        self._estructurada = estructurada if estructurada is not None else {}
        self._texto = texto
        self.recibido: dict[str, Any] = {}

    # --- lo que consume el adaptador -----------------------------------------

    def with_structured_output(
        self, schema: Any, *, method: str = "json_schema", **kwargs: Any
    ) -> Any:
        self.recibido["esquema"] = schema
        self.recibido["method"] = method
        return RunnableLambda(self._responder_estructurado)

    def bind(self, **kwargs: Any) -> Any:
        self.recibido["bind"] = kwargs
        return RunnableLambda(self._responder_texto)

    def invoke(self, mensajes: Any) -> AIMessage:
        return self._responder_texto(mensajes)

    # --- respuestas programadas ----------------------------------------------

    def _responder_estructurado(self, mensajes: Any) -> Any:
        self.recibido["mensajes"] = mensajes
        return self._estructurada

    def _responder_texto(self, mensajes: Any) -> AIMessage:
        self.recibido["mensajes"] = mensajes
        return AIMessage(content=self._texto)


class ChatQueExplota:
    """Doble que falla siempre, como falla un Ollama caído."""

    def __init__(self, excepcion: Exception | None = None) -> None:
        self._excepcion = excepcion or RuntimeError("connection refused")

    def with_structured_output(self, *_a: Any, **_k: Any) -> Any:
        return RunnableLambda(self._explotar)

    def bind(self, *_a: Any, **_k: Any) -> Any:
        return RunnableLambda(self._explotar)

    def invoke(self, *_a: Any, **_k: Any) -> Any:
        return self._explotar(None)

    def _explotar(self, _mensajes: Any) -> Any:
        raise self._excepcion


def _cliente(chat_estructurado: Any = None, chat_prosa: Any = None) -> ClienteLangChain:
    # `cast` y no un subtipo real de BaseChatModel: heredar de la clase base
    # obligaría a implementar `_generate`, `_llm_type` y el resto del contrato
    # interno de LangChain. El adaptador consume tres métodos, y son los que
    # el doble implementa.
    return ClienteLangChain(
        modelo="modelo:test",
        chat_estructurado=cast(BaseChatModel, chat_estructurado or ChatFalso()),
        chat_prosa=cast(BaseChatModel, chat_prosa or ChatFalso()),
    )


# --- El contrato del puerto --------------------------------------------------

def test_cumple_el_puerto_cliente_llm() -> None:
    """Si esto falla, el adaptador no es intercambiable y no sirve de nada."""
    assert isinstance(_cliente(), ClienteLLM)


def test_expone_el_nombre_del_modelo() -> None:
    """El nombre viaja al trace de cada análisis: sin él no se sabe quién respondió."""
    assert _cliente().nombre == "modelo:test"


# --- estructurado ------------------------------------------------------------

def test_estructurado_devuelve_el_dict_del_modelo() -> None:
    chat = ChatFalso(estructurada={"intencion": "product_performance", "dias": 30})
    resultado = _cliente(chat_estructurado=chat).estructurado("sis", "usr", ESQUEMA)
    assert resultado == {"intencion": "product_performance", "dias": 30}


def test_estructurado_manda_sistema_y_usuario_en_ese_orden() -> None:
    """El prompt de sistema lleva los ejemplos y las reglas de clasificación.

    Perderlo, o mandarlo como mensaje de usuario, degrada la clasificación sin
    romper nada: el JSON sigue siendo válido y la intención pasa a estar mal.
    Ese fue exactamente el modo de falla que documenta ADR-001.
    """
    chat = ChatFalso(estructurada={"intencion": "x"})
    _cliente(chat_estructurado=chat).estructurado("reglas", "consulta del usuario", ESQUEMA)

    mensajes = chat.recibido["mensajes"]
    assert [type(m) for m in mensajes] == [SystemMessage, HumanMessage]
    assert mensajes[0].content == "reglas"
    assert mensajes[1].content == "consulta del usuario"


def test_estructurado_usa_la_api_de_salida_estructurada_de_ollama() -> None:
    """`json_schema` obliga al modelo por gramática; `json_mode` solo pide JSON.

    La diferencia no es estilística: con `json_mode` el modelo puede devolver un
    JSON válido con las claves que se le ocurran, y el esquema deja de ser una
    garantía para volverse una sugerencia.
    """
    chat = ChatFalso(estructurada={"intencion": "x"})
    _cliente(chat_estructurado=chat).estructurado("sis", "usr", ESQUEMA)

    assert chat.recibido["esquema"] == ESQUEMA
    assert chat.recibido["method"] == "json_schema"


def test_estructurado_rechaza_una_respuesta_que_no_es_un_dict() -> None:
    """El puerto promete `dict`. Devolver otra cosa rompe a los nodos de arriba.

    Falla acá, con un mensaje que nombra al adaptador, y no tres capas más
    adelante con un `AttributeError` sobre un objeto que nadie sabe de dónde
    salió.
    """
    chat = ChatFalso(estructurada="esto no es un dict")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="dict"):
        _cliente(chat_estructurado=chat).estructurado("sis", "usr", ESQUEMA)


def test_estructurado_propaga_la_falla_del_modelo() -> None:
    """Igual que `ClienteOllama`: el adaptador no se traga los errores.

    Quien decide cómo degradar es el grafo, que tiene el estado para hacerlo.
    Un adaptador que devuelve `{}` ante un fallo convierte una caída en datos
    silenciosamente vacíos.
    """
    with pytest.raises(RuntimeError, match="connection refused"):
        _cliente(chat_estructurado=ChatQueExplota()).estructurado("s", "u", ESQUEMA)


# --- redactar ----------------------------------------------------------------

def test_redactar_devuelve_el_texto_del_modelo() -> None:
    chat = ChatFalso(texto="El período muestra una caída sostenida.")
    assert _cliente(chat_prosa=chat).redactar("sis", "usr") == (
        "El período muestra una caída sostenida."
    )


def test_redactar_aplana_los_bloques_de_contenido() -> None:
    """langchain-core 1.x puede devolver `content` como lista de bloques.

    Leer `.content` a secas devolvería esa lista y el informe terminaría con la
    repr de un dict adentro. `.text` es la propiedad que aplana, y por eso el
    adaptador la usa.
    """
    chat = ChatFalso(texto=[{"type": "text", "text": "Ventas estables."}])
    assert _cliente(chat_prosa=chat).redactar("sis", "usr") == "Ventas estables."


def test_redactar_acota_los_tokens_de_salida() -> None:
    """El límite no es cosmético: cada token de más son segundos de CPU.

    Ver ADR-003. Un `redactar` sin techo puede colgar un análisis por minutos.

    Se afirma la forma exacta —dentro de `options`— y no solo que el número
    llegó: `ChatOllama` rechaza `num_predict` como kwarg suelto.
    """
    chat = ChatFalso(texto="ok")
    _cliente(chat_prosa=chat).redactar("sis", "usr", max_tokens=120)
    assert chat.recibido["bind"]["options"]["num_predict"] == 120


def test_redactar_no_pierde_la_temperatura_al_acotar_los_tokens() -> None:
    """`options` REEMPLAZA el dict que arma ChatOllama desde sus campos.

    O sea: pasar solo `num_predict` deja al modelo con la temperatura por
    defecto de Ollama en vez de la configurada. No falla, no avisa, y las
    conclusiones salen con otro régimen de sampling que el que mide el golden
    set. Es el peor tipo de bug: el que solo se nota en los resultados.
    """
    chat = ChatFalso(texto="ok")
    _cliente(chat_prosa=chat).redactar("sis", "usr", max_tokens=120)
    assert chat.recibido["bind"]["options"]["temperature"] == 0.3


def test_redactar_manda_sistema_y_usuario_en_ese_orden() -> None:
    chat = ChatFalso(texto="ok")
    _cliente(chat_prosa=chat).redactar("instrucciones", "datos")

    mensajes = chat.recibido["mensajes"]
    assert [type(m) for m in mensajes] == [SystemMessage, HumanMessage]
    assert mensajes[0].content == "instrucciones"
    assert mensajes[1].content == "datos"


def test_redactar_propaga_la_falla_del_modelo() -> None:
    with pytest.raises(RuntimeError, match="connection refused"):
        _cliente(chat_prosa=ChatQueExplota()).redactar("s", "u")


# --- Configuración del modelo real -------------------------------------------

def test_separa_la_temperatura_de_clasificar_y_la_de_redactar() -> None:
    """Clasificar es determinístico; redactar necesita algo de aire.

    Son dos regímenes de sampling distintos, y por eso son dos instancias
    distintas. Compartir una sola obligaría a elegir cuál de las dos tareas se
    hace peor.
    """
    cliente = ClienteLangChain(modelo="m", host="http://localhost:11434")
    # El puerto expone BaseChatModel; la temperatura es de ChatOllama. El
    # isinstance no es ceremonia: si el constructor dejara de armar un
    # ChatOllama, este test debe fallar por eso y no por un AttributeError.
    assert isinstance(cliente.chat_estructurado, ChatOllama)
    assert isinstance(cliente.chat_prosa, ChatOllama)
    assert cliente.chat_estructurado.temperature == 0
    assert cliente.chat_prosa.temperature == 0.3


def test_los_dos_chats_apagan_el_razonamiento_en_voz_alta() -> None:
    """El mismo defecto que `ClienteOllama` ya tenía arreglado, y que este
    adaptador nunca recibió.

    `agent/llm.py` manda `"think": False` en `estructurado` y en `redactar`, con
    la ablación medida al lado. Acá no estaba, así que con `qwen3:4b` este
    cliente reproducía los dos síntomas: el timeout entero consumido razonando
    al clasificar, y `redactar` devolviendo texto VACÍO porque los tokens de
    `num_predict` se iban al bloque `thinking` que nadie lee.

    En `ChatOllama` el flag se llama `reasoning` y se traduce a `think` en el
    payload (`_chat_params`: `"think": kwargs.pop("reasoning", self.reasoning)`).
    Va en el constructor y no en el `bind` de `redactar` porque los DOS métodos
    lo necesitan, y porque `options` se resuelve aparte: bindear opciones no lo
    pisa.

    Se afirma sobre el payload y no sobre el atributo a propósito. Que el campo
    valga `False` no prueba que salga en la request; lo que rompía el informe
    era la request.
    """
    cliente = ClienteLangChain(modelo="m", host="http://localhost:11434")

    for chat in (cliente.chat_estructurado, cliente.chat_prosa):
        assert isinstance(chat, ChatOllama)
        payload = chat._chat_params([HumanMessage(content="hola")])
        assert payload["think"] is False


def test_acotar_los_tokens_no_reenciende_el_razonamiento() -> None:
    """`redactar` bindea `options`, y ahí es donde se perdería el flag.

    Es el mismo modo de falla que el comentario de `redactar` ya documenta para
    la temperatura: `options` REEMPLAZA al dict armado desde los campos del
    modelo. Si `think` viajara ahí adentro, acotar los tokens lo borraría en
    silencio y volvería el texto vacío.
    """
    cliente = ClienteLangChain(modelo="m", host="http://localhost:11434")
    chat = cliente.chat_prosa
    assert isinstance(chat, ChatOllama)

    payload = chat._chat_params(
        [HumanMessage(content="hola")],
        options={"temperature": 0.3, "num_predict": 60},
    )

    assert payload["think"] is False
    assert payload["options"]["num_predict"] == 60


def test_apunta_al_host_de_ollama_configurado() -> None:
    cliente = ClienteLangChain(modelo="m", host="http://otro-host:9999")
    assert isinstance(cliente.chat_estructurado, ChatOllama)
    assert isinstance(cliente.chat_prosa, ChatOllama)
    assert cliente.chat_estructurado.base_url == "http://otro-host:9999"
    assert cliente.chat_prosa.base_url == "http://otro-host:9999"


# --- Selección del backend ---------------------------------------------------
#
# Sin un selector, el adaptador sería código que solo ejercitan los tests: la
# dependencia seguiría estando de adorno, que es justamente lo que se quería
# arreglar. `LLM_BACKEND` es lo que lo vuelve alcanzable en producción.

def test_por_defecto_se_usa_el_cliente_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    """El default no cambia: menos dependencias para el mismo resultado.

    Cambiarlo sería mover el camino que ya está medido por el golden set a
    cambio de nada. El adaptador se opta, no se impone.
    """
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    assert isinstance(crear_cliente(), ClienteOllama)


def test_se_puede_elegir_el_backend_langchain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BACKEND", "langchain")
    assert isinstance(crear_cliente(), ClienteLangChain)


def test_el_backend_se_lee_sin_distinguir_mayusculas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BACKEND", "  LangChain ")
    assert isinstance(crear_cliente(), ClienteLangChain)


def test_un_backend_desconocido_falla_al_arrancar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un typo tiene que romper acá, no degradar en silencio al default.

    Si `LLM_BACKEND=langchian` cayera al cliente httpx sin decir nada, la corrida
    mediría un backend distinto del que se creyó configurar — y ese es
    exactamente el tipo de defecto de instrumento que ya nos costó una sesión.
    """
    monkeypatch.setenv("LLM_BACKEND", "langchian")
    with pytest.raises(ValueError, match="langchian"):
        crear_cliente()


def test_los_dos_backends_cumplen_el_mismo_puerto(monkeypatch: pytest.MonkeyPatch) -> None:
    """Intercambiables de verdad: los nodos no pueden notar la diferencia."""
    for backend in ("httpx", "langchain"):
        monkeypatch.setenv("LLM_BACKEND", backend)
        assert isinstance(crear_cliente(), ClienteLLM)


def test_los_dos_backends_saben_reportar_su_salud(monkeypatch: pytest.MonkeyPatch) -> None:
    """`replay` y `demo` cortan temprano si Ollama no responde.

    Ese chequeo existe porque un entorno incompleto no falla: produce capturas
    plausibles y vacías. Si un backend no supiera contestarlo, el harness
    perdería su única defensa contra eso.
    """
    for backend in ("httpx", "langchain"):
        monkeypatch.setenv("LLM_BACKEND", backend)
        assert isinstance(crear_cliente(), ClienteLLMConSalud)


# --- Contra el modelo real ---------------------------------------------------
#
# Todo lo de arriba prueba la traducción contra dobles: rápido, determinístico y
# ciego a una cosa importante. Que `with_structured_output` haga lo que dice
# contra un Ollama de verdad no lo demuestra ningún doble.
#
# Marcados `llm`: quedan fuera de la corrida por defecto (ver addopts). Se corren
# con `pytest -m llm` cuando se toca este adaptador o se sube la versión de
# langchain-ollama.

@pytest.fixture(scope="module")
def clientes_reales() -> tuple[ClienteOllama, ClienteLangChain]:
    if not ollama_responde():
        pytest.skip("Ollama no responde: levantalo con `ollama serve`")
    return ClienteOllama(), ClienteLangChain()


@pytest.mark.llm
def test_real_ambos_backends_respetan_el_esquema(
    clientes_reales: tuple[ClienteOllama, ClienteLangChain],
) -> None:
    """El contrato duro: sea cual sea el backend, sale un dict que cumple.

    No se compara el VALOR devuelto. Dos backends pueden clasificar distinto y
    seguir siendo ambos correctos como adaptadores; que acierten es lo que mide
    el golden set, y es otra pregunta.
    """
    httpx_, lc = clientes_reales
    sistema = "Clasificá la consulta. Respondé solo con el JSON pedido."
    usuario = "¿Cómo vienen las ventas de P001 en los últimos 30 días?"

    for cliente in (httpx_, lc):
        salida = cliente.estructurado(sistema, usuario, ESQUEMA)
        assert isinstance(salida, dict), f"{type(cliente).__name__} no devolvió dict"
        assert "intencion" in salida, f"{type(cliente).__name__} omitió una clave requerida"
        assert isinstance(salida["intencion"], str)


@pytest.mark.llm
def test_real_ambos_backends_redactan_texto_plano(
    clientes_reales: tuple[ClienteOllama, ClienteLangChain],
) -> None:
    """`.text` tiene que devolver un str, no la repr de una lista de bloques.

    Es el modo de falla más silencioso de langchain-core 1.x: el informe sale,
    se ve raro, y nadie sabe por qué hasta que lo lee una persona.
    """
    httpx_, lc = clientes_reales
    sistema = "Respondé en una sola oración corta, en español."
    usuario = "Resumí: las ventas de P001 cayeron 12% en marzo."

    for cliente in (httpx_, lc):
        salida = cliente.redactar(sistema, usuario, max_tokens=60)
        assert isinstance(salida, str), f"{type(cliente).__name__} no devolvió str"
        assert salida.strip(), f"{type(cliente).__name__} devolvió texto vacío"
        assert "[{" not in salida, f"{type(cliente).__name__} filtró bloques sin aplanar"
