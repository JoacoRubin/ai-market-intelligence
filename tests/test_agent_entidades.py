"""Tests de la extracción determinística de entidades.

Un identificador de producto es un patrón: la letra P y hasta seis dígitos. Un
regex lo extrae perfecto, gratis y sin equivocarse nunca. Pedírselo a un modelo
de lenguaje probabilístico que corre a 8 tokens por segundo es delegar al LLM
algo que el software clásico resuelve mejor.

El diagnóstico del caso hyb-02 mostró el costo concreto de no hacerlo: cuando el
modelo clasificaba mal la intención, devolvía `product_ids: []` aunque el
identificador estuviera escrito en la consulta. Un error contaminaba al otro
porque las dos tareas viajaban en la misma llamada.

Separarlas hace que la extracción sea imposible de fallar, y deja al modelo una
sola tarea: clasificar.
"""

from datetime import date

import pytest

from agent.nodes.entidades import (
    corregir_intencion_por_entidades,
    extraer_periodo,
    extraer_product_ids,
)
from agent.state import Intencion, Periodo

# --- Extracción --------------------------------------------------------------

@pytest.mark.parametrize("consulta, esperado", [
    ("Compará P001 y P002 en los últimos 30 días", ["P001", "P002"]),
    ("El P010 cayó fuerte. Revisá los números y buscá qué pasó en el sector",
     ["P010"]),
    ("¿Cuál fue el margen del P012 durante enero?", ["P012"]),
    ("P007 vs P011: unidades y revenue", ["P007", "P011"]),
    ("Revisá el producto P030 por favor", ["P030"]),
    ("Compará Apple vs Microsoft", []),
    ("Contame un chiste", []),
])
def test_extrae_los_identificadores_presentes(consulta, esperado):
    assert extraer_product_ids(consulta) == esperado


def test_es_insensible_a_mayusculas():
    assert extraer_product_ids("compará p001 con P002") == ["P001", "P002"]


def test_conserva_el_orden_de_aparicion():
    """El orden importa para el informe: el primero mencionado suele ser el
    producto sobre el que se pregunta."""
    assert extraer_product_ids("Compará P050 contra P002") == ["P050", "P002"]


def test_elimina_duplicados():
    assert extraer_product_ids("P001 vs P001 y P002") == ["P001", "P002"]


def test_no_confunde_palabras_que_empiezan_con_p():
    """'Producto 10' no es un identificador; 'PYME2024' tampoco."""
    assert extraer_product_ids("El producto 10 de la PYME2024") == []


def test_no_captura_identificadores_pegados_a_otras_palabras():
    assert extraer_product_ids("REPO01 y XP001Y no son productos") == []


def test_respeta_los_limites_de_palabra_con_puntuacion():
    assert extraer_product_ids("¿Y el P003? Comparalo con (P004).") == ["P003", "P004"]


def test_rechaza_identificadores_demasiado_largos():
    assert extraer_product_ids("El P1234567890 no existe") == []


def test_limita_la_cantidad():
    """Una consulta con cincuenta productos no es un análisis comparativo."""
    consulta = " ".join(f"P{i:03d}" for i in range(1, 40))
    assert len(extraer_product_ids(consulta)) <= 10


# --- Corrección de intención -------------------------------------------------

def test_company_research_con_productos_internos_se_corrige_a_hybrid():
    """La contradicción que el diagnóstico dejó a la vista.

    `company_research` significa, por definición, una consulta sobre empresas
    EXTERNAS. Si la consulta nombra productos del catálogo interno, la
    clasificación es incorrecta: como mínimo es híbrida.

    Esto no es parchear al modelo: es aplicar una regla del dominio que el
    software puede verificar y el modelo no.
    """
    corregida, motivo = corregir_intencion_por_entidades(
        Intencion.COMPANY_RESEARCH, ["P010"]
    )
    assert corregida == Intencion.HYBRID
    assert motivo is not None


def test_company_research_sin_productos_se_mantiene():
    corregida, motivo = corregir_intencion_por_entidades(
        Intencion.COMPANY_RESEARCH, []
    )
    assert corregida == Intencion.COMPANY_RESEARCH
    assert motivo is None


def test_product_performance_con_productos_se_mantiene():
    corregida, _ = corregir_intencion_por_entidades(
        Intencion.PRODUCT_PERFORMANCE, ["P001", "P002"]
    )
    assert corregida == Intencion.PRODUCT_PERFORMANCE


def test_product_performance_sin_productos_queda_fuera_de_alcance():
    """Sin productos identificados no hay nada que consultar. Seguir llevaría al
    sintetizador a redactar sobre la nada."""
    corregida, motivo = corregir_intencion_por_entidades(
        Intencion.PRODUCT_PERFORMANCE, []
    )
    assert corregida == Intencion.FUERA_DE_ALCANCE
    assert motivo is not None


