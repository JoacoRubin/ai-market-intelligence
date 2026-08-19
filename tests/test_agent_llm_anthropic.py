"""Tests del adaptador Anthropic del puerto `ClienteLLM`.

Es el **tercer** adaptador del mismo puerto, y el primero contra un proveedor
que no es Ollama. Eso lo vuelve el más interesante de los tres: `ClienteOllama`
y `ClienteLangChain` hablan con el mismo backend por dos caminos, así que un
puerto moldeado sobre los detalles de Ollama los pasaría a los dos sin
protestar. Este no.

Lo que se prueba acá es **la traducción**, igual que en ADR-007: que la llamada
del puerto se convierta en la llamada correcta del SDK, y que la respuesta del
SDK se convierta en lo que el puerto promete.

**El doble devuelve un `anthropic.types.Message` de verdad**, construido con los
modelos Pydantic del SDK, y no un objeto inventado con los atributos que al
adaptador le convienen. La diferencia no es cosmética: un doble a mano acepta
cualquier forma y no vería que el SDK renombró `usage` o cambió los bloques de
contenido. Es la lección que ya nos costó una sesión —los dobles no validan la
API del framework— aplicada tan lejos como un doble puede llegar.

Tan lejos, y no más. Lo que ningún doble puede decir es si la API **acepta** lo
que le mandamos: si `output_config.format` exige `additionalProperties`, si el
modelo rechaza un `temperature`. Eso lo contestan los tests marcados `llm`, al
final, que necesitan `ANTHROPIC_API_KEY` y no corren por defecto.
"""

from typing import Any

import pytest
from anthropic.types import Message, TextBlock, Usage

from agent.llm import ClienteLLM, ClienteLLMConSalud, ClienteLLMConUso, Uso, crear_cliente
from agent.llm_anthropic import MODELO_POR_DEFECTO, ClienteAnthropic

ESQUEMA = {
    "type": "object",
    "properties": {"intencion": {"type": "string"}, "dias": {"type": "integer"}},
    "required": ["intencion"],
}


