"""Regresiones de seguridad para el runtime local en Docker.

Estos tests inspeccionan configuracion como texto a proposito: no requieren un
daemon de Docker, no interpolan ``.env`` y, por lo tanto, tampoco imprimen
secretos en CI.
"""

from pathlib import Path
from typing import Final

import pyodbc
import pytest

import core.db as db

RAIZ: Final = Path(__file__).resolve().parent.parent


def _bloque_servicio(compose: str, nombre: str) -> str:
    """Extrae un servicio de primer nivel sin depender de PyYAML."""
    lineas = compose.splitlines(keepends=True)
    inicio = next(
        indice for indice, linea in enumerate(lineas) if linea.rstrip() == f"  {nombre}:"
    )
    fin = next(
        (
            indice
            for indice, linea in enumerate(lineas[inicio + 1 :], inicio + 1)
            if linea.startswith("  ") and not linea.startswith("    ")
        ),
        len(lineas),
    )
    return "".join(lineas[inicio:fin])


def test_redis_no_publica_puertos_al_host() -> None:
    compose = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")

    redis = _bloque_servicio(compose, "redis")

    assert "ports:" not in redis
    assert "6379:6379" not in redis


def test_servicios_publicos_solo_bindearon_loopback() -> None:
    compose = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")

    sqlserver = _bloque_servicio(compose, "sqlserver")
    api = _bloque_servicio(compose, "api")

    assert '"127.0.0.1:1433:1433"' in sqlserver
    assert '"127.0.0.1:8000:8000"' in api


def test_api_y_worker_reciben_solo_variables_permitidas() -> None:
    compose = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")

    for nombre in ("api", "worker"):
        servicio = _bloque_servicio(compose, nombre)
        assert "env_file:" not in servicio
        assert "MSSQL_SA_PASSWORD" not in servicio
        assert "MSSQL_APP_USER:" in servicio
        assert "MSSQL_APP_PASSWORD:" in servicio


def test_runtime_python_declara_usuario_no_root() -> None:
    dockerfile = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    runtime = dockerfile.split("FROM python:3.13-slim AS runtime", maxsplit=1)[1]

    assert "USER app:app" in runtime
    assert runtime.index("USER app:app") < runtime.index("CMD [")


def test_cache_de_huggingface_no_depende_del_home_de_root() -> None:
    compose = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = (RAIZ / "Dockerfile").read_text(encoding="utf-8")

    for nombre in ("api", "worker"):
        servicio = _bloque_servicio(compose, nombre)
        assert "hf_cache:/home/app/.cache/huggingface" in servicio
        assert "/root/.cache/huggingface" not in servicio
    assert "HF_HOME=/home/app/.cache/huggingface" in dockerfile


def test_health_de_db_usa_conexion_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    usada = False

    class Conexion:
        def close(self) -> None:
            nonlocal usada
            usada = True

    monkeypatch.setattr(db, "conectar_lectura", lambda base=None: Conexion())

    def admin_prohibido(base: str | None = None) -> None:
        raise AssertionError("health no debe usar la credencial administrativa")

    monkeypatch.setattr(db, "conectar_admin", admin_prohibido)

    assert db.hay_base_disponible() is True
    assert usada is True


def test_health_de_db_degrada_si_falla_el_lector(monkeypatch: pytest.MonkeyPatch) -> None:
    def lector_caido(base: str | None = None) -> None:
        raise RuntimeError("SQL Server no disponible")

    monkeypatch.setattr(db, "conectar_lectura", lector_caido)

    assert db.hay_base_disponible() is False


def test_admin_requiere_credencial_explicita(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MSSQL_SA_PASSWORD", raising=False)
    monkeypatch.setattr(
        db, "driver_disponible", lambda: "ODBC Driver 18 for SQL Server"
    )
    monkeypatch.setattr(pyodbc, "connect", lambda *args, **kwargs: object())

    with pytest.raises(RuntimeError, match="MSSQL_SA_PASSWORD no está definida"):
        db.conectar_admin()


# --- secretos fuera de la configuración versionada ---------------------------
#
# Mismo criterio que el resto de este archivo: se inspecciona configuración como
# TEXTO. No hace falta un daemon de Docker ni una corrida de Actions, y una
# credencial que vuelva a aparecer rompe el test en el commit que la introduce
# —no seis meses después, cuando alguien audite el repo.

CREDENCIALES_QUE_NO_DEBEN_VOLVER = (
    "Reader_Local_2026!",
    "Dev_Local_2026!",
    "Dev_CI_Only_2026!",
)


def test_el_compose_no_trae_credenciales_por_defecto() -> None:
    """`:-` daba un arranque silencioso con una contraseña versionada.

    Con `:?` el stack falla y dice qué falta. Un sistema que levanta con una
    credencial que cualquiera que leyó el repo conoce es peor que uno que no
    levanta: el segundo se arregla en dos minutos, el primero no se nota.
    """
    compose = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")

    for credencial in CREDENCIALES_QUE_NO_DEBEN_VOLVER:
        assert credencial not in compose

    assert "${MSSQL_SA_PASSWORD:?" in compose
    assert "${MSSQL_APP_PASSWORD:?" in compose


def test_el_script_del_usuario_readonly_no_trae_la_password_adentro() -> None:
    """La contraseña entra por `-v APP_PASSWORD`, no versionada en el .sql."""
    script = (RAIZ / "infra" / "sql" / "02_readonly_user.sql").read_text(
        encoding="utf-8"
    )

    for credencial in CREDENCIALES_QUE_NO_DEBEN_VOLVER:
        assert credencial not in script

    assert "$(APP_PASSWORD)" in script
    # Sin `:setvar` con default: si la variable no se pasa, sqlcmd aborta en vez
    # de crear el login con un valor de relleno.
    assert ":setvar APP_PASSWORD" not in script


def test_el_workflow_de_ci_no_trae_credenciales_literales() -> None:
    ci = (RAIZ / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    for credencial in CREDENCIALES_QUE_NO_DEBEN_VOLVER:
        assert credencial not in ci

    assert "secrets.CI_MSSQL_SA_PASSWORD" in ci
    assert "secrets.CI_MSSQL_APP_PASSWORD" in ci


def test_el_workflow_de_ci_declara_permisos_minimos() -> None:
    """Sin este bloque el GITHUB_TOKEN hereda lo que diga la config del repo.

    En repositorios creados antes del cambio de default de GitHub eso es
    read-and-write sobre todos los scopes, para un workflow que solo lee el
    código y corre tests.
    """
    ci = (RAIZ / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "\npermissions:\n  contents: read\n" in ci


def test_el_workflow_de_ci_no_usa_pull_request_target() -> None:
    """El disparador que corre con el token del repo base sobre código ajeno.

    Combinado con un checkout de la rama del PR es la vía clásica de
    exfiltración de secretos en Actions. Hoy no se usa; este test lo mantiene
    así.
    """
    ci = (RAIZ / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "pull_request_target:" not in ci
