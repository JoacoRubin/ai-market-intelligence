"""Generador del dataset sintético de e-commerce.

El dataset cumple dos funciones a la vez:

1. **Insumo**: alimenta SQL Server y es la base de todos los KPIs del sistema.
2. **Ground truth**: declara explícitamente qué eventos anómalos contiene, para
   poder evaluar después si el sistema los detecta.

La segunda función es la que justifica generar datos en vez de bajar un dataset
público. Con datos reales sabés lo que pasó solo si alguien lo anotó; acá lo
sabés porque lo sembraste vos.

Toda la aleatoriedad pasa por un único `Generator` de NumPy inicializado con el
seed de la config. Nada de `random` global ni de `datetime.now()`: si el dataset
cambiara entre corridas, ningún backtest sería reproducible y ninguna métrica
comparable entre versiones del modelo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

# --- Catálogo ---------------------------------------------------------------

MARCAS = ["Norte", "Aurora", "Vertex", "Calma", "Pampa", "Lumen", "Sable", "Ribera"]
CATEGORIAS = ["calzado", "indumentaria", "accesorios", "deportes", "hogar"]
CANALES = ["web", "app", "marketplace", "retail"]
SEGMENTOS = ["nuevo", "recurrente", "vip", "inactivo"]
REGIONES = ["AMBA", "Centro", "Cuyo", "NOA", "NEA", "Patagonia"]
MOTIVOS_DEVOLUCION = [
    "talle incorrecto", "producto dañado", "no coincide con la descripción",
    "arrepentimiento", "demora en la entrega",
]

# Factor multiplicativo de demanda por día de la semana (lunes=0 .. domingo=6).
# Genera la estacionalidad semanal que hace no trivial al forecast: sin esto,
# el baseline naïve sería imbatible y no habría nada que aprender.
FACTOR_DIA_SEMANA = np.array([0.82, 0.88, 0.95, 1.05, 1.28, 1.35, 0.92])

# Unidades que lleva cada línea de pedido. Una sola fuente de verdad: la usan
# tanto la generación de order_items como el descuento de stock del inventario.
UNIDADES_POR_LINEA = np.array([1, 2, 3])
PROB_UNIDADES_POR_LINEA = np.array([0.68, 0.24, 0.08])
UNIDADES_POR_PEDIDO = float(UNIDADES_POR_LINEA @ PROB_UNIDADES_POR_LINEA)


@dataclass
class DatasetConfig:
    """Parámetros de generación. El seed es lo que hace todo reproducible."""

    seed: int = 42
    fecha_inicio: date = date(2025, 1, 1)
    fecha_fin: date = date(2026, 6, 30)
    n_productos: int = 40
    n_clientes: int = 500
    tasa_devolucion_base: float = 0.035
    n_picos_ventas: int = 6
    n_caidas_ventas: int = 3
    n_picos_devoluciones: int = 4
    factor_pico: tuple[float, float] = (8.0, 13.0)
    eventos: list = field(default_factory=list)


def _rango_fechas(cfg: DatasetConfig) -> pd.DatetimeIndex:
    return pd.date_range(cfg.fecha_inicio, cfg.fecha_fin, freq="D")


def _generar_productos(cfg: DatasetConfig, rng: np.random.Generator) -> pd.DataFrame:
    n = cfg.n_productos
    precios = np.round(rng.uniform(15, 320, n), 2)
    # El costo se deriva del precio para garantizar margen positivo siempre.
    # Un catálogo con costo > precio vuelve absurdo cualquier análisis de
    # rentabilidad, y es un error que después nadie encuentra.
    margen = rng.uniform(0.28, 0.62, n)
    costos = np.round(precios * (1 - margen), 2)

    dias_totales = (cfg.fecha_fin - cfg.fecha_inicio).days
    offsets = rng.integers(-540, max(dias_totales - 30, 1), n)

    return pd.DataFrame({
        "id": [f"P{i:03d}" for i in range(1, n + 1)],
        "brand": rng.choice(MARCAS, n),
        "category": rng.choice(CATEGORIAS, n),
        "price": precios,
        "cost": costos,
        "launch_date": [cfg.fecha_inicio + timedelta(days=int(o)) for o in offsets],
    })


def _generar_clientes(cfg: DatasetConfig, rng: np.random.Generator) -> pd.DataFrame:
    n = cfg.n_clientes
    dias = (cfg.fecha_fin - cfg.fecha_inicio).days
    offsets = rng.integers(-365, max(dias, 1), n)
    return pd.DataFrame({
        "id": [f"C{i:04d}" for i in range(1, n + 1)],
        "segment": rng.choice(SEGMENTOS, n, p=[0.30, 0.42, 0.13, 0.15]),
        "region": rng.choice(REGIONES, n, p=[0.38, 0.19, 0.11, 0.13, 0.10, 0.09]),
        "created_at": [
            pd.Timestamp(cfg.fecha_inicio + timedelta(days=int(o)))
            for o in offsets
        ],
    })


def _matriz_demanda(
    cfg: DatasetConfig,
    rng: np.random.Generator,
    productos: pd.DataFrame,
    fechas: pd.DatetimeIndex,
) -> tuple[np.ndarray, pd.DataFrame, pd.DataFrame]:
    """Construye la matriz (días x productos) de pedidos.

    Capas que se multiplican, de más estructural a más ruidosa:
      demanda base del producto
        x estacionalidad semanal
        x estacionalidad anual
        x campañas activas
        x eventos sembrados (picos y caídas)
        -> Poisson

    Devuelve además el ground truth y las campañas, porque ambos se definen acá:
    son las causas de los patrones que después hay que detectar.
    """
    n_dias, n_prod = len(fechas), len(productos)

    # Demanda expresada en PEDIDOS por día, no en unidades. Cada pedido lleva
    # después 1 a 3 unidades. Modelar unidades directamente concentraba toda la
    # demanda diaria de un producto en una sola línea de orden, lo que producía
    # tickets de venta mayorista en un dataset que debe parecer retail.
    demanda_base = rng.gamma(shape=1.6, scale=1.0, size=n_prod) + 0.2

    dow = FACTOR_DIA_SEMANA[fechas.dayofweek.to_numpy()]
    dia_del_anio = fechas.dayofyear.to_numpy()
    anual = 1 + 0.22 * np.sin(2 * np.pi * (dia_del_anio - 80) / 365.25)

    intensidad = (demanda_base[None, :] * dow[:, None] * anual[:, None])

    # Producto no vendible antes de su lanzamiento.
    lanzamientos = pd.to_datetime(productos["launch_date"]).to_numpy()
    disponible = fechas.to_numpy()[:, None] >= lanzamientos[None, :]
    intensidad = np.where(disponible, intensidad, 0.0)

    # --- campañas ---
    filas_camp = []
    n_campanias = max(cfg.n_productos // 3, 4)
    idx_camp = rng.choice(n_prod, n_campanias, replace=False)
    for j in idx_camp:
        inicio = int(rng.integers(0, max(n_dias - 30, 1)))
        largo = int(rng.integers(5, 21))
        fin = min(inicio + largo, n_dias - 1)
        descuento = float(np.round(rng.choice([0.10, 0.15, 0.20, 0.25, 0.30]), 2))
        # Un descuento mayor levanta más la demanda: relación conocida y sembrada.
        intensidad[inicio:fin + 1, j] *= 1 + descuento * 4.5
        filas_camp.append({
            "product_id": productos.iloc[j]["id"],
            "start_date": fechas[inicio].date(),
            "end_date": fechas[fin].date(),
            "spend": float(np.round(rng.uniform(500, 9000), 2)),
            "discount": descuento,
        })
    campanias = pd.DataFrame(filas_camp)

    # --- eventos sembrados (ground truth) ---
    gt = []
    ventana_valida = n_dias - 45  # lejos de los bordes: deja contexto a ambos lados

    def _elegir_dia_con_demanda(rng, intensidad, j, lo, hi, minimo=0.6):
        """Devuelve un día del rango donde el producto TIENE demanda, o None.

        Sembrar un evento en un día sin demanda (producto aún no lanzado, o
        intensidad nula) no produce ninguna señal observable: quedaría anotado
        en el ground truth un evento que los datos no contienen. Un ground truth
        que miente es peor que no tenerlo, porque hace perseguir fantasmas con
        métricas que parecen legítimas.
        """
        validos = np.nonzero(intensidad[lo:hi, j] > minimo)[0]
        if len(validos) == 0:
            return None
        return int(validos[int(rng.integers(0, len(validos)))]) + lo

    def _producto_con_demanda(rng, intensidad, lo, hi, intentos=60):
        for _ in range(intentos):
            j = int(rng.integers(0, n_prod))
            i = _elegir_dia_con_demanda(rng, intensidad, j, lo, hi)
            if i is not None:
                return j, i
        return None, None

    for _ in range(cfg.n_picos_ventas):
        j, i = _producto_con_demanda(rng, intensidad, 30, ventana_valida)
        if j is None:
            continue
        factor = float(rng.uniform(*cfg.factor_pico))
        intensidad[i, j] *= factor
        gt.append({
            "tipo": "pico_ventas",
            "product_id": productos.iloc[j]["id"],
            "fecha": fechas[i].date(),
            "magnitud": round(factor, 2),
            "descripcion": f"pico de demanda x{factor:.1f} sembrado",
        })

    for _ in range(cfg.n_caidas_ventas):
        j, i = _producto_con_demanda(rng, intensidad, 30, ventana_valida)
        if j is None:
            continue
        largo = int(rng.integers(3, 8))
        intensidad[i:i + largo, j] *= 0.12
        gt.append({
            "tipo": "caida_ventas",
            "product_id": productos.iloc[j]["id"],
            "fecha": fechas[i].date(),
            "magnitud": largo,
            "descripcion": f"caída sostenida de {largo} días (rotura de stock)",
        })

    pedidos = rng.poisson(intensidad)
    return pedidos, pd.DataFrame(gt), campanias


def _generar_ordenes(
    cfg: DatasetConfig,
    rng: np.random.Generator,
    pedidos: np.ndarray,
    productos: pd.DataFrame,
    clientes: pd.DataFrame,
    fechas: pd.DatetimeIndex,
    campanias: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convierte la matriz de pedidos en órdenes y líneas de detalle."""
    precios = productos["price"].to_numpy()
    ids_producto = productos["id"].to_numpy()
    ids_cliente = clientes["id"].to_numpy()

    # Descuento vigente por (día, producto), para que unit_price refleje campañas.
    descuento = np.zeros_like(pedidos, dtype=float)
    if not campanias.empty:
        pos = {pid: k for k, pid in enumerate(ids_producto)}
        fecha_a_idx = {f.date(): k for k, f in enumerate(fechas)}
        for _, c in campanias.iterrows():
            j = pos[c["product_id"]]
            i0, i1 = fecha_a_idx[c["start_date"]], fecha_a_idx[c["end_date"]]
            descuento[i0:i1 + 1, j] = c["discount"]

    ordenes, items = [], []
    n_orden = n_item = 0

    # Cada celda de la matriz son PEDIDOS de ese producto ese día. Se expanden a
    # una línea por pedido, y las líneas se agrupan en órdenes de 1 a 3 productos
    # distintos — que es como se ve una cesta de e-commerce real.
    dias_idx, prods_idx = np.nonzero(pedidos)
    por_dia: dict[int, list[int]] = {}
    for i, j in zip(dias_idx, prods_idx, strict=True):
        por_dia.setdefault(int(i), []).extend([int(j)] * int(pedidos[i, j]))

    for i in sorted(por_dia):
        lineas = por_dia[i]
        orden_de_visita = rng.permutation(len(lineas))

        pendientes = [lineas[k] for k in orden_de_visita]
        actual: list[int] = []
        objetivo = int(rng.integers(1, 4))

        def _emitir(lote: list[int], i=i) -> None:
            nonlocal n_orden, n_item
            if not lote:
                return
            n_orden += 1
            id_orden = f"O{n_orden:06d}"
            hora = int(rng.integers(8, 23))
            minuto = int(rng.integers(0, 60))
            ordenes.append({
                "id": id_orden,
                "customer_id": rng.choice(ids_cliente),
                "created_at": fechas[i] + timedelta(hours=hora, minutes=minuto),
                "channel": rng.choice(CANALES, p=[0.41, 0.33, 0.18, 0.08]),
                "status": rng.choice(["completada", "cancelada"], p=[0.965, 0.035]),
            })
            for j in lote:
                n_item += 1
                # 1 unidad en la mayoría de las líneas; 2 o 3 en la cola.
                cantidad = int(rng.choice(UNIDADES_POR_LINEA,
                                          p=PROB_UNIDADES_POR_LINEA))
                precio = float(np.round(precios[j] * (1 - descuento[i, j]), 2))
                items.append({
                    "id": f"OI{n_item:07d}",
                    "order_id": id_orden,
                    "product_id": ids_producto[j],
                    "quantity": cantidad,
                    "unit_price": precio,
                })

        for j in pendientes:
            # Un producto no puede repetirse dentro de la misma orden: eso
            # inflaría el conteo de líneas y rompería el análisis de cesta.
            if j in actual or len(actual) >= objetivo:
                _emitir(actual)
                actual = []
                objetivo = int(rng.integers(1, 4))
            actual.append(j)
        _emitir(actual)

    return pd.DataFrame(ordenes), pd.DataFrame(items)