def _mensaje(texto: str = "{}", entrada: int = 100, salida: int = 20) -> Message:
    """Un `Message` real del SDK, con los tipos del SDK."""
    return Message(
        id="msg_test",
        model="claude-opus-5",
        role="assistant",
        type="message",
        content=[TextBlock(type="text", text=texto)],
        stop_reason="end_turn",
        stop_sequence=None,
        usage=Usage(
            input_tokens=entrada,
            output_tokens=salida,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


class MessagesFalso:
    """Registra con qué kwargs se llamó a `messages.create`.

    Registrar importa tanto como responder: un adaptador que devuelve el dict
    correcto pero mandó el prompt de sistema como mensaje de usuario está roto,
    y sin inspeccionar la llamada ese bug es invisible.
    """

    def __init__(self, respuestas: list[Message] | None = None,
                 excepcion: Exception | None = None) -> None:
        self._respuestas = list(respuestas or [])
        self._excepcion = excepcion
        self.llamadas: list[dict[str, Any]] = []
        self.conteos: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Message:
        self.llamadas.append(kwargs)
        if self._excepcion:
            raise self._excepcion
        return self._respuestas.pop(0) if self._respuestas else _mensaje()

    def count_tokens(self, **kwargs: Any) -> Any:
        self.conteos.append(kwargs)

        class _Conteo:
            input_tokens = 2_346

        return _Conteo()


class AnthropicFalso:
    """Doble del cliente del SDK: solo la superficie que el adaptador consume."""

    def __init__(self, messages: MessagesFalso | None = None,
                 modelos_ok: bool = True) -> None:
        self.messages = messages or MessagesFalso()
        self._modelos_ok = modelos_ok

    @property
    def models(self) -> Any:
        falla = None if self._modelos_ok else RuntimeError("401 authentication_error")

        class _Models:
            def list(self, **_kwargs: Any) -> object:
                if falla:
                    raise falla
                return object()

        return _Models()


def _cliente(messages: MessagesFalso | None = None, **kwargs: Any) -> ClienteAnthropic:
    return ClienteAnthropic(
        modelo="claude-opus-5",
        cliente=AnthropicFalso(messages or MessagesFalso()),
        **kwargs,
    )


# --- El contrato del puerto --------------------------------------------------

def test_cumple_el_puerto_cliente_llm() -> None:
    """Si esto falla, el adaptador no es intercambiable y no sirve de nada."""
    assert isinstance(_cliente(), ClienteLLM)


def test_cumple_el_puerto_con_salud() -> None:
    """`replay` y `demo` cortan temprano si el backend no responde."""
    assert isinstance(_cliente(), ClienteLLMConSalud)


def test_cumple_el_puerto_con_uso() -> None:
    """Sin esto no hay forma de saber cuánto costó una corrida.

    Es el puerto que separa a un proveedor pago de uno local: `ClienteOllama` no
    tiene nada que reportar porque no cobra nada.
    """
    assert isinstance(_cliente(), ClienteLLMConUso)


def test_expone_el_nombre_del_modelo() -> None:
    """El nombre viaja al trace y al registro del eval.

    `eval/registro.py` se niega a comparar corridas de modelos distintos, y usa
    este campo para saberlo. Un nombre equivocado convierte esa defensa en nada.
    """
    assert _cliente().nombre == "claude-opus-5"


# --- estructurado ------------------------------------------------------------

def test_estructurado_devuelve_el_dict_del_modelo() -> None:
    msgs = MessagesFalso([_mensaje('{"intencion": "product_performance", "dias": 30}')])
    assert _cliente(msgs).estructurado("sis", "usr", ESQUEMA) == {
        "intencion": "product_performance", "dias": 30
    }


def test_estructurado_manda_el_sistema_como_system_y_no_como_mensaje() -> None:
    """La diferencia central con Ollama, y la más fácil de errar.

    En `/api/chat` de Ollama el prompt de sistema es un mensaje más del array.
    En la API de Anthropic es un parámetro aparte, `system`. Mandarlo como
    `{"role": "system"}` dentro de `messages` no rompe nada visible: el modelo
    lo lee igual y clasifica un poco peor. Ese es el modo de falla que este test
    existe para atrapar.
    """
    msgs = MessagesFalso([_mensaje('{"intencion": "x"}')])
    _cliente(msgs).estructurado("las reglas", "la consulta", ESQUEMA)

    kwargs = msgs.llamadas[0]
    assert kwargs["system"] == "las reglas"
    assert kwargs["messages"] == [{"role": "user", "content": "la consulta"}]


def test_estructurado_pide_salida_estructurada_por_esquema() -> None:
    """`output_config.format` obliga por gramática, como `json_schema` en Ollama.

    La alternativa —pedir JSON en el prompt y parsear— produce un JSON válido
    con las claves que al modelo se le ocurran. El esquema dejaría de ser
    garantía para volverse sugerencia.
    """
    msgs = MessagesFalso([_mensaje('{"intencion": "x"}')])
    _cliente(msgs).estructurado("sis", "usr", ESQUEMA)

    formato = msgs.llamadas[0]["output_config"]["format"]
    assert formato["type"] == "json_schema"
    assert formato["schema"]["properties"] == ESQUEMA["properties"]
    assert formato["schema"]["required"] == ESQUEMA["required"]


def test_estructurado_cierra_el_esquema_con_additional_properties() -> None:
    """Las salidas estructuradas exigen el esquema cerrado; los nuestros están abiertos.

    Los `ESQUEMA` de los nodos se escribieron para Ollama, que no lo pide. El
    adaptador los cierra en el borde en vez de tocar los nodos: cambiarlos allá
    modificaría el prompt que manda `ClienteOllama`, y el golden set dejaría de
    medir el mismo sistema que venía midiendo.
    """
    msgs = MessagesFalso([_mensaje('{"intencion": "x"}')])
    _cliente(msgs).estructurado("sis", "usr", ESQUEMA)

    assert msgs.llamadas[0]["output_config"]["format"]["schema"][
        "additionalProperties"] is False


def test_estructurado_cierra_tambien_los_objetos_anidados() -> None:
    """El esquema del synthesizer tiene objetos adentro de un array.

    Cerrar solo la raíz dejaría abierto justo el nivel donde el modelo inventa
    claves: cada conclusión con su `texto` y su `fuente`.
    """
    anidado = {
        "type": "object",
        "properties": {
            "conclusiones": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"texto": {"type": "string"},
                                   "fuente": {"type": "string"}},
                    "required": ["texto"],
                },
            }
        },
        "required": ["conclusiones"],
    }
    msgs = MessagesFalso([_mensaje('{"conclusiones": []}')])
    _cliente(msgs).estructurado("sis", "usr", anidado)

    enviado = msgs.llamadas[0]["output_config"]["format"]["schema"]
    assert enviado["additionalProperties"] is False
    assert enviado["properties"]["conclusiones"]["items"][
        "additionalProperties"] is False


