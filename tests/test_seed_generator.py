"""Tests del generador de dataset sintético.

Por qué estos tests existen antes que el generador:

El dataset sintético es la base sobre la que se calculan TODOS los KPIs del
sistema. Si el generador produce datos incoherentes, cada métrica que salga de
ahí va a estar mal — y el agente va a redactar esos errores con total elegancia
y confianza. El LLM no tiene forma de saber que el revenue no cierra.

Además, el dataset tiene un requisito poco habitual: **debe traer anomalías
conocidas sembradas a propósito**. No alcanza con generar datos plausibles; hay
que saber exactamente qué eventos contiene para después poder evaluar si el
sistema los detecta. El dataset es a la vez insumo y ground truth.

Estos tests verifican PROPIEDADES, no valores concretos. Un dataset generado al
azar no se testea comparando contra un fixture: se testea comprobando que las
invariantes se cumplen sea cual sea la corrida.
"""

from datetime import date

import pandas as pd
import pytest

from seeds.generate import DatasetConfig, generar_dataset


@pytest.fixture(scope="module")
def config() -> DatasetConfig:
    return DatasetConfig(
        seed=42,
        fecha_inicio=date(2025, 1, 1),
        fecha_fin=date(2026, 6, 30),
        n_productos=40,
        n_clientes=500,
    )


@pytest.fixture(scope="module")
def ds(config: DatasetConfig) -> dict[str, pd.DataFrame]:
    return generar_dataset(config)


# --- Reproducibilidad --------------------------------------------------------

def test_mismo_seed_produce_dataset_identico(config: DatasetConfig) -> None:
    """Sin esto no hay ciencia posible: si el dataset cambia entre corridas,
    ninguna métrica es comparable y ningún backtest es reproducible."""
    a = generar_dataset(config)
    b = generar_dataset(config)

    for tabla in ("products", "orders", "order_items", "inventory",
                  "returns", "campaigns", "customers"):
        assert a[tabla].equals(b[tabla]), f"la tabla {tabla} no es reproducible"


def test_seed_distinto_produce_dataset_distinto(config: DatasetConfig) -> None:
    otro = generar_dataset(DatasetConfig(**{**config.__dict__, "seed": 43}))
    ds_base = generar_dataset(config)
    assert not otro["orders"].equals(ds_base["orders"])


# --- Integridad referencial --------------------------------------------------

def test_order_items_referencian_productos_existentes(ds: dict[str, pd.DataFrame]) -> None:
    huerfanos = set(ds["order_items"]["product_id"]) - set(ds["products"]["id"])
    assert not huerfanos, f"order_items apunta a productos inexistentes: {huerfanos}"


def test_order_items_referencian_ordenes_existentes(ds: dict[str, pd.DataFrame]) -> None:
    huerfanos = set(ds["order_items"]["order_id"]) - set(ds["orders"]["id"])
    assert not huerfanos, f"order_items apunta a órdenes inexistentes: {huerfanos}"


def test_orders_referencian_clientes_existentes(ds: dict[str, pd.DataFrame]) -> None:
    huerfanos = set(ds["orders"]["customer_id"]) - set(ds["customers"]["id"])
    assert not huerfanos, f"orders apunta a clientes inexistentes: {huerfanos}"


def test_returns_referencian_order_items_existentes(ds: dict[str, pd.DataFrame]) -> None:
    huerfanos = set(ds["returns"]["order_item_id"]) - set(ds["order_items"]["id"])
    assert not huerfanos, f"returns apunta a items inexistentes: {huerfanos}"


# --- Coherencia económica ----------------------------------------------------

def test_precio_mayor_que_costo(ds: dict[str, pd.DataFrame]) -> None:
    """Un catálogo donde el costo supera al precio da márgenes negativos y
    convierte cualquier análisis de rentabilidad en un sinsentido."""
    perdida = ds["products"][ds["products"]["price"] <= ds["products"]["cost"]]
    assert perdida.empty, f"{len(perdida)} productos con precio <= costo"


def test_cantidades_y_precios_positivos(ds: dict[str, pd.DataFrame]) -> None:
    assert (ds["order_items"]["quantity"] > 0).all()
    assert (ds["order_items"]["unit_price"] > 0).all()


# --- Coherencia temporal -----------------------------------------------------

def test_fechas_dentro_del_rango_configurado(
    ds: dict[str, pd.DataFrame],
    config: DatasetConfig,
) -> None:
    fechas = ds["orders"]["created_at"].dt.date
    assert fechas.min() >= config.fecha_inicio
    assert fechas.max() <= config.fecha_fin


def test_devoluciones_posteriores_a_su_orden(ds: dict[str, pd.DataFrame]) -> None:
    """Una devolución anterior a la compra rompe cualquier cálculo de tasa de
    devolución por ventana temporal."""
    items = ds["order_items"].merge(
        ds["orders"][["id", "created_at"]],
        left_on="order_id", right_on="id", suffixes=("", "_orden"),
    )[["id", "created_at"]]

    r = ds["returns"].merge(items, left_on="order_item_id", right_on="id",
                            suffixes=("_ret", "_orden"))
    invalidas = r[r["created_at_ret"] < r["created_at_orden"]]
    assert invalidas.empty, f"{len(invalidas)} devoluciones anteriores a su compra"


