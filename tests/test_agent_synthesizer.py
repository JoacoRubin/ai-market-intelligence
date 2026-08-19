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

from agent.llm import ClienteOllama, ClientePredecible
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
