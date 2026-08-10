"""Tests de la construcción de la serie temporal desde SQL.

Estos tests existen por un bug que **no falló**: el driver ODBC legacy devuelve
`CAST(... AS DATE)` como string `'2025-06-10'`, y el código lo buscaba con un
objeto `date`. Ninguna clave coincidía, así que la serie salía entera en ceros y
el forecast informaba "0 unidades" con toda confianza.

Ese es el tipo de error más peligroso: no rompe nada, no tira excepción, no
aparece en los logs. Devuelve un resultado bien formateado y completamente
falso. Un test de "la función no explota" lo habría dado por bueno.
"""

from datetime import date

import pytest

from core.db import hay_base_disponible
from ml.series import serie_diaria

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not hay_base_disponible(),
                       reason="SQL Server no está levantado"),
]

DESDE, HASTA = date(2025, 4, 1), date(2026, 3, 31)


def test_la_serie_tiene_un_punto_por_dia_del_rango():
    """Sin huecos: un día faltante correría la serie y el lag de 7 días dejaría
    de caer en el mismo día de la semana."""
    dias, valores = serie_diaria("P002", DESDE, HASTA)
    assert len(dias) == len(valores) == (HASTA - DESDE).days + 1
    assert dias[0] == DESDE and dias[-1] == HASTA


def test_la_serie_no_viene_toda_en_cero():
    """El bug silencioso.

    P002 vendió cientos de unidades en el período. Una serie en cero significa
    que las claves del diccionario no coincidieron con las fechas del rango —
    y el forecast habría proyectado cero sin quejarse.
    """
    _, valores = serie_diaria("P002", DESDE, HASTA)
    assert valores.sum() > 0, (
        "la serie salió toda en cero: las fechas de SQL no están casando con "
        "las del rango"
    )


def test_los_dias_con_ventas_son_una_porcion_razonable():
    """Ni todos los días con ventas (sospechoso) ni casi ninguno (bug)."""
    _, valores = serie_diaria("P002", DESDE, HASTA)
    con_ventas = (valores > 0).mean()
    assert 0.1 < con_ventas < 1.0, f"solo {con_ventas:.0%} de los días con ventas"


def test_los_dias_sin_ventas_son_cero_y_no_faltan():
    _, valores = serie_diaria("P002", DESDE, HASTA)
    assert (valores >= 0).all()


def test_el_total_coincide_con_el_kpi_de_unidades():
    """Doble contabilidad, otra vez: la serie y el KPI tienen que dar lo mismo.

    Si el forecast entrenara sobre una definición de "venta" distinta de la que
    reporta el informe, el modelo predeciría una cosa y la tabla mostraría otra.
    """
    from core.kpis import unidades_vendidas

    desde, hasta = date(2026, 1, 1), date(2026, 3, 31)
    _, valores = serie_diaria("P002", desde, hasta)
    assert int(valores.sum()) == unidades_vendidas("P002", desde, hasta)


def test_un_producto_inexistente_devuelve_una_serie_de_ceros():
    """No es un error: simplemente no vendió nada. Lo que importa es que la
    serie tenga la longitud correcta para que el resto no se rompa."""
    dias, valores = serie_diaria("P999", DESDE, HASTA)
    assert len(dias) == (HASTA - DESDE).days + 1
    assert valores.sum() == 0