def test_estructurado_no_muta_el_esquema_que_recibe() -> None:
    """Los `ESQUEMA` son constantes de módulo compartidas entre adaptadores.

    Si el adaptador las cerrara in-place, `ClienteOllama` empezaría a mandar un
    esquema distinto del que mandaba antes — por un import, sin que nadie
    tocara ese archivo. Un bug de acción a distancia, del peor tipo.
    """
    copia = dict(ESQUEMA)
    _cliente().estructurado("sis", "usr", ESQUEMA)
    assert copia == ESQUEMA
    assert "additionalProperties" not in ESQUEMA


def test_estructurado_no_manda_temperature() -> None:
    """Claude Opus 5 RECHAZA `temperature` con un 400: el parámetro no existe más.

    Los otros dos adaptadores fijan temperatura 0 para clasificar. Acá no se
    puede, y mandarla igual "por simetría" haría fallar todas las llamadas.

    Es una divergencia real entre proveedores y está documentada en el ADR: el
    régimen de sampling NO se puede sostener constante entre Ollama y Anthropic,
    así que la comparación de modelos tiene ese confound y hay que declararlo.
    """
    msgs = MessagesFalso([_mensaje('{"intencion": "x"}')])
    _cliente(msgs).estructurado("sis", "usr", ESQUEMA)

    assert "temperature" not in msgs.llamadas[0]
    assert "top_p" not in msgs.llamadas[0]


def test_estructurado_rechaza_una_respuesta_que_no_es_un_dict() -> None:
    """El puerto promete `dict`. Igual que en los otros dos adaptadores.

    Falla acá, con un mensaje que nombra al adaptador, y no tres capas más
    arriba con un `AttributeError` sobre un objeto que nadie sabe de dónde vino.
    """
    msgs = MessagesFalso([_mensaje('["esto", "es", "un", "array"]')])
    with pytest.raises(TypeError, match="dict"):
        _cliente(msgs).estructurado("sis", "usr", ESQUEMA)


def test_estructurado_propaga_la_falla_del_proveedor() -> None:
    """El adaptador no se traga los errores: quien decide cómo degradar es el grafo.

    Un adaptador que devuelve `{}` ante un fallo convierte una caída en datos
    silenciosamente vacíos, y el informe sale sin que nadie se entere.
    """
    msgs = MessagesFalso(excepcion=RuntimeError("overloaded_error"))
    with pytest.raises(RuntimeError, match="overloaded_error"):
        _cliente(msgs).estructurado("sis", "usr", ESQUEMA)


# --- redactar ----------------------------------------------------------------

def test_redactar_devuelve_el_texto_del_modelo() -> None:
    msgs = MessagesFalso([_mensaje("El período muestra una caída sostenida.")])
    assert _cliente(msgs).redactar("sis", "usr") == (
        "El período muestra una caída sostenida."
    )


def test_redactar_junta_todos_los_bloques_de_texto() -> None:
    """Una respuesta puede venir partida en varios bloques de texto.

    Leer solo el primero devolvería un informe truncado a mitad de oración, sin
    error y sin aviso. Es el mismo modo de falla que `.text` resuelve en el
    adaptador de LangChain.
    """
    partido = _mensaje()
    partido.content = [TextBlock(type="text", text="Ventas estables. "),
                       TextBlock(type="text", text="Sin anomalías.")]
    assert _cliente(MessagesFalso([partido])).redactar("sis", "usr") == (
        "Ventas estables. Sin anomalías."
    )


def test_redactar_ignora_los_bloques_que_no_son_texto() -> None:
    """Con thinking activo la respuesta trae bloques `thinking` antes del texto.

    Concatenarlos a ciegas metería el razonamiento del modelo adentro del
    informe. Se filtra por `type`, no por posición.
    """
    class BloqueRaro:
        type = "thinking"
        thinking = "dejame pensar..."

    con_thinking = _mensaje("La conclusión.")
    con_thinking.content = [BloqueRaro(), *con_thinking.content]  # type: ignore[list-item]
    assert _cliente(MessagesFalso([con_thinking])).redactar("s", "u") == "La conclusión."


