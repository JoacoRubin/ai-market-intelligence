"""Series temporales de ventas desde SQL Server.

La serie se arma con las MISMAS reglas de negocio que los KPIs (ADR-005): se
excluyen las órdenes canceladas. Si el forecast entrenara sobre una definición
de "venta" distinta de la que reporta el informe, el modelo predeciría una cosa
y la tabla mostraría otra — y nadie sabría cuál creer.

Los días sin ventas se rellenan con cero, no se omiten. Una serie con huecos
rompe los lags: el "día anterior" dejaría de ser el día anterior.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np

from core.db import cursor_lectura


def _a_date(valor) -> date:
    """Normaliza a `date` lo que devuelva el driver: date, datetime o string."""
    if isinstance(valor, date) and not isinstance(valor, datetime):
        return valor
    if isinstance(valor, datetime):
        return valor.date()
    return date.fromisoformat(str(valor)[:10])


def serie_diaria(
    product_id: str, desde: date, hasta: date
) -> tuple[list[date], np.ndarray]:
    """Unidades vendidas por día, sin huecos."""
    sql = """
        SELECT CAST(o.created_at AS DATE) AS dia, SUM(oi.quantity) AS unidades
        FROM dbo.order_items oi
        JOIN dbo.orders o ON o.id = oi.order_id
        WHERE oi.product_id = ?
          AND o.status <> 'cancelada'
          AND CAST(o.created_at AS DATE) BETWEEN ? AND ?
        GROUP BY CAST(o.created_at AS DATE)
        ORDER BY dia
    """
    with cursor_lectura() as cur:
        filas = cur.execute(
            sql, (product_id, desde.isoformat(), hasta.isoformat())
        ).fetchall()

    # El driver ODBC legacy devuelve `CAST(... AS DATE)` como string
    # ('2025-06-10'), no como objeto date. Buscar con una clave `date` no
    # coincidía con ninguna y la serie salía ENTERA EN CERO — sin error, sin
    # excepción, sin nada en los logs. El forecast informaba "0 unidades" con
    # total confianza. Normalizar la clave es lo que evita ese silencio.
    por_dia = {_a_date(f[0]): float(f[1]) for f in filas}

    dias, valores = [], []
    actual = desde
    while actual <= hasta:
        dias.append(actual)
        # Un día sin ventas es un cero, no un dato faltante. Omitirlo correría
        # la serie y el lag de 7 días dejaría de caer en el mismo día de semana.
        valores.append(por_dia.get(actual, 0.0))
        actual += timedelta(days=1)

    return dias, np.asarray(valores, dtype=float)