def test_hybrid_sin_productos_queda_fuera_de_alcance():
    corregida, _ = corregir_intencion_por_entidades(Intencion.HYBRID, [])
    assert corregida == Intencion.FUERA_DE_ALCANCE


def test_fuera_de_alcance_con_productos_no_se_fuerza():
    """Si el modelo entendió que la consulta no corresponde, la presencia de un
    identificador no lo contradice: "borrá el P001" nombra un producto y sigue
    estando fuera de alcance.
    """
    corregida, _ = corregir_intencion_por_entidades(
        Intencion.FUERA_DE_ALCANCE, ["P001"]
    )
    assert corregida == Intencion.FUERA_DE_ALCANCE


# --- Extracción determinística del período -----------------------------------
#
# El mismo argumento que ya justifica extraer los product_ids con un regex, y
# que está escrito en `router.ESQUEMA`: "es un patrón fijo, y el software lo
# resuelve perfecto, gratis y sin equivocarse".
#
# El costo de no hacerlo se midió el 2026-08-12. El router le pedía al modelo un
# número de DÍAS y `_normalizar_periodo` armaba siempre `[hoy - dias, hoy]`, así
# que el agente no podía representar un período histórico cerrado. La consulta
# "Analizá el desempeño de P010 durante 2025-06" se resolvía como
# 2025-12-30 → 2026-06-30: el mes preguntado quedaba FUERA del período
# analizado, y ninguna métrica del eval lo detectaba.
#
# Y el few-shot enseñaba el error: "¿el margen del P012 durante enero?" ->
# {"dias": 31}. Enero no es "31 días hacia atrás desde hoy", es un mes concreto.

HOY = date(2026, 6, 30)


def test_extrae_un_mes_escrito_como_aaaa_mm():
    """El caso exacto de las consultas del eval."""
    assert extraer_periodo("Analizá el desempeño de P010 durante 2025-06", HOY) == (
        Periodo(desde=date(2025, 6, 1), hasta=date(2025, 6, 30))
    )


def test_el_mes_termina_el_ultimo_dia_que_le_corresponde():
    """Febrero de un año no bisiesto tiene 28. Calcularlo con un `timedelta(30)`
    es la clase de error que después aparece como un dato faltante."""
    assert extraer_periodo("ventas de P001 en 2026-02", HOY) == Periodo(
        desde=date(2026, 2, 1), hasta=date(2026, 2, 28)
    )


def test_reconoce_el_mes_escrito_en_castellano():
    assert extraer_periodo("¿Cuál fue el margen del P012 en junio de 2025?", HOY) == (
        Periodo(desde=date(2025, 6, 1), hasta=date(2025, 6, 30))
    )


@pytest.mark.parametrize("texto", [
    "marzo 2026", "en Marzo de 2026", "durante marzo del 2026",
])
def test_acepta_las_formas_naturales_de_escribir_un_mes(texto):
    assert extraer_periodo(f"P001 {texto}", HOY) == Periodo(
        desde=date(2026, 3, 1), hasta=date(2026, 3, 31)
    )


def test_un_rango_explicito_se_respeta_tal_cual():
    assert extraer_periodo("P001 entre 2025-03-15 y 2025-04-10", HOY) == Periodo(
        desde=date(2025, 3, 15), hasta=date(2025, 4, 10)
    )


def test_sin_periodo_explicito_devuelve_none():
    """`None` significa "no hay nada que extraer", y ahí sí decide el modelo con
    su cantidad de días. No se inventa un rango: eso es lo que hacía el bug."""
    assert extraer_periodo("Compará P001 y P002 en los últimos 30 días", HOY) is None


def test_un_identificador_de_producto_no_se_confunde_con_un_ano():
    """`P2025` es un producto. Leerlo como año sería el mismo error que contar
    `doc_112` como una cifra de negocio."""
    assert extraer_periodo("Analizá P2025 y P2026", HOY) is None


def test_un_mes_imposible_no_se_acepta():
    assert extraer_periodo("P001 durante 2025-13", HOY) is None


def test_un_rango_invertido_no_se_acepta():
    """Si `desde` es posterior a `hasta`, el `Periodo` sería inválido. Devolver
    `None` deja que el flujo normal siga en vez de reventar tres capas después."""
    assert extraer_periodo("P001 entre 2025-04-10 y 2025-03-15", HOY) is None


def test_no_devuelve_un_periodo_futuro():
    """El dataset termina en `hoy`. Un período enteramente posterior no tiene
    datos que analizar, y arrastrarlo produce un informe vacío sin explicación."""
    assert extraer_periodo("P001 durante 2027-01", HOY) is None