def test_redactar_acota_los_tokens_de_salida() -> None:
    """El techo no es cosmético: en un proveedor pago, cada token de más es plata.

    Es el mismo argumento que ADR-003 hacía en segundos de CPU, traducido a la
    unidad que cobra Anthropic.
    """
    msgs = MessagesFalso([_mensaje("ok")])
    _cliente(msgs).redactar("sis", "usr", max_tokens=120)
    assert msgs.llamadas[0]["max_tokens"] == 120


def test_redactar_propaga_la_falla_del_proveedor() -> None:
    msgs = MessagesFalso(excepcion=RuntimeError("rate_limit_error"))
    with pytest.raises(RuntimeError, match="rate_limit_error"):
        _cliente(msgs).redactar("s", "u")


# --- Esfuerzo ----------------------------------------------------------------

def test_usa_esfuerzo_bajo_por_defecto() -> None:
    """En Opus 5 el thinking viene ENCENDIDO y sus tokens se facturan como salida.

    Nuestras dos llamadas son mecánicas: clasificar una consulta y redactar
    cinco oraciones a partir de números ya calculados. Dejar el esfuerzo por
    defecto (`high`) multiplicaría la factura sin que ninguna métrica del golden
    set suba.

    Es una hipótesis, no un dogma — por eso es parámetro y no constante: el
    esfuerzo es un eje más que la comparación puede barrer y medir.
    """
    msgs = MessagesFalso([_mensaje('{"intencion": "x"}')])
    _cliente(msgs).estructurado("sis", "usr", ESQUEMA)
    assert msgs.llamadas[0]["output_config"]["effort"] == "low"


def test_el_esfuerzo_es_configurable() -> None:
    msgs = MessagesFalso([_mensaje("ok")])
    _cliente(msgs, esfuerzo="high").redactar("sis", "usr")
    assert msgs.llamadas[0]["output_config"]["effort"] == "high"


def test_un_esfuerzo_invalido_falla_al_construir() -> None:
    """Un typo tiene que romper acá y no a mitad de una corrida paga.

    Descubrir que `efort="hihg"` cayó al default después de gastar quince
    llamadas es exactamente el defecto de instrumento que ya nos costó caro.
    """
    with pytest.raises(ValueError, match="hihg"):
        _cliente(esfuerzo="hihg")


# --- Uso y costo -------------------------------------------------------------

def test_el_uso_arranca_en_cero() -> None:
    assert _cliente().uso() == Uso(tokens_entrada=0, tokens_salida=0,
                                   tokens_cacheados=0, llamadas=0)


def test_acumula_el_uso_de_cada_llamada() -> None:
    """Sin acumular, el costo de una corrida habría que reconstruirlo a mano.

    El eval llama al modelo dos veces por caso y quince casos por corrida: son
    treinta llamadas cuyos tokens tienen que sumarse en algún lado.
    """
    msgs = MessagesFalso([
        _mensaje('{"intencion": "x"}', entrada=800, salida=12),
        _mensaje("una conclusión", entrada=2000, salida=140),
    ])
    cliente = _cliente(msgs)
    cliente.estructurado("sis", "usr", ESQUEMA)
    cliente.redactar("sis", "usr")

    assert cliente.uso() == Uso(tokens_entrada=2800, tokens_salida=152,
                                tokens_cacheados=0, llamadas=2)


def test_registra_los_tokens_leidos_de_cache_aparte() -> None:
    """Un token cacheado cuesta ~10% de uno normal: sumarlos juntos infla el costo.

    Se miden aparte incluso sabiendo que hoy no aplican —nuestros prefijos fijos
    quedan por debajo del mínimo cacheable— porque el día que apliquen, el
    número tiene que estar bien sin tocar el instrumento.
    """
    m = _mensaje('{"intencion": "x"}', entrada=100, salida=10)
    m.usage.cache_read_input_tokens = 700
    cliente = _cliente(MessagesFalso([m]))
    cliente.estructurado("sis", "usr", ESQUEMA)

    assert cliente.uso().tokens_cacheados == 700
    assert cliente.uso().tokens_entrada == 100


