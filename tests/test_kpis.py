"""Tests de los KPIs por doble contabilidad.

Cada métrica se calcula dos veces, de forma independiente:

  1. Con una consulta T-SQL contra SQL Server.
  2. Con pandas, sobre el mismo dataset generado en memoria.

Si los dos resultados no coinciden, una de las dos implementaciones está mal —
y hay que averiguar cuál. Es el principio de la partida doble aplicado a
métricas: no se confía en un único cálculo, se lo confronta con otro hecho por
un camino distinto.

Por qué tanto trabajo para cinco números: estos KPIs son la materia prima del
informe. Un margen mal calculado no produce un error visible — produce una frase
perfectamente redactada que dice algo falso. El LLM no tiene forma de detectarlo
y el lector tampoco. El test es lo único que se interpone.

Las tres reglas de negocio que los tests fijan (ver ADR-005):
  - Las órdenes canceladas NO cuentan como venta.
  - Las devoluciones NO se restan del revenue: se reportan aparte.
  - El margen se calcula sobre el precio efectivamente pagado, no el de lista.
"""

from datetime import date

import pandas as pd
import pytest

from core.db import hay_base_disponible
from core.kpis import (
    crecimiento_pct,
    margen_pct,
    metricas_de_producto,
    revenue,
    tasa_devolucion_pct,
    unidades_vendidas,
)
from seeds.generate import DatasetConfig, generar_dataset

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not hay_base_disponible(),
        reason="SQL Server no está levantado (.\\tasks.ps1 db-up)",
    ),
]

DESDE = date(2026, 1, 1)
HASTA = date(2026, 3, 31)
TOLERANCIA = 0.01  # un centavo: los DECIMAL de SQL y los float de pandas difieren


@pytest.fixture(scope="module")
def ds() -> dict[str, pd.DataFrame]:
    """El mismo dataset que está cargado en la base (seed por defecto)."""
    return generar_dataset(DatasetConfig())


@pytest.fixture(scope="module")
def ventas(ds) -> pd.DataFrame:
    """Líneas de venta válidas, con su fecha y el costo del producto.

    REGLA: se excluyen las órdenes canceladas. Una cancelación no es una venta,
    y son el 3,5% del dataset: contarlas desviaría todas las métricas.
    """
    items = ds["order_items"].merge(
        ds["orders"][["id", "created_at", "status"]],
        left_on="order_id", right_on="id", suffixes=("", "_ord"),
    )
    items = items[items["status"] != "cancelada"].copy()
    items["fecha"] = items["created_at"].dt.date
    items = items.merge(
        ds["products"][["id", "cost"]], left_on="product_id", right_on="id",
        suffixes=("", "_prod"),
    )
    items["revenue"] = items["quantity"] * items["unit_price"]
    # Margen sobre el precio PAGADO, no el de lista: con campañas activas, usar
    # el precio de lista infla el margen y el informe miente sin que se note.
    items["costo_total"] = items["quantity"] * items["cost"]
    return items


def _en_rango(ventas: pd.DataFrame, pid: str, desde: date, hasta: date) -> pd.DataFrame:
    return ventas[
        (ventas["product_id"] == pid)
        & (ventas["fecha"] >= desde)
        & (ventas["fecha"] <= hasta)
    ]


@pytest.fixture(scope="module")
def productos_de_prueba(ventas) -> list[str]:
    """Los cinco productos con más movimiento en el rango: si algo no cierra,
    conviene que sea sobre volumen alto, donde los errores se ven."""
    en_rango = ventas[(ventas["fecha"] >= DESDE) & (ventas["fecha"] <= HASTA)]
    return (en_rango.groupby("product_id")["quantity"].sum()
            .sort_values(ascending=False).head(5).index.tolist())


# --- KPI 1: unidades vendidas -----------------------------------------------

def test_unidades_coincide_con_el_calculo_independiente(ventas, productos_de_prueba):
    for pid in productos_de_prueba:
        esperado = int(_en_rango(ventas, pid, DESDE, HASTA)["quantity"].sum())
        obtenido = unidades_vendidas(pid, DESDE, HASTA)
        assert obtenido == esperado, (
            f"{pid}: SQL dice {obtenido} unidades, pandas dice {esperado}"
        )


