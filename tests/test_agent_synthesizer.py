"""Tests del contrato de salida del synthesizer.

El synthesizer es **el 76% del tiempo de una consulta**. Medido sobre las cinco
capturas de replay: 506,7 s de 665,5 s totales. El router se lleva otro 17,6%,
y entre los dos —los únicos dos nodos que llaman al modelo— suman el 94%. SQL
es el 0,6% y el planner es 0,0%.

Eso significa que cualquier cosa que se le pida de más al modelo acá se paga en
segundos reales, y que este archivo protege el lugar más caro del sistema.

No corre contra la base ni contra el modelo: arma el estado a mano.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.llm import ClienteOllama, ClientePredecible, ClienteQueFalla
from agent.nodes.synthesizer import ESQUEMA, MAX_CONCLUSIONES, sintetizar
from agent.state import AnalysisState
from core.report import MetricaProducto


def _estado() -> AnalysisState:
    return AnalysisState(
        request_id="test-synth",
        consulta="Compará P002 y P003 en los últimos 90 días",
        resultados_tools={"product_metrics": {
            "P002": MetricaProducto(
                product_id="P002", nombre="Alfa", unidades=30, revenue=8929.8,
                margen_pct=28.2, fuente="sql:product_metrics"),
        }},
    )


# --- El techo del array ------------------------------------------------------

def test_el_esquema_acota_el_array_de_conclusiones() -> None:
    """`maxItems` va en el esquema porque es lo único que el modelo OBEDECE.

    Medido contra `llama3.2:3b` sobre el prompt real del caso más caro
    (`cmp-01`, 6.521 chars):

        sin techo                   129,2 s   6 conclusiones   412 tokens   OK
        num_predict=400             114,6 s   0 conclusiones   400 tokens   JSON TRUNCADO
        num_predict=400 + maxItems  104,2 s   5 conclusiones   337 tokens   OK

    Sin `maxItems` el modelo genera **seis** conclusiones y el código se queda
    con cinco: se pagan segundos de CPU por una que se tira. Con `maxItems`
    cierra solo en 337 tokens y nunca llega al techo.

    Se compara contra `MAX_CONCLUSIONES` y no contra un `5` literal a propósito:
    son dos expresiones del mismo límite y tienen que decir lo mismo. Si alguien
    sube la constante a 8 y se olvida del esquema, el sistema vuelve en silencio
    a pedirle 5 al modelo —el informe sale más pobre sin que nada falle— y este
    test es lo único que ata las dos puntas.
    """
    assert ESQUEMA["properties"]["conclusiones"]["maxItems"] == MAX_CONCLUSIONES


def test_sigue_cortando_en_python_aunque_el_modelo_devuelva_de_mas() -> None:
    """`maxItems` es una instrucción, no una garantía. El corte se queda.

    Un modelo puede ignorar el esquema, y el día que lo haga el informe no
    puede crecer sin límite. Defensa en profundidad: el esquema ahorra CPU, el
    slice sostiene el contrato.
    """
    cliente = ClientePredecible(
        conclusiones=[f"Conclusión número {i}." for i in range(MAX_CONCLUSIONES + 3)]
    )
    estado = sintetizar(_estado(), cliente)

    assert estado.informe is not None
    assert len(estado.informe.resumen_ejecutivo) <= MAX_CONCLUSIONES


# --- El respaldo tiene que decir POR QUE ---------------------------------------


def test_el_respaldo_por_una_excepcion_nombra_la_causa() -> None:
    """La advertencia generica esconde el diagnostico. Medido en produccion.

    El 2026-08-27, con el stack levantado, el sintetizador tardo 300.328 ms
    —`TIMEOUT_SEGUNDOS` clavado— y el informe salio del respaldo. La unica
    senal hacia afuera era "el modelo no produjo conclusiones utilizables",
    que es verdad y no alcanza para hacer nada: no distingue un timeout de un
    modelo que contesto una lista vacia.

    El `except` capturaba la excepcion en `estado.error`, un campo que NADIE
    lee: el `Report` no lo tiene y `registro.error` solo se escribe si revienta
    el grafo entero. El `ReadTimeout`, con su mensaje, se descartaba.

    Un error capturado y no reportado es peor que uno que no se captura: el
    sistema aparenta andar mientras quema cinco minutos de CPU por analisis.
    """
    cliente = ClienteQueFalla(TimeoutError("se acabo el tiempo"))

    estado = sintetizar(_estado(), cliente)

    assert estado.informe is not None
    unida = " ".join(estado.informe.advertencias)
    assert "TimeoutError" in unida
    assert "se acabo el tiempo" in unida


def test_el_respaldo_sin_excepcion_no_inventa_una_causa() -> None:
    """La contracara: el modelo contesto, pero no dio nada usable.

    Son dos fallas distintas con dos arreglos distintos —una es de
    infraestructura, la otra es del prompt— y la advertencia tiene que
    permitir distinguirlas sin adivinar.
    """
    class _ClienteSinConclusiones:
        """Contesta bien, pero con el array vacio.

        No se usa `ClientePredecible([])` porque hace `conclusiones or [...]`:
        una lista vacia cae al default y el respaldo nunca se dispararia.
        """

        nombre = "falso:vacio"

        def estructurado(self, *_a: Any, **_k: Any) -> dict[str, Any]:
            return {"conclusiones": []}

        def redactar(self, *_a: Any, **_k: Any) -> str:
            return ""

    estado = sintetizar(_estado(), _ClienteSinConclusiones())

    assert estado.informe is not None
    unida = " ".join(estado.informe.advertencias)
    assert "no produjo conclusiones utilizables" in unida
    assert "Error" not in unida


# --- Lo que NO se hace, y por qué --------------------------------------------

def test_estructurado_no_acota_los_tokens_de_salida(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`num_predict` en `estructurado` DECAPITA el JSON. Medido, no supuesto.

    Es la "optimización" obvia y es la trampa: el modelo no sabe que hay un
    techo, genera hasta que lo cortan, y a los 400 tokens la respuesta queda
    partida al medio. `json.loads` falla, el synthesizer cae al respaldo
    determinístico, los informes salen más secos y la métrica del eval baja.

    Un cambio que parece una mejora, no rompe ningún test unitario, y degrada
    el sistema en silencio. El límite correcto va en el esquema, donde el
    modelo lo lee y cierra solo.

    `redactar` sí lo lleva, y ahí está bien: es prosa libre, y cortarla de más
    produce un texto más corto, no un texto inválido.
    """
    capturado: dict[str, Any] = {}

    def _chat_falso(payload: dict[str, Any]) -> dict[str, Any]:
        capturado.update(payload)
        return {"message": {"content": "{}"}}

    cliente = ClienteOllama()
    monkeypatch.setattr(cliente, "_chat", _chat_falso)
    cliente.estructurado("sistema", "usuario", ESQUEMA)

    assert "num_predict" not in capturado["options"]


