"""Tests de seguridad de la capa de datos.

Estos tests no verifican una funcionalidad: verifican que ciertas cosas
**sean imposibles**. Son los más importantes del proyecto.

El agente construye consultas a partir de lenguaje natural del usuario y de
documentos recuperados por RAG. Ninguna de esas dos fuentes es confiable. El
diseño no apuesta a que el modelo nunca se equivoque: apuesta a que cuando se
equivoque, el motor lo frene.

Un permiso que nadie testeó es un permiso que no sabés si existe. Estos tests
convierten "el usuario es read-only" de una afirmación del README en un hecho
verificado en cada corrida de CI.
"""

from collections.abc import Iterator
from typing import Any

import pyodbc
import pytest

from core.db import conectar_admin, conectar_lectura, hay_base_disponible

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not hay_base_disponible(),
        reason="SQL Server no está levantado (docker compose up -d)",
    ),
]


@pytest.fixture
def lectura() -> Iterator[Any]:
    con = conectar_lectura()
    yield con
    con.close()


# --- Lo que SÍ tiene que poder hacer ----------------------------------------

def test_lector_puede_consultar_productos(lectura: Any) -> None:
    """Si esto falla, el agente no puede trabajar: sin SELECT no hay KPIs."""
    lectura.cursor().execute("SELECT COUNT(*) FROM dbo.products").fetchone()


def test_lector_puede_hacer_joins_analiticos(lectura: Any) -> None:
    lectura.cursor().execute("""
        SELECT TOP 5 p.id, SUM(oi.quantity) AS unidades
        FROM dbo.products p
        LEFT JOIN dbo.order_items oi ON oi.product_id = p.id
        GROUP BY p.id
    """).fetchall()


# --- Lo que NO tiene que poder hacer ----------------------------------------

def test_lector_no_puede_insertar(lectura: Any) -> None:
    with pytest.raises(pyodbc.Error):
        lectura.cursor().execute(
            "INSERT INTO dbo.products (id, brand, category, price, cost, launch_date) "
            "VALUES ('HACK', 'x', 'y', 1, 0.5, '2026-01-01')"
        )


def test_lector_no_puede_actualizar(lectura: Any) -> None:
    with pytest.raises(pyodbc.Error):
        lectura.cursor().execute("UPDATE dbo.products SET price = 0")


def test_lector_no_puede_borrar(lectura: Any) -> None:
    """El caso que todo el mundo teme: un DELETE sin WHERE."""
    with pytest.raises(pyodbc.Error):
        lectura.cursor().execute("DELETE FROM dbo.order_items")


def test_lector_no_puede_dropear_tablas(lectura: Any) -> None:
    with pytest.raises(pyodbc.Error):
        lectura.cursor().execute("DROP TABLE dbo.returns")


def test_lector_no_puede_crear_tablas(lectura: Any) -> None:
    with pytest.raises(pyodbc.Error):
        lectura.cursor().execute("CREATE TABLE dbo.puerta_trasera (x INT)")


def test_lector_no_puede_leer_el_ground_truth(lectura: Any) -> None:
    """El agente NO puede consultar la lista de anomalías sembradas.

    Si pudiera, cualquier evaluación de detección dejaría de medir algo: sería
    un examen a libro abierto donde el libro tiene las respuestas.
    """
    with pytest.raises(pyodbc.Error):
        lectura.cursor().execute("SELECT * FROM dbo.ground_truth")


def test_el_ground_truth_si_es_accesible_para_evaluacion(lectura: Any) -> None:
    """Contraprueba: la tabla existe y el rol admin sí la ve.

    Sin esto, el test anterior pasaría igual si la tabla no existiera —
    y estaríamos celebrando una defensa que en realidad no está probada.
    """
    con = conectar_admin()
    try:
        con.cursor().execute("SELECT COUNT(*) FROM dbo.ground_truth").fetchone()
    finally:
        con.close()
