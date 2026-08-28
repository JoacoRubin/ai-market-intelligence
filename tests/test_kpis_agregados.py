"""Contrato unitario de la lectura consistente de KPIs.

No necesita SQL Server: el objetivo es fijar la frontera de acceso a datos. El
agregado que consume el agente debe salir de una sola sentencia y un solo
cursor; los tests de doble contabilidad en ``test_kpis.py`` siguen verificando
los numeros contra el dataset real.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Any

import pytest

from core import kpis


class _CursorEspia:
    def __init__(self, fila: tuple[Any, ...]) -> None:
        self.fila = fila
        self.ejecuciones: list[tuple[str, tuple[str, ...]]] = []

    def execute(self, sql: str, parametros: tuple[str, ...]) -> _CursorEspia:
        self.ejecuciones.append((sql, parametros))
        return self

    def fetchone(self) -> tuple[Any, ...]:
        return self.fila


def test_metricas_usa_una_sola_lectura_agregada(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = _CursorEspia(("Acme", "Audio", 12, 1200.0, 720.0, 10, 2, 1000.0))
    aperturas = 0

    @contextmanager
    def cursor_lectura_espio() -> Any:
        nonlocal aperturas
        aperturas += 1
        yield cursor

    monkeypatch.setattr(kpis, "cursor_lectura", cursor_lectura_espio)

    metrica = kpis.metricas_de_producto(
        "P001", date(2026, 1, 1), date(2026, 1, 31)
    )

    assert aperturas == 1
    assert len(cursor.ejecuciones) == 1
    sql, parametros = cursor.ejecuciones[0]
    assert "SELECT" in sql.upper()
    assert "?" in sql
    assert "P001" not in sql
    assert parametros == (
        "P001",
        "2026-01-01",
        "2026-01-31",
        "2025-12-01",
        "2025-12-31",
    )
    assert metrica.nombre == "Acme Audio"
    assert metrica.unidades == 12
    assert metrica.revenue == 1200.0
    assert metrica.margen_pct == pytest.approx(40.0)
    assert metrica.crecimiento_pct == pytest.approx(20.0)
    assert metrica.tasa_devolucion_pct == pytest.approx(20.0)


def test_metricas_agregadas_preservan_nulls_semanticos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = _CursorEspia((None, None, 0, 0, 0, 0, 0, 0))

    @contextmanager
    def cursor_lectura_falso() -> Any:
        yield cursor

    monkeypatch.setattr(kpis, "cursor_lectura", cursor_lectura_falso)

    metrica = kpis.metricas_de_producto(
        "P404", date(2026, 2, 1), date(2026, 2, 28)
    )

    assert metrica.nombre == "P404"
    assert metrica.unidades == 0
    assert metrica.revenue == 0.0
    assert metrica.margen_pct is None
    assert metrica.crecimiento_pct is None
    assert metrica.tasa_devolucion_pct is None
