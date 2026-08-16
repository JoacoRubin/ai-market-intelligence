"""Carga del dataset sintético en SQL Server.

Orden de inserción dictado por las claves foráneas: primero el catálogo, después
lo transaccional. Si el orden se rompe, la base rechaza la carga — que es
exactamente lo que debe pasar. Las FK no son burocracia: son la red que evita
que un ETL a medio terminar deje datos incoherentes que después nadie explica.

La carga es idempotente por truncado: se vacían las tablas en orden inverso y se
vuelve a insertar. Con datos sintéticos regenerables, es más simple y más
confiable que intentar un upsert.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from typing import Any

import pandas as pd

from core.db import conectar_admin, driver_disponible
from seeds.generate import DatasetConfig, generar_dataset

# Orden de inserción: respeta las dependencias de clave foránea.
ORDEN_CARGA: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("products", ("id", "brand", "category", "price", "cost", "launch_date")),
    ("customers", ("id", "segment", "region", "created_at")),
    ("orders", ("id", "customer_id", "created_at", "channel", "status")),
    ("order_items", ("id", "order_id", "product_id", "quantity", "unit_price")),
    ("returns", ("order_item_id", "reason", "created_at")),
    ("inventory", ("product_id", "date", "stock")),
    ("campaigns", ("product_id", "start_date", "end_date", "spend", "discount")),
    ("ground_truth", ("tipo", "product_id", "fecha", "magnitud", "descripcion")),
)


def _a_tipo_nativo(valor: Any, fechas_como_texto: bool = False) -> Any:
    """Convierte tipos de NumPy/pandas a tipos que el driver ODBC entiende.

    pyodbc no sabe qué hacer con np.int64 ni con pd.Timestamp. Sin esta
    conversión la carga falla con errores de binding poco descriptivos.

    `fechas_como_texto` existe solo por el driver legacy de Windows, que no
    implementa el binding de `datetime` en `executemany` y responde
    "HYC00: Optional feature not implemented". SQL Server parsea sin problema
    las fechas en formato ISO, así que pasarlas como texto es una salida segura.
    Con ODBC Driver 17/18 no hace falta y se envían como datetime nativo.
    """
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    if isinstance(valor, pd.Timestamp):
        valor = valor.to_pydatetime()
    if isinstance(valor, datetime):
        return valor.strftime("%Y-%m-%d %H:%M:%S") if fechas_como_texto else valor
    if isinstance(valor, date):
        return valor.isoformat() if fechas_como_texto else valor
    if isinstance(valor, str):
        return str(valor)  # normaliza np.str_ a str nativo
    if hasattr(valor, "item"):  # escalares de NumPy
        return valor.item()
    return valor


def _filas(
    df: pd.DataFrame, columnas: tuple[str, ...], fechas_como_texto: bool = False
) -> list[tuple[Any, ...]]:
    return [
        tuple(_a_tipo_nativo(v, fechas_como_texto) for v in fila)
        for fila in df[list(columnas)].itertuples(index=False, name=None)
    ]


def cargar(cfg: DatasetConfig | None = None, verbose: bool = True) -> dict[str, int]:
    cfg = cfg or DatasetConfig()
    ds = generar_dataset(cfg)

    con = conectar_admin()
    cur = con.cursor()

    # fast_executemany acelera muchísimo la inserción masiva, pero dimensiona el
    # buffer de cada columna a partir de la PRIMERA fila del lote. Si una fila
    # posterior trae un string más largo, lo trunca en silencio o falla con
    # "String data, right truncation" — sin decir qué columna ni qué fila.
    # Los drivers modernos manejan bien el caso; el legacy de Windows no.
    # Ante la duda, correctitud antes que velocidad.
    driver_moderno = driver_disponible().startswith("ODBC Driver")
    cur.fast_executemany = driver_moderno
    fechas_como_texto = not driver_moderno

    cargadas: dict[str, int] = {}
    try:
        # Vaciado en orden inverso para no violar las claves foráneas.
        for tabla, _ in reversed(ORDEN_CARGA):
            cur.execute(f"DELETE FROM dbo.{tabla}")
        con.commit()

        for tabla, columnas in ORDEN_CARGA:
            df = ds[tabla]
            if df.empty:
                cargadas[tabla] = 0
                continue
            marcadores = ", ".join("?" * len(columnas))
            sql = (
                f"INSERT INTO dbo.{tabla} ({', '.join(columnas)}) "
                f"VALUES ({marcadores})"
            )
            cur.executemany(sql, _filas(df, columnas, fechas_como_texto))
            cargadas[tabla] = len(df)
            if verbose:
                print(f"  {tabla:<14} {len(df):>7,} filas", flush=True)

        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    return cargadas


if __name__ == "__main__":
    print("Generando y cargando dataset en SQL Server...")
    resultado = cargar()
    print(f"\nTotal: {sum(resultado.values()):,} filas cargadas.")
    sys.exit(0)
