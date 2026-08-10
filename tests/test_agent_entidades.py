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

import pytest

from agent.nodes.entidades import (
    corregir_intencion_por_entidades,
    extraer_product_ids,
)
from agent.state import Intencion

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