def test_el_uso_se_acumula_aunque_la_llamada_devuelva_algo_invalido() -> None:
    """Una respuesta que no cumple el esquema igual se cobra.

    Un contador que solo suma los éxitos subestima la factura, y la subestima
    justo en las corridas que salieron mal — que son las que uno repite.
    """
    msgs = MessagesFalso([_mensaje('"no es un dict"', entrada=500, salida=8)])
    cliente = _cliente(msgs)
    with pytest.raises(TypeError):
        cliente.estructurado("sis", "usr", ESQUEMA)

    assert cliente.uso() == Uso(tokens_entrada=500, tokens_salida=8,
                                tokens_cacheados=0, llamadas=1)


# --- Salud -------------------------------------------------------------------

def test_disponible_cuando_el_proveedor_contesta() -> None:
    assert _cliente().disponible() is True


def test_no_disponible_si_las_credenciales_fallan() -> None:
    """Sin key válida, `replay` tiene que cortar antes de producir capturas vacías.

    Ese chequeo existe porque un entorno incompleto no falla ruidosamente:
    produce salidas plausibles y huecas.
    """
    cliente = ClienteAnthropic(modelo="claude-opus-5",
                               cliente=AnthropicFalso(modelos_ok=False))
    assert cliente.disponible() is False


# --- Selección del backend ---------------------------------------------------

