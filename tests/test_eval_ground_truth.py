"""Tests de cómo se interroga al agente durante la evaluación.

Lo que se protege acá es la validez del examen. Un eval que menciona la anomalía
en el enunciado —"¿por qué subieron las devoluciones de P010 el 14 de febrero?"—
no mide si el agente detecta algo: mide si sabe repetir lo que se le dijo.

Es el mismo cuidado que motivó el `DENY SELECT` sobre `dbo.ground_truth`. De
nada sirve cerrarle la puerta a la tabla si después la respuesta viaja en la
pregunta.
"""

from __future__ import annotations

from datetime import date

from eval.ground_truth import consulta_para
from eval.metricas import EventoSembrado

CAIDA = EventoSembrado(
    tipo="caida_ventas", product_id="P010", fecha=date(2026, 2, 14),
    magnitud=-42.5, descripcion="Quiebre de stock por campaña sin reposición",
)

PICO = EventoSembrado(
    tipo="pico_devoluciones", product_id="P010", fecha=date(2026, 2, 14),
    magnitud=5.7, descripcion="Cambio de lote del proveedor",
)


def test_la_fecha_que_llega_como_texto_se_convierte_a_date():
    """El driver ODBC devuelve las columnas DATE como string.

    Con un `@dataclass` la anotación `fecha: date` no valida nada y el string
    viajaba intacto hasta reventar tres capas más adelante, en un `strftime`.
    El modelo tiene que rechazar o convertir en el borde, que es donde el dato
    entra — el mismo criterio que sostiene a `Report` y a `AnalysisState`.
    """
    evento = EventoSembrado(
        tipo="pico_devoluciones", product_id="P038", fecha="2025-05-04",
        magnitud=0.42, descripcion="lote defectuoso",
    )

    assert evento.fecha == date(2025, 5, 4)
    assert "2025-05" in consulta_para(evento)


def test_la_consulta_nombra_el_producto_y_el_periodo():
    consulta = consulta_para(CAIDA)

    assert "P010" in consulta
    assert "2026-02" in consulta


def test_la_consulta_no_revela_el_tipo_de_evento():
    consulta = consulta_para(CAIDA).lower()

    assert "caida_ventas" not in consulta
    assert "caída" not in consulta


def test_la_consulta_no_revela_la_descripcion_del_evento():
    """La descripción ES la respuesta. Si viaja en la pregunta, el agente
    solamente tiene que devolverla."""
    consulta = consulta_para(PICO).lower()

    assert "lote" not in consulta
    assert "proveedor" not in consulta


def test_la_consulta_no_revela_la_magnitud():
    consulta = consulta_para(PICO)

    assert "5,7" not in consulta
    assert "5.7" not in consulta


def test_dos_eventos_distintos_producen_la_misma_pregunta():
    """La prueba más fuerte de que el enunciado no depende de la respuesta.

    Mismo producto y mismo mes, anomalías opuestas —una caída de ventas y un
    pico de devoluciones—, y el agente recibe exactamente el mismo texto. Lo
    que cambia es lo que tiene que encontrar, no lo que se le cuenta.
    """
    assert consulta_para(CAIDA) == consulta_para(PICO)


def test_la_consulta_pide_explicar_lo_anomalo_sin_afirmar_que_lo_hay():
    """Se invita a mirar, no se avisa que hay algo.

    Si la consigna afirmara que hubo una anomalía, el agente encontraría una
    aunque no existiera — y no se podría medir el falso positivo.
    """
    consulta = consulta_para(PICO).lower()

    assert "si ves algo fuera de lo normal" in consulta
