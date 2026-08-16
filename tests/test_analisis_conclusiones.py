"""Tests del generador de conclusiones determinísticas.

Estas frases van directo al informe que lee una persona. No las escribe un LLM:
las arma código, comparando números. Eso las hace verificables — y obliga a
testear tanto el contenido como la forma.

El primer test existe por un bug real: el formateo de miles se aplicaba con un
`replace(",", ".")` sobre la frase completa, y se comía las comas gramaticales.
Producía "lidera con 242. frente a 0", que en un informe ejecutivo queda como
un error de alguien que no revisó lo que entregaba.
"""

from datetime import datetime
from typing import Any

import pytest

from core.conclusiones import _alertas_de_devolucion, _conclusiones
from core.kpis import FUENTE
from core.report import MetricaProducto


def _m(pid: str, nombre: str, **kw: Any) -> MetricaProducto:
    base: dict[str, Any] = dict(
        product_id=pid, nombre=nombre, unidades=100, revenue=10000.0,
        margen_pct=30.0, crecimiento_pct=5.0, tasa_devolucion_pct=3.0,
        fuente=FUENTE,
    )
    base.update(kw)
    return MetricaProducto(**base)


# --- Forma -------------------------------------------------------------------

def test_no_se_rompen_las_comas_gramaticales() -> None:
    """El separador de miles no puede pisar la puntuación de la frase."""
    conclusiones = _conclusiones([
        _m("P001", "Alfa", unidades=1500),
        _m("P002", "Beta", unidades=900),
    ])
    texto = " ".join(c.texto for c in conclusiones)
    assert ". frente a" not in texto, f"coma gramatical corrompida: {texto}"
    assert ". respecto" not in texto


def test_los_miles_se_separan_con_punto() -> None:
    conclusiones = _conclusiones([
        _m("P001", "Alfa", unidades=1500),
        _m("P002", "Beta", unidades=900),
    ])
    texto = " ".join(c.texto for c in conclusiones)
    assert "1.500" in texto, f"esperaba '1.500' con separador de miles: {texto}"


def test_los_decimales_usan_coma() -> None:
    conclusiones = _conclusiones([
        _m("P001", "Alfa", margen_pct=31.2),
        _m("P002", "Beta", margen_pct=24.8),
    ])
    texto = " ".join(c.texto for c in conclusiones)
    assert "31,2%" in texto, f"esperaba '31,2%' con coma decimal: {texto}"


# --- Contenido ---------------------------------------------------------------

def test_toda_conclusion_es_un_hecho_con_fuente() -> None:
    """Las conclusiones derivan de números consultados: son hechos, y cada uno
    tiene que poder rastrearse hasta la consulta que lo produjo."""
    for c in _conclusiones([_m("P001", "Alfa"), _m("P002", "Beta")]):
        assert c.tipo == "hecho"
        assert c.fuentes == [FUENTE]


def test_identifica_al_lider_en_unidades() -> None:
    conclusiones = _conclusiones([
        _m("P001", "Alfa", unidades=100),
        _m("P002", "Beta", unidades=500),
    ])
    texto = " ".join(c.texto for c in conclusiones)
    assert "Beta" in texto and "lidera en unidades" in texto


def test_senala_cuando_el_lider_en_volumen_no_lidera_en_revenue() -> None:
    """Es la observación comercial más valiosa que se puede derivar sin ML:
    vender más unidades no es vender mejor."""
    conclusiones = _conclusiones([
        _m("P001", "Alfa", unidades=1000, revenue=5000.0),
        _m("P002", "Beta", unidades=200, revenue=40000.0),
    ])
    texto = " ".join(c.texto for c in conclusiones)
    assert "no es el líder en facturación" in texto


def test_reporta_las_caidas() -> None:
    conclusiones = _conclusiones([
        _m("P001", "Alfa", crecimiento_pct=12.0),
        _m("P002", "Beta", crecimiento_pct=-8.5),
    ])
    texto = " ".join(c.texto for c in conclusiones)
    assert "Beta" in texto and "cae" in texto and "8,5%" in texto


def test_un_solo_producto_no_produce_comparaciones() -> None:
    conclusiones = _conclusiones([_m("P001", "Alfa", unidades=1234)])
    assert len(conclusiones) == 1
    assert "1.234" in conclusiones[0].texto
    assert "lidera" not in conclusiones[0].texto


def test_ignora_las_metricas_sin_dato() -> None:
    """Un producto sin margen calculable no puede ganar la comparación de
    márgenes ni hacer explotar el generador."""
    conclusiones = _conclusiones([
        _m("P001", "Alfa", margen_pct=None, crecimiento_pct=None),
        _m("P002", "Beta", margen_pct=22.0),
    ])
    texto = " ".join(c.texto for c in conclusiones)
    assert "None" not in texto
    assert "Beta" in texto


# --- Alertas -----------------------------------------------------------------

def test_alerta_cuando_un_producto_devuelve_mucho_mas_que_el_resto() -> None:
    alertas = _alertas_de_devolucion([
        _m("P001", "Alfa", tasa_devolucion_pct=2.0),
        _m("P002", "Beta", tasa_devolucion_pct=2.5),
        _m("P003", "Gama", tasa_devolucion_pct=18.0),
    ])
    assert len(alertas) == 1
    assert "Gama" in alertas[0]


def test_sin_alerta_cuando_las_tasas_son_parejas() -> None:
    alertas = _alertas_de_devolucion([
        _m("P001", "Alfa", tasa_devolucion_pct=3.0),
        _m("P002", "Beta", tasa_devolucion_pct=3.4),
    ])
    assert alertas == []


def test_sin_alerta_con_un_solo_producto() -> None:
    """Con un único producto no hay grupo contra el cual comparar. Alertar ahí
    sería inventar una anomalía donde solo hay falta de contexto."""
    assert _alertas_de_devolucion([_m("P001", "Alfa", tasa_devolucion_pct=40.0)]) == []


@pytest.mark.parametrize("tasas", [[0.0, 0.0], [None, None], [None, 5.0]])
def test_alertas_no_explotan_con_datos_incompletos(tasas: list[Any]) -> None:
    metricas = [_m(f"P{i:03d}", f"N{i}", tasa_devolucion_pct=t)
                for i, t in enumerate(tasas, 1)]
    assert isinstance(_alertas_de_devolucion(metricas), list)


def test_las_conclusiones_entran_en_un_informe_valido() -> None:
    """Contraprueba de integración: las afirmaciones generadas tienen que poder
    construir un Report sin violar sus invariantes de trazabilidad."""
    from core.report import Fuente, Report

    metricas = [_m("P001", "Alfa", unidades=1500), _m("P002", "Beta", unidades=900)]
    informe = Report(
        request_id="req-test", consulta="x", generado_en=datetime.now(),
        modelo_llm="sin-llm:test",
        fuentes=[Fuente(id=FUENTE, tipo="sql", referencia="dbo.order_items",
                        consultada_en=datetime.now())],
        resumen_ejecutivo=_conclusiones(metricas),
        metricas=metricas,
    )
    assert len(informe.resumen_ejecutivo) > 0