# --- Ground truth: eventos sembrados ----------------------------------------

def test_expone_ground_truth_de_anomalias(ds: dict[str, pd.DataFrame]) -> None:
    """El dataset debe declarar qué anomalías contiene. Sin esto no se puede
    evaluar la detección: no habría contra qué comparar."""
    assert "ground_truth" in ds, "el dataset no expone su ground truth"
    gt = ds["ground_truth"]
    assert len(gt) > 0, "no se sembró ninguna anomalía"

    for col in ("tipo", "product_id", "fecha", "descripcion"):
        assert col in gt.columns, f"al ground truth le falta la columna {col}"


def test_anomalias_sembradas_son_estadisticamente_detectables(ds: dict[str, pd.DataFrame]) -> None:
    """No alcanza con anotar que hay una anomalía: tiene que notarse en los datos.

    Una anomalía que no se despega del ruido normal no sirve para evaluar nada,
    porque un detector que la encuentre estaría acertando de casualidad.
    Se exige que el día anómalo supere en 2.5 desvíos la media móvil del producto.
    """
    items = ds["order_items"].merge(
        ds["orders"][["id", "created_at"]], left_on="order_id", right_on="id"
    )
    items["fecha"] = items["created_at"].dt.date

    picos = ds["ground_truth"][ds["ground_truth"]["tipo"] == "pico_ventas"]
    assert len(picos) > 0, "no se sembró ningún pico de ventas"

    no_detectables = []
    for _, a in picos.iterrows():
        serie = (items[items["product_id"] == a["product_id"]]
                 .groupby("fecha")["quantity"].sum().sort_index())
        if len(serie) < 30:
            continue
        media, desvio = serie.mean(), serie.std()
        valor = serie.get(a["fecha"])
        if valor is None or desvio == 0:
            no_detectables.append((a["product_id"], a["fecha"], "sin datos"))
            continue
        z = (valor - media) / desvio
        if z < 2.5:
            no_detectables.append((a["product_id"], a["fecha"], round(z, 2)))

    assert not no_detectables, f"anomalías indistinguibles del ruido: {no_detectables}"


def test_hay_estacionalidad_semanal(ds: dict[str, pd.DataFrame]) -> None:
    """El blueprint pide estacionalidad. Sin ella, cualquier forecast se vuelve
    trivial y el baseline naïve sería imbatible: no habría nada que aprender."""
    items = ds["order_items"].merge(
        ds["orders"][["id", "created_at"]], left_on="order_id", right_on="id"
    )
    por_dia_semana = items.groupby(
        items["created_at"].dt.dayofweek
    )["quantity"].sum()

    variacion = (por_dia_semana.max() - por_dia_semana.min()) / por_dia_semana.mean()
    assert variacion > 0.15, (
        f"variación semanal de solo {variacion:.1%}; el dataset no tiene "
        "estacionalidad suficiente para que el forecast sea interesante"
    )


# --- Realismo comercial ------------------------------------------------------

def test_unidades_por_linea_son_realistas(ds: dict[str, pd.DataFrame]) -> None:
    """En retail, una línea de pedido lleva una o dos unidades del producto.

    Si toda la demanda diaria de un producto se concentra en una sola línea, el
    dataset deja de parecer e-commerce y pasa a parecer venta mayorista. No es
    un detalle cosmético: `ticket promedio` y `unidades por orden` son KPIs del
    sistema, y sobre datos irreales devuelven números irreales.
    """
    q = ds["order_items"]["quantity"]
    assert q.median() <= 2, f"mediana de {q.median()} unidades por línea"
    assert q.quantile(0.95) <= 6, (
        f"el percentil 95 es {q.quantile(0.95)} unidades por línea"
    )


def test_ticket_promedio_en_rango_plausible(ds: dict[str, pd.DataFrame]) -> None:
    items = ds["order_items"].copy()
    items["revenue"] = items["quantity"] * items["unit_price"]
    ticket = items.groupby("order_id")["revenue"].sum().mean()
    assert 40 <= ticket <= 700, f"ticket promedio de USD {ticket:,.2f}"


def test_una_orden_no_repite_el_mismo_producto(ds: dict[str, pd.DataFrame]) -> None:
    """Dos líneas del mismo producto en la misma orden inflarían artificialmente
    el conteo de líneas y romperían cualquier análisis de cesta."""
    dup = ds["order_items"].duplicated(subset=["order_id", "product_id"]).sum()
    assert dup == 0, f"{dup} líneas duplican producto dentro de la misma orden"


# --- Volumen mínimo ----------------------------------------------------------

def test_volumen_suficiente_para_analisis(
    ds: dict[str, pd.DataFrame],
    config: DatasetConfig,
) -> None:
    assert len(ds["products"]) == config.n_productos
    assert len(ds["customers"]) == config.n_clientes
    assert len(ds["orders"]) > 1000, "muy pocas órdenes para análisis temporal"
    assert len(ds["returns"]) > 0, "sin devoluciones no se puede calcular la tasa"
