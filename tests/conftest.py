"""Configuración compartida de los tests.

El punto importante es el fixture `sin_llm_real`: desde que la API ejecuta el
grafo del agente, **cualquier** test que llame a `POST /analyses` invocaría a
Ollama y esperaría los minutos que tarda la inferencia en CPU.

Los tests de la API no están para medir al modelo —eso lo hace el golden set—
sino para verificar contratos, códigos de estado y ciclo de vida del recurso.
Por eso se les inyecta un doble por defecto. Los que necesiten un
comportamiento específico lo sobrescriben con `dependency_overrides`.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


# --- .env, antes de importar nada que lea el entorno -------------------------
#
# `core.db` exige `MSSQL_SA_PASSWORD` por entorno desde que dejó de tenerla
# hardcodeada. El endurecimiento está bien; lo que faltaba era el camino para
# el desarrollador: `tasks.ps1` carga `.env`, pero no en sus tareas de test, y
# un IDE o un `uv run pytest` no pasan por ahí. Sin esto, doce tests fallan con
# un RuntimeError que enuncia la doctrina y no dice "copiá env.example a .env".
#
# Se lee con la biblioteca estándar y no con python-dotenv: son diez líneas y
# no justifican una dependencia que además terminaría en el wheel.
#
# Una variable ya presente en el proceso GANA sobre el archivo — mismo criterio
# que `Import-ProjectEnv` en `tasks.ps1`—, así se puede usar una credencial
# efímera sin editar nada. Y va solo en los tests: el código de producción
# nunca lee un archivo de credenciales por su cuenta.
def _cargar_env() -> None:
    ruta = Path(__file__).resolve().parent.parent / ".env"
    if not ruta.is_file():
        return
    for linea in ruta.read_text(encoding="utf-8-sig").splitlines():
        limpia = linea.strip()
        if not limpia or limpia.startswith("#") or "=" not in limpia:
            continue
        nombre, _, valor = limpia.partition("=")
        os.environ.setdefault(nombre.strip(), valor)


_cargar_env()

from agent.llm import ClientePredecible  # noqa: E402
from apps.api.main import app, obtener_cliente_llm  # noqa: E402


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