def test_se_puede_elegir_el_backend_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin selector el adaptador sería código que solo ejercitan los tests."""
    monkeypatch.setenv("LLM_BACKEND", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-de-mentira")
    assert isinstance(crear_cliente(), ClienteAnthropic)


def test_el_backend_anthropic_usa_su_propio_modelo_por_defecto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`OLLAMA_MODEL` no puede filtrarse al proveedor pago.

    `crear_cliente()` tiene `llama3.2:3b` como default del parámetro. Si el
    adaptador lo tomara, la primera corrida contra Anthropic fallaría con un
    404 de modelo inexistente — o peor, el registro anotaría `llama3.2:3b` en
    una corrida que corrió contra otra cosa.
    """
    monkeypatch.setenv("LLM_BACKEND", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-de-mentira")
    cliente = crear_cliente()
    assert cliente.nombre == MODELO_POR_DEFECTO
    assert cliente.nombre.startswith("claude-")


def test_el_modelo_de_anthropic_se_puede_elegir_por_entorno(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Barrer modelos es el objetivo del ejercicio: tiene que ser configuración.

    Si el modelo estuviera hardcodeado, comparar Opus contra Haiku sería editar
    código entre corrida y corrida — y el registro no podría distinguir qué
    cambió entre las dos.
    """
    monkeypatch.setenv("LLM_BACKEND", "anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-de-mentira")
    assert crear_cliente().nombre == "claude-haiku-4-5"


def test_los_tres_backends_cumplen_el_mismo_puerto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Intercambiables de verdad: los nodos no pueden notar la diferencia.

    Este es EL test del ejercicio. Dos adaptadores contra el mismo backend
    podían compartir un sesgo hacia Ollama sin que se notara; el tercero, contra
    otro proveedor, es el que convierte al puerto en un hecho verificado.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-de-mentira")
    for backend in ("httpx", "langchain", "anthropic"):
        monkeypatch.setenv("LLM_BACKEND", backend)
        assert isinstance(crear_cliente(), ClienteLLM)


# --- Contar tokens sin gastar ------------------------------------------------

def test_contar_tokens_devuelve_el_conteo_del_proveedor() -> None:
    """Es la herramienta más útil del adaptador: el endpoint es GRATIS.

    Permite calcular el costo exacto de una corrida antes de gastar un centavo,
    con el tokenizador del proveedor en vez de una regla de tres sobre
    caracteres — que fue con lo que se estimó la primera vez, y erró por 4x.
    """
    msgs = MessagesFalso()
    assert _cliente(msgs).contar_tokens("sis", "usr") == 2_346


def test_contar_tokens_incluye_el_esquema() -> None:
    """El esquema viaja en el request y ocupa tokens.

    Contar sin él daría un número más chico que la llamada real. Y el error
    hacia abajo es el peligroso: se descubre con la factura.
    """
    msgs = MessagesFalso()
    _cliente(msgs).contar_tokens("sis", "usr", ESQUEMA)

    enviado = msgs.conteos[0]["output_config"]["format"]["schema"]
    assert enviado["properties"] == ESQUEMA["properties"]
    assert enviado["additionalProperties"] is False


def test_contar_tokens_no_suma_al_uso_facturado() -> None:
    """`count_tokens` no se factura: sumarlo inflaría el costo de cada corrida.

    Un instrumento que se cobra a sí mismo mide otra cosa que la que dice.
    """
    cliente = _cliente()
    cliente.contar_tokens("sis", "usr", ESQUEMA)
    assert cliente.uso() == Uso()


# --- Contra la API real ------------------------------------------------------
#
# Todo lo de arriba prueba la traducción contra dobles: rápido, determinístico y
# ciego a lo único que importa acá. Un doble acepta cualquier cosa que le
# mandemos; lo que ningún doble puede decir es si la API la ACEPTA.
#
# Esa distinción ya nos costó una sesión: 21 tests unitarios en verde y un
# TypeError contra Ollama real. ADR-007 lo dejó escrito y estos tests son la
# misma disciplina aplicada al tercer adaptador.
#
# Marcados `llm`: quedan fuera de la corrida por defecto. Se corren con
# `pytest tests/test_agent_llm_anthropic.py -m llm` cuando se toca este
# adaptador o se sube la versión del SDK.
#
# El primero de los tres NO consume tokens: `count_tokens` es gratis. Es el que
# hay que correr primero el día que aparezca la key.

@pytest.fixture(scope="module")
def cliente_real() -> ClienteAnthropic:
    from agent.llm_anthropic import hay_credenciales

    if not hay_credenciales():
        pytest.skip("sin ANTHROPIC_API_KEY: no hay contra qué correr")
    return ClienteAnthropic()


@pytest.mark.llm
def test_real_contar_tokens_no_cuesta_nada_y_devuelve_un_numero(
    cliente_real: ClienteAnthropic,
) -> None:
    """El único test de esta sección que se puede correr sin crédito cargado.

    Confirma dos cosas de una: que las credenciales sirven, y que el conteo con
    el esquema cerrado no es rechazado por la API.
    """
    tokens = cliente_real.contar_tokens(
        "Clasificá la consulta. Respondé solo con el JSON pedido.",
        "¿Cómo vienen las ventas de P001 en los últimos 30 días?",
        ESQUEMA,
    )
    assert tokens > 0
    assert cliente_real.uso() == Uso()


@pytest.mark.llm
def test_real_la_api_acepta_nuestros_esquemas_cerrados(
    cliente_real: ClienteAnthropic,
) -> None:
    """LA pregunta que ningún doble contesta: ¿la API acepta lo que mandamos?

    `_cerrar()` agrega `additionalProperties: False` porque las salidas
    estructuradas lo quieren. Eso es una hipótesis hasta que la API la confirme.
    Si este test falla con un 400, el que está mal es el adaptador, no la API.

    No se compara el VALOR devuelto: que el modelo acierte la intención es otra
    pregunta, y la contesta el golden set.
    """
    salida = cliente_real.estructurado(
        "Clasificá la consulta. Respondé solo con el JSON pedido.",
        "¿Cómo vienen las ventas de P001 en los últimos 30 días?",
        ESQUEMA,
    )
    assert isinstance(salida, dict)
    assert isinstance(salida["intencion"], str)
    assert cliente_real.uso().llamadas == 1
    assert cliente_real.uso().tokens_entrada > 0


@pytest.mark.llm
def test_real_redactar_devuelve_texto_plano_y_reporta_uso(
    cliente_real: ClienteAnthropic,
) -> None:
    """Con thinking activo la respuesta trae bloques que no son texto.

    Si `_texto()` filtrara mal, el razonamiento del modelo terminaría adentro
    del informe. Es el modo de falla más silencioso que tiene este adaptador:
    el informe sale, se ve raro, y nadie sabe por qué hasta que lo lee alguien.
    """
    antes = cliente_real.uso().llamadas
    salida = cliente_real.redactar(
        "Respondé en una sola oración corta, en español.",
        "Resumí: las ventas de P001 cayeron 12% en marzo.",
        max_tokens=60,
    )
    assert isinstance(salida, str)
    assert salida.strip()
    assert "thinking" not in salida.lower()
    assert cliente_real.uso().llamadas == antes + 1
