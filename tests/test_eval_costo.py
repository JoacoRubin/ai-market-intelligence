"""Tests de la conversión de tokens a dólares.

Los tokens son un hecho que reporta el proveedor. El costo es una
interpretación: depende de una tarifa que cambia sin avisar y que en algunos
modelos tiene precios promocionales con fecha de vencimiento.

Por eso viven separados. `Uso` guarda tokens y este módulo los convierte, con
las tarifas fechadas. Un registro que guardara dólares directamente sería
irreproducible: dentro de seis meses nadie podría saber con qué precio se
calculó ese número.
"""

from __future__ import annotations

import pytest

from agent.llm import Uso
from eval.costo import FECHA_TARIFAS, TARIFAS, Tarifa, costo_usd

USO = Uso(tokens_entrada=35_190, tokens_salida=1_995, tokens_cacheados=0, llamadas=30)


# --- La conversión -----------------------------------------------------------

def test_convierte_tokens_a_dolares_con_la_tarifa_del_modelo() -> None:
    """El golden set son 15 casos x 2 llamadas al modelo.

    El número de referencia salió de medir los prompts reales del agente:
    ~2.346 tokens de entrada y ~133 de salida por consulta.
    """
    # 35.190 x $5/1M + 1.995 x $25/1M
    esperado = 35_190 * 5.00 / 1e6 + 1_995 * 25.00 / 1e6
    assert costo_usd(USO, "claude-opus-5") == pytest.approx(esperado)


def test_un_uso_vacio_cuesta_cero() -> None:
    assert costo_usd(Uso(), "claude-opus-5") == 0.0


def test_los_tokens_leidos_de_cache_cuestan_una_fraccion() -> None:
    """Una lectura de cache cuesta ~10% de un token de entrada normal.

    Cobrarlos a precio pleno sobreestima la factura; no cobrarlos la
    subestima. Las dos versiones dan un número que no es el que llega.
    """
    con_cache = Uso(tokens_entrada=0, tokens_salida=0, tokens_cacheados=1_000_000)
    assert costo_usd(con_cache, "claude-opus-5") == pytest.approx(0.50)


def test_el_modelo_local_cuesta_cero_y_eso_es_un_dato() -> None:
    """`llama3.2:3b` está en la tabla con tarifa cero, y no ausente.

    Es la diferencia entre "sé que es gratis" y "no sé cuánto cuesta". La tabla
    comparativa del portfolio necesita la primera: un guion en la columna de
    costo del modelo local no diría nada, un $0.00 dice todo.
    """
    assert costo_usd(USO, "llama3.2:3b") == 0.0


def test_un_modelo_sin_tarifa_devuelve_none_y_no_cero() -> None:
    """`None` y no `0.0`. Un modelo que no sabemos cuánto cuesta no es gratis.

    Es el mismo criterio que ya usa `registro.py` con las métricas que no
    aplican: una métrica que no juzgó nada no reprobó. Devolver `0.0` acá
    metería un modelo pago en la tabla como si no costara, que es exactamente
    la conclusión más cara que podría sacar alguien leyendo el registro.
    """
    assert costo_usd(USO, "gpt-5.6-terra") is None


def test_un_modelo_desconocido_no_explota() -> None:
    """El eval no puede caerse por no saber una tarifa.

    Una corrida que midió bien la calidad y no supo el precio sigue siendo una
    corrida válida. Perderla entera por eso sería peor que registrarla sin el
    costo.
    """
    assert costo_usd(USO, "modelo-que-no-existe") is None


# --- La tabla ----------------------------------------------------------------

def test_las_tarifas_estan_fechadas() -> None:
    """Sin fecha, la tabla es una afirmación sin contexto.

    Los precios de las APIs bajan (y suben) varias veces al año. Un número sin
    la fecha en que era cierto no se puede auditar ni actualizar con criterio.
    """
    assert FECHA_TARIFAS


def test_estan_los_tres_escalones_de_claude() -> None:
    """La comparación necesita techo, medio y piso para que signifique algo.

    Medir contra un solo modelo pago contesta "¿cuánto sale?". Medir contra los
    tres contesta "¿cuánta calidad se compra con cada dólar?", que es la
    pregunta de negocio.
    """
    for modelo in ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"):
        assert modelo in TARIFAS, f"falta la tarifa de {modelo}"


def test_ninguna_tarifa_tiene_precios_negativos() -> None:
    for modelo, t in TARIFAS.items():
        assert t.usd_por_millon_entrada >= 0, modelo
        assert t.usd_por_millon_salida >= 0, modelo


def test_la_salida_nunca_es_mas_barata_que_la_entrada() -> None:
    """Vale para todo proveedor: generar cuesta más que leer.

    Si una tarifa cargada violara esto, es un error de tipeo —columnas
    invertidas— y no un precio real. Es el chequeo que atrapa el error más
    probable al actualizar la tabla a mano.
    """
    for modelo, t in TARIFAS.items():
        assert t.usd_por_millon_salida >= t.usd_por_millon_entrada, modelo


def test_la_tarifa_del_modelo_local_es_cero_en_las_dos_puntas() -> None:
    local = TARIFAS["llama3.2:3b"]
    assert local == Tarifa(0.0, 0.0)