def test_estructurado_apaga_el_razonamiento_en_voz_alta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El techo que SI corresponde acá no es de tokens: es apagar el think.

    Ablacion sobre el prompt real del sintetizador, una variable por vez, con
    grupo de control (2026-08-27):

        qwen3:4b  como estaba        312,2 s   FALLO ReadTimeout
        qwen3:4b  think=False         60,9 s   112 tokens    3 conclusiones  OK
        qwen3:4b  num_predict=400    194,9 s   400 tokens    JSON TRUNCADO
        llama3.2:3b (control)        132,0 s   174 tokens    4 conclusiones  OK

    El tiempo NO se iba escribiendo JSON —son ~112 tokens— se iba razonando
    antes. `maxItems` no lo alcanza porque el razonamiento no es parte del
    array, y `num_predict` lo empeora: gasta 1.516 caracteres pensando, se
    queda sin presupuesto y decapita la respuesta. Es el mismo hallazgo que
    ya documenta el test de arriba, ahora medido contra un modelo que piensa.

    Con `think=False`, `qwen3:4b` queda MAS rapido que `llama3.2:3b`, el
    modelo con el que se midio el golden set.

    Es seguro mandarlo siempre: `llama3.2:3b` —que no tiene la capacidad—
    devuelve HTTP 200 en 5,9 s y lo ignora. Un flag que hay que acordarse de
    poner solo para algunos modelos es uno que se olvida.
    """
    capturado: dict[str, Any] = {}

    def _chat_falso(payload: dict[str, Any]) -> dict[str, Any]:
        capturado.update(payload)
        return {"message": {"content": "{}"}}

    cliente = ClienteOllama()
    monkeypatch.setattr(cliente, "_chat", _chat_falso)
    cliente.estructurado("sistema", "usuario", ESQUEMA)

    assert capturado["think"] is False


def test_redactar_si_acota_los_tokens_de_salida(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La contracara del test anterior: en prosa el techo sí corresponde."""
    capturado: dict[str, Any] = {}

    def _chat_falso(payload: dict[str, Any]) -> dict[str, Any]:
        capturado.update(payload)
        return {"message": {"content": "texto"}}

    cliente = ClienteOllama()
    monkeypatch.setattr(cliente, "_chat", _chat_falso)
    cliente.redactar("sistema", "usuario", max_tokens=120)

    assert capturado["options"]["num_predict"] == 120