def test_unidades_excluye_las_ordenes_canceladas(ds, productos_de_prueba):
    """Contraprueba: si la query NO filtrara canceladas, daría más alto.

    Sin este test, una query sin el filtro pasaría el test anterior solo si el
    cálculo en pandas tuviera el mismo error — y ahí la doble contabilidad
    dejaría de servir para nada.
    """
    items = ds["order_items"].merge(
        ds["orders"][["id", "created_at", "status"]],
        left_on="order_id", right_on="id", suffixes=("", "_ord"),
    )
    items["fecha"] = items["created_at"].dt.date

    for pid in productos_de_prueba:
        en_rango = items[(items["product_id"] == pid)
                         & (items["fecha"] >= DESDE) & (items["fecha"] <= HASTA)]
        con_canceladas = int(en_rango["quantity"].sum())
        sin_canceladas = int(en_rango[en_rango["status"] != "cancelada"]["quantity"].sum())
        if con_canceladas == sin_canceladas:
            continue  # ese producto no tuvo cancelaciones en el rango
        assert unidades_vendidas(pid, DESDE, HASTA) == sin_canceladas
        break
    else:
        pytest.skip("ningún producto de prueba tuvo cancelaciones en el rango")


# --- KPI 2: revenue ----------------------------------------------------------

def test_revenue_coincide_con_el_calculo_independiente(ventas, productos_de_prueba):
    for pid in productos_de_prueba:
        esperado = float(_en_rango(ventas, pid, DESDE, HASTA)["revenue"].sum())
        obtenido = revenue(pid, DESDE, HASTA)
        assert obtenido == pytest.approx(esperado, abs=TOLERANCIA), (
            f"{pid}: SQL dice {obtenido:,.2f}, pandas dice {esperado:,.2f}"
        )


def test_revenue_usa_el_precio_pagado_no_el_de_lista(ds, ventas):
    """Con campañas de descuento activas, calcular sobre el precio de lista
    infla el revenue.

    Este test no usa los productos de mayor volumen: busca uno que efectivamente
    haya tenido una campaña y mide dentro de la ventana de esa campaña. Un test
    que se saltea porque no encontró el caso no está probando la regla — está
    fingiendo que la probó.
    """
    precios = ds["products"].set_index("id")["price"]

    for _, camp in ds["campaigns"].iterrows():
        pid = camp["product_id"]
        filas = _en_rango(ventas, pid, camp["start_date"], camp["end_date"])
        if filas.empty:
            continue

        real = float(filas["revenue"].sum())
        con_lista = float((filas["quantity"] * precios[pid]).sum())
        if abs(con_lista - real) < TOLERANCIA:
            continue

        obtenido = revenue(pid, camp["start_date"], camp["end_date"])
        assert obtenido == pytest.approx(real, abs=TOLERANCIA), (
            f"{pid}: SQL dice {obtenido:,.2f}, pandas dice {real:,.2f}"
        )
        # Y la contraprueba: usar el precio de lista habría dado otra cosa.
        assert obtenido != pytest.approx(con_lista, abs=TOLERANCIA), (
            f"{pid}: el revenue coincide con el precio de LISTA "
            f"({con_lista:,.2f}); la query está ignorando el descuento"
        )
        return

    pytest.fail(
        "el dataset no contiene ninguna campaña con ventas y descuento efectivo; "
        "sin ese caso, la regla del precio pagado queda sin verificar"
    )


# --- KPI 3: margen -----------------------------------------------------------

def test_margen_coincide_con_el_calculo_independiente(ventas, productos_de_prueba):
    for pid in productos_de_prueba:
        filas = _en_rango(ventas, pid, DESDE, HASTA)
        rev = float(filas["revenue"].sum())
        costo = float(filas["costo_total"].sum())
        if rev == 0:
            continue
        esperado = (rev - costo) / rev * 100
        obtenido = margen_pct(pid, DESDE, HASTA)
        assert obtenido == pytest.approx(esperado, abs=0.05), (
            f"{pid}: SQL dice {obtenido:.2f}%, pandas dice {esperado:.2f}%"
        )


