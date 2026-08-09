"""Conexión a SQL Server.

Dos roles bien separados, y la separación es deliberada:

- `conectar_admin()`  -> usuario `sa`. Solo para el ETL de carga y las migraciones.
- `conectar_lectura()` -> usuario `ami_reader`. Es el ÚNICO que ven las tools
  del agente. No puede escribir, y no puede leer la tabla de ground truth.

Que las dos funciones vivan en el mismo módulo hace visible la asimetría: si
alguien algún día usa `conectar_admin` desde una tool del agente, se ve en el
import y salta en la revisión. Esconder la conexión privilegiada detrás de un
único `get_connection()` genérico es cómodo hasta el día que no lo es.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from functools import lru_cache

import pyodbc

# Orden de preferencia. El 18 es el driver actual de Microsoft; "SQL Server" es
# el legacy de Windows: funciona, pero está deprecado y maneja mal DATETIME2.
# Se usa solo como último recurso para no bloquear el desarrollo.
DRIVERS_PREFERIDOS = (
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "SQL Server",
)

SERVIDOR = os.getenv("MSSQL_SERVER", "localhost,1433")
BASE = os.getenv("MSSQL_DB", "ami")


@lru_cache(maxsize=1)
def driver_disponible() -> str:
    """Devuelve el mejor driver ODBC instalado, en orden de preferencia."""
    instalados = set(pyodbc.drivers())
    for driver in DRIVERS_PREFERIDOS:
        if driver in instalados:
            return driver
    raise RuntimeError(
        "No hay ningún driver ODBC de SQL Server instalado. "
        "Instalá el ODBC Driver 18: winget install Microsoft.msodbcsql.18"
    )


def _cadena(usuario: str, password: str, base: str | None = None) -> str:
    driver = driver_disponible()
    partes = [
        f"Driver={{{driver}}}",
        f"Server={SERVIDOR}",
        f"UID={usuario}",
        f"PWD={password}",
    ]
    if base:
        partes.append(f"Database={base}")
    # Solo los drivers modernos entienden estas opciones; el legacy las ignora
    # o falla. El servidor es local, por eso confiar en su certificado es
    # aceptable acá y NO lo sería en cualquier otro contexto.
    if driver.startswith("ODBC Driver"):
        partes.append("TrustServerCertificate=yes")
    return ";".join(partes)


def conectar_admin(base: str | None = BASE) -> pyodbc.Connection:
    """Conexión con permisos de escritura. SOLO para ETL y migraciones.

    Nunca debe usarse desde una tool del agente ni desde un endpoint de la API.
    """
    return pyodbc.connect(
        _cadena("sa", os.getenv("MSSQL_SA_PASSWORD", "Dev_Local_2026!"), base),
        timeout=15,
    )


def conectar_lectura(base: str | None = BASE) -> pyodbc.Connection:
    """Conexión de solo lectura. Es la que usan las tools del agente.

    Los permisos los hace cumplir el motor, no este código.
    """
    return pyodbc.connect(
        _cadena(
            os.getenv("MSSQL_APP_USER", "ami_reader"),
            os.getenv("MSSQL_APP_PASSWORD", "Reader_Local_2026!"),
            base,
        ),
        timeout=15,
        readonly=True,
    )


@contextmanager
def cursor_lectura():
    """Cursor de solo lectura con cierre garantizado."""
    con = conectar_lectura()
    try:
        yield con.cursor()
    finally:
        con.close()


def hay_base_disponible() -> bool:
    """Indica si SQL Server responde. Se usa para saltear tests que la requieren."""
    try:
        con = conectar_admin(base=None)
        con.close()
        return True
    except Exception:
        return False
