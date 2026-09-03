"""Tests de la selección de casos que se publican en el replay.

Elegir QUÉ ejecuciones se muestran es una decisión de producto, no de
infraestructura, y por eso vive en su propio módulo y tiene sus propios tests.

La regla que se defiende acá: la selección tiene que cubrir las cuatro
intenciones del agente, incluida `fuera_de_alcance`. Un demo que solo muestra
caminos felices esconde justamente la capacidad más difícil de construir — que
el agente sepa decir "esto no me corresponde".
"""

from __future__ import annotations

import pytest

from agent.state import Intencion
from replay.casos import (
    EXCLUIDAS,
    SELECCION,
    TOOLS_IMPLEMENTADAS,
    CasoGolden,
    cargar_golden_set,
    casos_para_replay,
)


def test_carga_el_golden_set_con_sus_campos() -> None:
    casos = cargar_golden_set()

    assert len(casos) > 10
    primero = casos[0]
    assert isinstance(primero, CasoGolden)
    assert primero.id == "cmp-01"
    assert primero.consulta.startswith("Compará P001")
    assert primero.intencion == Intencion.PRODUCT_PERFORMANCE


def test_la_seleccion_cubre_toda_intencion_que_el_agente_sabe_servir() -> None:
    """Incluida `fuera_de_alcance`, que es la que nadie muestra.

    Si alguien recorta la selección y deja solo comparaciones, este test falla y
    obliga a tomar la decisión a conciencia en vez de por descuido.
    """
    intenciones = {c.intencion for c in casos_para_replay()}

    assert intenciones == set(Intencion) - EXCLUIDAS


def test_ya_no_hay_intenciones_excluidas() -> None:
    """La exclusión tiene que seguir al código, no a una lista escrita a mano.

    Hasta ADR-014 esto exigía `COMPANY_RESEARCH in EXCLUIDAS`: el planner la
    rechazaba porque no había tool que ejecutar. Ahora `research_company.py`
    existe y el planner arma un paso `RESEARCH_COMPANY` en cuanto el router
    identifica una empresa (`agent/nodes/planner.py`) — la exclusión ya no
    corresponde. Se deja `EXCLUIDAS` vacío en vez de borrado: si el día de
    mañana se retira una tool y una intención vuelve a quedar sin camino, hay
    un lugar único donde declararlo, y este test es el que lo va a notar.
    """
    assert not EXCLUIDAS
    assert "research_company" in [t.stem for t in TOOLS_IMPLEMENTADAS]


def test_la_seleccion_respeta_el_orden_declarado() -> None:
    """El orden es narrativo: la primera captura es la que se ve primero."""
    assert [c.id for c in casos_para_replay()] == list(SELECCION)


def test_falla_ruidosamente_si_un_caso_seleccionado_no_existe() -> None:
    """Un id mal escrito tiene que reventar acá, no producir un replay incompleto."""
    with pytest.raises(ValueError, match="no-existe"):
        casos_para_replay(seleccion=("cmp-01", "no-existe"))


def test_los_casos_seleccionados_traen_su_nota_cuando_la_tienen() -> None:
    """La nota del golden set explica POR QUÉ ese caso es interesante.

    Es el texto que acompaña a la ejecución en el sitio: sin él, el visitante ve
    una consulta cualquiera en vez de "el caso exacto que el spike falló".
    """
    por_id = {c.id: c for c in cargar_golden_set()}

    assert por_id["cmp-01"].nota == "el caso exacto que el spike falló"
    assert por_id["perf-02"].nota is None
