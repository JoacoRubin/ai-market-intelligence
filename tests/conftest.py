"""Configuración compartida de los tests.

El punto importante es el fixture `sin_llm_real`: desde que la API ejecuta el
grafo del agente, **cualquier** test que llame a `POST /analyses` invocaría a
Ollama y esperaría los minutos que tarda la inferencia en CPU.

Los tests de la API no están para medir al modelo —eso lo hace el golden set—
sino para verificar contratos, códigos de estado y ciclo de vida del recurso.
Por eso se les inyecta un doble por defecto. Los que necesiten un
comportamiento específico lo sobrescriben con `dependency_overrides`.
"""

from collections.abc import Iterator

import pytest

from agent.llm import ClientePredecible
from apps.api.main import app, obtener_cliente_llm


@pytest.fixture(autouse=True)
def sin_llm_real() -> Iterator[None]:
    """Impide que un test toque el modelo real sin quererlo.

    Es `autouse` a propósito: si hubiera que acordarse de pedirlo, alcanzaría
    con olvidarlo una vez para que la suite pasara de segundos a media hora, y
    nadie sabría cuál test lo causó.
    """
    app.dependency_overrides.setdefault(
        obtener_cliente_llm, lambda: ClientePredecible()
    )
    yield
    app.dependency_overrides.clear()
