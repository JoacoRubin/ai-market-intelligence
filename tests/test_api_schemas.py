"""Tests de los contratos de la API que no necesitan base de datos.

`tests/test_api.py` cubre los endpoints, pero está marcado `db`: sin SQL Server
levantado se saltea entero, y con él se van las reglas de validación de la
solicitud, que son lógica pura y no tocan la base.

Que una regla de dominio solo se verifique cuando hay un contenedor corriendo
es una regla que en la práctica se verifica poco. Estos tests la sacan de ahí.
"""

from datetime import date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from apps.api.schemas import (
    Analisis,
    AnalisisResumen,
    EstadoAnalisis,
    ListaAnalisis,
    SolicitudAnalisis,
)


def _estructurada(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "product_ids": ["P001", "P002"],
        "desde": date(2026, 1, 1),
        "hasta": date(2026, 3, 31),
    }
    base.update(kw)
    return base


# --- Formas excluyentes ------------------------------------------------------

def test_acepta_la_forma_estructurada() -> None:
    s = SolicitudAnalisis(**_estructurada())
    assert s.es_estructurada


def test_acepta_la_forma_en_lenguaje_natural() -> None:
    s = SolicitudAnalisis(consulta="Compará P001 y P002")
    assert not s.es_estructurada


def test_rechaza_las_dos_formas_juntas() -> None:
    """Con ambas presentes queda ambiguo cuál define el análisis.

    Se rechaza en vez de elegir una en silencio: que el usuario descubra
    después cuál se usó es peor que un error explícito.
    """
    with pytest.raises(ValidationError, match="no ambos"):
        SolicitudAnalisis(**_estructurada(consulta="Compará P001 y P002"))


def test_rechaza_una_solicitud_vacia() -> None:
    with pytest.raises(ValidationError, match="hace falta"):
        SolicitudAnalisis()


# --- Reglas de la forma estructurada ------------------------------------------

def test_los_ids_requieren_periodo() -> None:
    with pytest.raises(ValidationError, match="requiere 'desde' y 'hasta'"):
        SolicitudAnalisis(product_ids=["P001"])


def test_rechaza_el_rango_invertido() -> None:
    with pytest.raises(ValidationError, match="invertido"):
        SolicitudAnalisis(
            **_estructurada(desde=date(2026, 3, 31), hasta=date(2026, 1, 1))
        )


def test_rechaza_productos_repetidos() -> None:
    """Un id repetido duplicaría la fila en el informe sin agregar información."""
    with pytest.raises(ValidationError, match="repetidos"):
        SolicitudAnalisis(**_estructurada(product_ids=["P001", "P001"]))


def test_una_consulta_de_solo_espacios_no_cuenta_como_consulta() -> None:
    """`"   "` es verdadero como string pero vacío como consulta.

    Sin el `.strip()` del validador, pasaría la solicitud y el agente terminaría
    interpretando la nada.
    """
    with pytest.raises(ValidationError, match="hace falta"):
        SolicitudAnalisis(consulta="     ")


# --- Listado: covarianza ------------------------------------------------------

def test_el_listado_acepta_analisis_completos() -> None:
    """El almacén guarda `Analisis`; el listado los expone como `AnalisisResumen`.

    Es el caso covariante que motivó tipar `items` como `Sequence`: con `list`
    —que es invariante— un `list[Analisis]` no encaja aunque cada elemento sí.
    """
    completo = Analisis(
        id="req-1",
        estado=EstadoAnalisis.COMPLETADO,
        creado_en=datetime(2026, 1, 1, 12, 0),
        consulta="Compará P001 y P002",
        product_ids=["P001"],
    )
    lista = ListaAnalisis(total=1, items=[completo])
    assert lista.total == 1
    assert lista.items[0].id == "req-1"


def test_el_listado_sigue_aceptando_resumenes() -> None:
    resumen = AnalisisResumen(
        id="req-2",
        estado=EstadoAnalisis.PENDIENTE,
        creado_en=datetime(2026, 1, 1, 12, 0),
        consulta="Compará P001 y P002",
    )
    assert ListaAnalisis(total=1, items=[resumen]).items[0].id == "req-2"


def test_el_listado_serializa_solo_los_campos_del_resumen() -> None:
    """El contrato del listado es la vista liviana, aunque adentro haya más.

    Si `informe` se filtrara al listado, cada página traería los informes
    completos: el endpoint dejaría de ser un índice y pasaría a ser una descarga.
    """
    completo = Analisis(
        id="req-3",
        estado=EstadoAnalisis.COMPLETADO,
        creado_en=datetime(2026, 1, 1, 12, 0),
        consulta="Compará P001 y P002",
        error="algo salió mal",
    )
    payload = ListaAnalisis(total=1, items=[completo]).model_dump()
    assert "error" not in payload["items"][0]
    assert "informe" not in payload["items"][0]
