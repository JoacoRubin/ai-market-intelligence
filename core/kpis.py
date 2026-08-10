"""KPIs comerciales en T-SQL.

Estas funciones son la única fuente de los números del informe. El LLM nunca
calcula una métrica: interpreta y redacta lo que sale de acá.

Tres decisiones de negocio, fijadas y explícitas (ver ADR-005). No son detalles
de implementación: cambian los números y cualquiera de las tres, resuelta de
forma distinta en dos lugares, produce un informe que se contradice a sí mismo.

  1. Las órdenes canceladas NO cuentan como venta. Son el 3,5% del dataset.
  2. Las devoluciones NO se restan del revenue. Revenue bruto y tasa de
     devolución son métricas distintas; netearlas esconde el problema.
  3. El margen se calcula sobre el precio EFECTIVAMENTE PAGADO (`unit_price`),
     no sobre el de lista. Con campañas de descuento activas, usar el de lista
     infla el margen y el informe miente sin que se note.

Seguridad: todas las consultas son parametrizadas y corren con el usuario
read-only. No se construye SQL por concatenación en ningún punto — ni siquiera
para los identificadores, que son literales fijos en el código.
"""

from __future__ import annotations

from datetime import date, timedelta

from core.db import cursor_lectura
from core.report import MetricaProducto

FUENTE = "sql:product_metrics"

# Condición común a todas las métricas de venta. Vive en una sola constante
# para que la regla "las canceladas no son ventas" no pueda divergir entre
# consultas: si mañana cambia, cambia en un solo lugar.
_VENTAS_VALIDAS = """
      AND o.status <> 'cancelada'
      AND CAST(o.created_at AS DATE) BETWEEN ? AND ?
"""


def _rango(desde: date, hasta: date) -> tuple[str, str]:
    """Fechas como texto ISO.

    El driver ODBC legacy de Windows no siempre acepta objetos `date` como
    parámetro; SQL Server parsea ISO sin ambigüedad y funciona con cualquier
    driver. Ver ADR-003.
    """
    return desde.isoformat(), hasta.isoformat()


def unidades_vendidas(product_id: str, desde: date, hasta: date) -> int:
    """Unidades vendidas del producto en el rango, excluyendo canceladas."""
    sql = f"""
        SELECT COALESCE(SUM(oi.quantity), 0)
        FROM dbo.order_items oi
        JOIN dbo.orders o ON o.id = oi.order_id
        WHERE oi.product_id = ?
        {_VENTAS_VALIDAS}
    """
    with cursor_lectura() as cur:
        fila = cur.execute(sql, (product_id, *_rango(desde, hasta))).fetchone()
    return int(fila[0] or 0)


def revenue(product_id: str, desde: date, hasta: date) -> float:
    """Revenue bruto: cantidad por precio pagado. Sin netear devoluciones."""
    sql = f"""
        SELECT COALESCE(SUM(oi.quantity * oi.unit_price), 0)
        FROM dbo.order_items oi
        JOIN dbo.orders o ON o.id = oi.order_id
        WHERE oi.product_id = ?
        {_VENTAS_VALIDAS}
    """
    with cursor_lectura() as cur:
        fila = cur.execute(sql, (product_id, *_rango(desde, hasta))).fetchone()
    return float(fila[0] or 0.0)


def margen_pct(product_id: str, desde: date, hasta: date) -> float | None:
    """Margen porcentual sobre el precio pagado.

    Devuelve None si no hubo ventas: sin revenue no hay margen que informar.
    Devolver 0 sería afirmar algo que no se sabe, y el informe lo mostraría
    como un hecho.
    """
    sql = f"""
        SELECT COALESCE(SUM(oi.quantity * oi.unit_price), 0) AS revenue,
               COALESCE(SUM(oi.quantity * p.cost), 0)        AS costo
        FROM dbo.order_items oi
        JOIN dbo.orders o   ON o.id = oi.order_id
        JOIN dbo.products p ON p.id = oi.product_id
        WHERE oi.product_id = ?
        {_VENTAS_VALIDAS}
    """
    with cursor_lectura() as cur:
        fila = cur.execute(sql, (product_id, *_rango(desde, hasta))).fetchone()

    rev, costo = float(fila[0] or 0.0), float(fila[1] or 0.0)
    if rev == 0:
        return None
    return (rev - costo) / rev * 100


def crecimiento_pct(product_id: str, desde: date, hasta: date) -> float | None:
    """Variación de revenue contra el período previo de igual duración.

    Devuelve None si no hubo revenue en el período previo. Un producto sin
    ventas anteriores no creció 0%: no tiene crecimiento medible. La distinción
    importa, porque 0% se lee como "se mantuvo estable".
    """
    dias = (hasta - desde).days
    desde_previo = desde - timedelta(days=dias + 1)
    hasta_previo = desde - timedelta(days=1)

    previo = revenue(product_id, desde_previo, hasta_previo)
    if previo == 0:
        return None

    actual = revenue(product_id, desde, hasta)
    return (actual - previo) / previo * 100


def tasa_devolucion_pct(product_id: str, desde: date, hasta: date) -> float | None:
    """Porcentaje de líneas de venta del rango que terminaron devueltas.

    Se cuenta por línea vendida en el período, sin importar cuándo se procesó
    la devolución: lo que se mide es la calidad de lo vendido en ese rango, no
    el volumen de devoluciones tramitadas en él.
    """
    sql = f"""
        SELECT COUNT(*) AS lineas,
               COALESCE(SUM(CASE WHEN d.order_item_id IS NOT NULL THEN 1 ELSE 0 END), 0)
                   AS devueltas
        FROM dbo.order_items oi
        JOIN dbo.orders o ON o.id = oi.order_id
        LEFT JOIN (SELECT DISTINCT order_item_id FROM dbo.returns) d
               ON d.order_item_id = oi.id
        WHERE oi.product_id = ?
        {_VENTAS_VALIDAS}
    """
    with cursor_lectura() as cur:
        fila = cur.execute(sql, (product_id, *_rango(desde, hasta))).fetchone()

    lineas, devueltas = int(fila[0] or 0), int(fila[1] or 0)
    if lineas == 0:
        return None
    return devueltas / lineas * 100


def _nombre_de_producto(product_id: str) -> str:
    sql = "SELECT brand, category FROM dbo.products WHERE id = ?"
    with cursor_lectura() as cur:
        fila = cur.execute(sql, (product_id,)).fetchone()
    if fila is None:
        return product_id
    return f"{fila[0]} {fila[1]}"


def metricas_de_producto(
    product_id: str, desde: date, hasta: date
) -> MetricaProducto:
    """Todos los KPIs de un producto, listos para el informe.

    Es lo que va a devolver la tool `product_metrics` del agente: entrega
    directamente el modelo del informe, sin traducciones intermedias donde se
    puedan perder las reglas de negocio.
    """
    return MetricaProducto(
        product_id=product_id,
        nombre=_nombre_de_producto(product_id),
        unidades=unidades_vendidas(product_id, desde, hasta),
        revenue=revenue(product_id, desde, hasta),
        margen_pct=margen_pct(product_id, desde, hasta),
        crecimiento_pct=crecimiento_pct(product_id, desde, hasta),
        tasa_devolucion_pct=tasa_devolucion_pct(product_id, desde, hasta),
        fuente=FUENTE,
    )