def _generar_devoluciones(
    cfg: DatasetConfig,
    rng: np.random.Generator,
    ordenes: pd.DataFrame,
    items: pd.DataFrame,
    productos: pd.DataFrame,
    gt: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Genera devoluciones y siembra picos de devoluciones como anomalías.

    Las devoluciones son SIEMPRE posteriores a su compra. Una devolución
    anterior a la orden rompería cualquier cálculo de tasa por ventana temporal,
    y es el tipo de inconsistencia que sobrevive callada hasta que un KPI da
    negativo sin explicación.
    """
    fechas_orden = ordenes.set_index("id")["created_at"]
    items = items.copy()
    items["fecha_orden"] = items["order_id"].map(fechas_orden)

    prob = np.full(len(items), cfg.tasa_devolucion_base)

    # Picos de devoluciones sembrados: un producto concreto en una ventana concreta.
    filas_gt = []
    ventanas = []
    if cfg.n_picos_devoluciones > 0 and len(productos) > 0:
        for _ in range(cfg.n_picos_devoluciones):
            pid = productos.iloc[int(rng.integers(0, len(productos)))]["id"]
            base = items[items["product_id"] == pid]["fecha_orden"]
            if base.empty:
                continue
            inicio = base.min() + (base.max() - base.min()) * float(rng.uniform(0.2, 0.75))
            fin = inicio + timedelta(days=int(rng.integers(4, 12)))
            mascara = (
                (items["product_id"] == pid)
                & (items["fecha_orden"] >= inicio)
                & (items["fecha_orden"] <= fin)
            ).to_numpy()
            if not mascara.any():
                continue
            prob[mascara] = 0.42
            ventanas.append((pid, inicio, fin))
            filas_gt.append({
                "tipo": "pico_devoluciones",
                "product_id": pid,
                "fecha": inicio.date(),
                "magnitud": 0.42,
                "descripcion": (
                    "tasa de devolución elevada por lote defectuoso, "
                    f"hasta {fin.date()}"
                ),
            })

    devuelto = rng.random(len(items)) < prob
    seleccion = items[devuelto]

    demora = rng.integers(1, 31, len(seleccion))
    devoluciones = pd.DataFrame({
        "order_item_id": seleccion["id"].to_numpy(),
        "reason": rng.choice(MOTIVOS_DEVOLUCION, len(seleccion)),
        "created_at": seleccion["fecha_orden"].to_numpy()
        + pd.to_timedelta(demora, unit="D"),
    })

    if filas_gt:
        gt = pd.concat([gt, pd.DataFrame(filas_gt)], ignore_index=True)
    return devoluciones, gt


def _generar_inventario(
    cfg: DatasetConfig,
    rng: np.random.Generator,
    pedidos: np.ndarray,
    productos: pd.DataFrame,
    fechas: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Stock diario por producto: reposición periódica menos lo vendido."""
    n_dias, n_prod = pedidos.shape
    stock = np.zeros((n_dias, n_prod), dtype=int)
    nivel = rng.integers(60, 260, n_prod)

    for i in range(n_dias):
        nivel = nivel - (pedidos[i] * UNIDADES_POR_PEDIDO).astype(int)
        # Reposición semanal, con faltantes ocasionales que explican las caídas.
        if i % 7 == 0:
            nivel = nivel + rng.integers(20, 130, n_prod)
        nivel = np.maximum(nivel, 0)
        stock[i] = nivel

    return pd.DataFrame({
        "product_id": np.tile(productos["id"].to_numpy(), n_dias),
        "date": np.repeat([f.date() for f in fechas], n_prod),
        "stock": stock.reshape(-1),
    })


def generar_dataset(cfg: DatasetConfig) -> dict[str, pd.DataFrame]:
    """Genera el dataset completo. Determinístico dado `cfg.seed`.

    Devuelve las siete tablas del modelo más `ground_truth`, que declara los
    eventos anómalos sembrados a propósito.
    """
    rng = np.random.default_rng(cfg.seed)
    fechas = _rango_fechas(cfg)

    productos = _generar_productos(cfg, rng)
    clientes = _generar_clientes(cfg, rng)
    pedidos, gt, campanias = _matriz_demanda(cfg, rng, productos, fechas)
    ordenes, items = _generar_ordenes(
        cfg, rng, pedidos, productos, clientes, fechas, campanias
    )
    devoluciones, gt = _generar_devoluciones(cfg, rng, ordenes, items, productos, gt)
    inventario = _generar_inventario(cfg, rng, pedidos, productos, fechas)

    return {
        "products": productos,
        "customers": clientes,
        "orders": ordenes,
        "order_items": items.drop(columns=["fecha_orden"], errors="ignore"),
        "returns": devoluciones,
        "campaigns": campanias,
        "inventory": inventario,
        "ground_truth": gt,
    }
