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
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

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

# Hostnames que corresponden a una base en la misma máquina o en la red privada
# de Compose. Solo para estos se acepta el certificado del servidor sin
# validarlo: SQL Server se autofirma y no hay una CA en el medio.
#
# `sqlserver` y `ami-sqlserver` son el nombre del servicio y del container en
# docker-compose.yml. Adentro de esa red el tráfico no sale del host, así que
# valen lo mismo que localhost — sin ellos, API y worker dejarían de conectar.
HOSTS_LOCALES = frozenset({
    "localhost", "127.0.0.1", "::1", "(local)", ".",
    "sqlserver", "ami-sqlserver",
})


def _variable_requerida(nombre: str) -> str:
    valor = os.getenv(nombre)
    if not valor:
        raise RuntimeError(
            f"{nombre} no está definida. Las credenciales de base no tienen "
            "valor por defecto: definilas en el entorno (ver env.example)."
        )
    return valor


def es_servidor_local(servidor: str = SERVIDOR) -> bool:
    """Indica si la cadena de servidor apunta a esta máquina o a la red de Compose.

    Se normaliza lo que ODBC admite como destino: `host,puerto`,
    `host\\instancia` y `[::1]`. Cualquier otra cosa se considera remota — el
    default seguro es desconfiar, porque el costo de equivocarse en un sentido
    es una conexión que falla y en el otro es un man-in-the-middle silencioso.
    """
    host = servidor.split(",")[0].strip()
    host = host.split("\\")[0].strip()
    host = host.strip("[]").lower()
    return host in HOSTS_LOCALES


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


def _encomillar(valor: str) -> str:
    """Encierra un valor entre llaves, que es el escape que define ODBC.

    Sin esto los valores se concatenan crudos y el `;` que separa parámetros
    queda a merced del contenido: una contraseña como
    ``x;TrustServerCertificate=yes`` no fallaría al autenticar — armaría una
    cadena de conexión DISTINTA de la que este código cree estar armando.

    Adentro de las llaves todo es literal salvo la llave de cierre, que se
    escapa duplicándola. Es el mismo mecanismo que ya usaba el nombre del
    driver, aplicado ahora también a los valores que vienen del entorno.
    """
    return "{" + valor.replace("}", "}}") + "}"


def _cadena(usuario: str, password: str, base: str | None = None) -> str:
    driver = driver_disponible()
    # `Server` NO se encomilla a propósito: los drivers parsean acá la forma
    # `host,puerto` y `host\instancia`, y encerrarla en llaves la volvería un
    # hostname literal con una coma adentro. No es una credencial y no viene
    # de entrada de usuario, así que no es la superficie que este escape cubre.
    partes = [
        f"Driver={{{driver}}}",
        f"Server={SERVIDOR}",
        f"UID={_encomillar(usuario)}",
        f"PWD={_encomillar(password)}",
    ]
    if base:
        partes.append(f"Database={_encomillar(base)}")
    # Solo los drivers modernos entienden estas opciones; el legacy las ignora
    # o falla.
    #
    # `TrustServerCertificate=yes` desactiva la validación del certificado del
    # servidor. Para una base local eso es aceptable —SQL Server se autofirma y
    # no hay red en el medio—, pero la condición se VERIFICA en vez de
    # asumirse: antes se aplicaba siempre, así que el día que MSSQL_SERVER
    # apuntara a un host remoto la conexión seguía aceptando cualquier
    # certificado, que es un man-in-the-middle sobre credenciales de base.
    #
    # Contra un servidor remoto se exige cifrado con validación. Si el
    # certificado no es confiable, la conexión falla — que es exactamente lo
    # que tiene que pasar.
    if driver.startswith("ODBC Driver"):
        partes.append(
            "TrustServerCertificate=yes" if es_servidor_local(SERVIDOR)
            else "Encrypt=yes"
        )
    return ";".join(partes)


def conectar_admin(base: str | None = BASE) -> pyodbc.Connection:
    """Conexión con permisos de escritura. SOLO para ETL y migraciones.

    Nunca debe usarse desde una tool del agente ni desde un endpoint de la API.
    """
    return pyodbc.connect(
        _cadena("sa", _variable_requerida("MSSQL_SA_PASSWORD"), base),
        timeout=15,
    )


def conectar_lectura(base: str | None = BASE) -> pyodbc.Connection:
    """Conexión de solo lectura. Es la que usan las tools del agente.

    Los permisos los hace cumplir el motor, no este código.
    """
    # La contraseña se EXIGE, igual que la de `sa`. Antes tenía el valor real
    # como default: un entorno sin `MSSQL_APP_PASSWORD` no fallaba, se conectaba
    # en silencio con una credencial escrita en el repositorio. El fallo que
    # debería abortar el arranque quedaba disfrazado de sistema sano.
    #
    # `MSSQL_APP_USER` sí conserva su default: es un nombre de usuario, no un
    # secreto, y coincide con el que crea `infra/sql/02_readonly_user.sql`.
    return pyodbc.connect(
        _cadena(
            os.getenv("MSSQL_APP_USER", "ami_reader"),
            _variable_requerida("MSSQL_APP_PASSWORD"),
            base,
        ),
        timeout=15,
        readonly=True,
    )


@contextmanager
def cursor_lectura() -> Iterator[Any]:
    """Cursor de solo lectura con cierre garantizado."""
    con = conectar_lectura()
    try:
        yield con.cursor()
    finally:
        con.close()


def hay_base_disponible() -> bool:
    """Indica si la base responde con la misma identidad read-only del agente.

    Este chequeo también alimenta el health de la API. Usar ``sa`` acá
    obligaría a entregar una credencial administrativa al runtime solo para
    ejecutar ``SELECT 1`` y daría verde aunque el usuario real estuviera roto.
    """
    try:
        con = conectar_lectura()
        con.close()
        return True
    except Exception:
        return False