def test_margen_esta_en_rango_plausible(productos_de_prueba):
    """El generador construye los costos con márgenes entre 28% y 62%.

    Un margen fuera de ese rango significa que la query está mezclando algo
    —costos de otro producto, filas duplicadas por un join— y no que el negocio
    ande espectacular.
    """
    for pid in productos_de_prueba:
        m = margen_pct(pid, DESDE, HASTA)
        assert -20 <= m <= 80, f"{pid}: margen de {m:.1f}%, sospechoso"


# --- KPI 4: crecimiento ------------------------------------------------------

def test_crecimiento_coincide_con_el_calculo_independiente(ventas, productos_de_prueba):
    """Compara el rango contra el período inmediatamente anterior de igual largo."""
    dias = (HASTA - DESDE).days
    from datetime import timedelta
    desde_previo = DESDE - timedelta(days=dias + 1)
    hasta_previo = DESDE - timedelta(days=1)

    for pid in productos_de_prueba:
        actual = float(_en_rango(ventas, pid, DESDE, HASTA)["revenue"].sum())
        previo = float(_en_rango(ventas, pid, desde_previo, hasta_previo)["revenue"].sum())
        if previo == 0:
            continue
        esperado = (actual - previo) / previo * 100
        obtenido = crecimiento_pct(pid, DESDE, HASTA)
        assert obtenido == pytest.approx(esperado, abs=0.05), (
            f"{pid}: SQL dice {obtenido:.2f}%, pandas dice {esperado:.2f}%"
        )


def test_crecimiento_sin_periodo_previo_devuelve_none():
    """Un producto sin ventas previas no tiene crecimiento 0%: no tiene
    crecimiento. Devolver 0 sería afirmar algo que no se sabe, y el informe
    lo mostraría como un hecho."""
    resultado = crecimiento_pct("P001", date(2020, 1, 1), date(2020, 3, 31))
    assert resultado is None


# --- KPI 5: tasa de devolución ----------------------------------------------

def test_tasa_devolucion_coincide_con_el_calculo_independiente(
    ds, ventas, productos_de_prueba
):
    devoluciones = set(ds["returns"]["order_item_id"])
    for pid in productos_de_prueba:
        filas = _en_rango(ventas, pid, DESDE, HASTA)
        if filas.empty:
            continue
        devueltas = filas["id"].isin(devoluciones).sum()
        esperado = devueltas / len(filas) * 100
        obtenido = tasa_devolucion_pct(pid, DESDE, HASTA)
        assert obtenido == pytest.approx(esperado, abs=0.05), (
            f"{pid}: SQL dice {obtenido:.2f}%, pandas dice {esperado:.2f}%"
        )


def test_tasa_devolucion_nunca_supera_el_cien_por_ciento(productos_de_prueba):
    for pid in productos_de_prueba:
        t = tasa_devolucion_pct(pid, DESDE, HASTA)
        assert t is None or 0 <= t <= 100, f"{pid}: tasa de devolución de {t}%"


# --- Integración: el KPI completo alimenta el modelo Report ------------------

def test_metricas_de_producto_devuelve_un_modelo_valido(productos_de_prueba):
    """La función que usa la tool del agente devuelve directamente el modelo
    del informe. Sin traducciones intermedias donde se pierdan reglas."""
    pid = productos_de_prueba[0]
    m = metricas_de_producto(pid, DESDE, HASTA)
    assert m.product_id == pid
    assert m.unidades >= 0
    assert m.revenue >= 0
    assert m.fuente.startswith("sql:")


def test_metricas_coinciden_con_los_kpis_individuales(productos_de_prueba):
    """El agregado no puede diferir de sus partes: si difiere, hay dos caminos
    de cálculo y tarde o temprano se contradicen."""
    pid = productos_de_prueba[0]
    m = metricas_de_producto(pid, DESDE, HASTA)
    assert m.unidades == unidades_vendidas(pid, DESDE, HASTA)
    assert m.revenue == pytest.approx(revenue(pid, DESDE, HASTA), abs=TOLERANCIA)
    assert m.margen_pct == pytest.approx(margen_pct(pid, DESDE, HASTA), abs=0.01)
