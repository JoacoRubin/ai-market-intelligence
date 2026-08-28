"""Contratos de onboarding, CI, packaging y documentación.

Son pruebas estáticas a propósito: no levantan Docker, no instalan dependencias
y no construyen el wheel. Verifican que los comandos públicos del repositorio
describan lo que realmente ejecutan.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _leer(ruta: str) -> str:
    return (RAIZ / ruta).read_text(encoding="utf-8-sig")


def test_tasks_es_portable_y_resuelve_uv_desde_path() -> None:
    tasks = _leer("tasks.ps1")

    assert "C:\\Users\\" not in tasks
    assert "Get-Command uv" in tasks
    assert "UV_PROJECT_ENVIRONMENT =" not in tasks


def test_db_up_solo_levanta_sqlserver_y_hace_preflight() -> None:
    tasks = _leer("tasks.ps1")
    bloque = re.search(r'^\s*"db-up"\s*\{(.*?)^\s*\}', tasks, re.M | re.S)

    assert bloque is not None
    assert "Require-Docker" in bloque.group(1)
    assert "Require-EnvFile" in bloque.group(1)
    assert "docker compose up -d sqlserver" in bloque.group(1)
    assert "docker compose up -d\n" not in bloque.group(1)


def test_init_seed_y_shell_importan_la_password_sa_sin_hardcodearla() -> None:
    tasks = _leer("tasks.ps1")

    assert "Import-ProjectEnv" in tasks
    assert "Require-SaPassword" in tasks
    assert "Dev_Local_2026!" not in tasks
    for tarea in ("db-init", "db-shell", "seed"):
        bloque = re.search(
            rf'^\s*"{tarea}"\s*\{{(.*?)^\s*\}}', tasks, re.M | re.S
        )
        assert bloque is not None
        assert "Import-ProjectEnv" in bloque.group(1)
        assert "Require-SaPassword" in bloque.group(1)

    assert "-P $env:MSSQL_SA_PASSWORD" in tasks


def test_tasks_usa_el_filtro_rapido_correcto_y_no_invoca_un_modulo_ml_inexistente() -> None:
    tasks = _leer("tasks.ps1")

    assert '-m "not db and not llm"' in tasks
    assert "python -m ml.demo" not in tasks


def test_help_lista_todas_las_tareas_declaradas() -> None:
    tasks = _leer("tasks.ps1")
    ayuda = re.search(r'^\s*"help"\s*\{(.*?)^\s*\}', tasks, re.M | re.S)
    assert ayuda is not None

    declaradas = set(re.findall(r'^\s*"([a-z-]+)"\s*\{', tasks, re.M))
    declaradas.discard("help")
    for tarea in declaradas:
        assert re.search(rf'Write-Host "\s*{re.escape(tarea)}\s', ayuda.group(1)), (
            f"help no documenta la tarea '{tarea}'"
        )


def test_redis_para_tests_solo_se_publica_en_loopback_mediante_override() -> None:
    tasks = _leer("tasks.ps1")

    assert '"redis-test-up"' in tasks
    assert "127.0.0.1:6379:6379" in tasks
    assert "docker compose -f docker-compose.yml -f $override up -d redis" in tasks


def test_ci_excluye_db_y_llm_y_mide_una_cobertura_basada_en_baseline() -> None:
    ci = _leer(".github/workflows/ci.yml")

    assert '-m "not db and not llm"' in ci
    assert "--cov-fail-under=70" in ci
    for paquete in (
        "agent", "application", "apps", "core", "eval", "ml", "rag", "replay", "seeds"
    ):
        assert f"--cov={paquete}" in ci


def test_ci_ejecuta_guardrails_con_sql_server_real() -> None:
    ci = _leer(".github/workflows/ci.yml")

    assert "guardrails-db:" in ci
    assert "mcr.microsoft.com/mssql/server:2025-latest" in ci
    assert "tests/test_db_guardrails.py" in ci


def test_el_wheel_incluye_todos_los_paquetes_runtime() -> None:
    pyproject = tomllib.loads(_leer("pyproject.toml"))
    paquetes = set(pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"])

    assert paquetes == {
        "agent", "application", "apps", "core", "eval", "ml", "rag", "replay", "seeds"
    }


def test_readme_declara_los_limites_verificables_del_producto() -> None:
    readme = _leer("README.md").lower()

    assert "research público todavía no está implementado" in readme
    assert "no incluye un caso de forecast" in readme
    assert "stale respecto de head" in readme
    assert "c:\\users\\" not in readme


# --- El loop local: correr los tests no puede pedir un ritual no escrito -----
#
# DEFECTO REAL (2026-08-28). Al exigir `MSSQL_SA_PASSWORD` por entorno --que
# está bien, sacó la credencial hardcodeada-- nadie actualizó el camino por el
# que se corren los tests. `Import-ProjectEnv` quedó en `db-up`, `db-init`,
# `seed` y `agente`, pero NO en `test`, `test-fast` ni `all`.
#
# El resultado: doce tests en rojo con un RuntimeError que enuncia la doctrina
# --"la credencial administrativa debe inyectarse solo en procesos explícitos"--
# y no dice lo único accionable, que es "copiá env.example a .env". Un
# endurecimiento que rompe el loop de desarrollo y no explica cómo salir se
# desactiva solo: la gente exporta la variable a mano y se olvida de por qué.

def test_las_tareas_de_test_cargan_el_env_del_proyecto() -> None:
    """Si `db-up` necesita `.env` para levantar la base, `test` lo necesita
    igual para hablarle."""
    tasks = _leer("tasks.ps1")

    for tarea in ("test", "test-fast", "all"):
        bloque = re.search(rf'"{tarea}"\s*\{{(.*?)\n    \}}', tasks, re.S)
        assert bloque, f"no se encontró la tarea '{tarea}' en tasks.ps1"
        assert "Import-ProjectEnv" in bloque.group(1), (
            f"la tarea '{tarea}' corre pytest sin cargar .env: los tests que "
            "tocan la base van a fallar por una variable ausente"
        )


def test_conftest_carga_el_env_para_pytest_invocado_directo() -> None:
    """`tasks.ps1` no es el único camino: un IDE, `uv run pytest` o el CI local
    invocan pytest directo. El contrato se cumple en conftest o no se cumple."""
    conftest = _leer("tests/conftest.py")

    assert ".env" in conftest, (
        "conftest no carga .env: correr pytest fuera de tasks.ps1 falla por "
        "variables de entorno ausentes"
    )
