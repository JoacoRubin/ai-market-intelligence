"""Tests de las herramientas del agente.

Los argumentos de una tool los produce el modelo de lenguaje. Eso los convierte
en **entrada no confiable**, exactamente igual que si vinieran de un formulario
público: el modelo puede equivocarse solo, o puede haber leído un documento con
texto diseñado para inducirlo a algo.

Por eso la tool no recibe una consulta: recibe **parámetros tipados y validados**.
El modelo elige QUÉ herramienta usar y con qué entidades; nunca CÓMO se consulta
la base.

Defensa en capas, de afuera hacia adentro:

  1. Esquema de entrada  -> este archivo
  2. Consulta parametrizada -> core/kpis.py
  3. Usuario read-only   -> tests/test_db_guardrails.py

Las tres tienen que fallar a la vez para que algo pase. Y la tercera la hace
cumplir SQL Server, que no tiene bugs de nuestro lado.
"""

from datetime import date

import pytest
from pydantic import ValidationError

from agent.state import AnalysisState
from agent.tools.product_metrics import (
    EntradaProductMetrics,
    ejecutar_product_metrics,
)
from core.db import hay_base_disponible

DESDE = date(2026, 1, 1)
HASTA = date(2026, 3, 31)


def _estado(**kw) -> AnalysisState:
    base = dict(request_id="req-001", consulta="Compará P001 y P002")
    base.update(kw)
    return AnalysisState(**base)


# --- Validación del esquema: lo que el modelo no puede colar ----------------

@pytest.mark.parametrize("malicioso", [
    "P001'; DROP TABLE products--",
    "P001 OR 1=1",
    "'; SELECT * FROM ground_truth--",
    "../../etc/passwd",
    "P001; EXEC xp_cmdshell 'dir'",
    "<script>alert(1)</script>",
    "P001 UNION SELECT password FROM users",
])
def test_rechaza_identificadores_que_no_son_identificadores(malicioso):
    """El formato del id es una lista blanca, no una lista negra.

    No se intenta detectar ataques —esa carrera se pierde siempre— sino
    describir qué es un id válido: la letra P y hasta seis dígitos. Todo lo
    demás se rechaza sin analizarlo.
    """
    with pytest.raises(ValidationError):
        EntradaProductMetrics(product_ids=[malicioso], desde=DESDE, hasta=HASTA)


def test_acepta_identificadores_bien_formados():
    e = EntradaProductMetrics(product_ids=["P001", "P042"], desde=DESDE, hasta=HASTA)
    assert e.product_ids == ["P001", "P042"]


def test_rechaza_lista_vacia():
    with pytest.raises(ValidationError):
        EntradaProductMetrics(product_ids=[], desde=DESDE, hasta=HASTA)


def test_rechaza_demasiados_productos():
    """Un tope explícito evita que una alucinación del modelo se convierta en
    una consulta de cientos de productos y un informe ilegible."""
    with pytest.raises(ValidationError):
        EntradaProductMetrics(product_ids=[f"P{i:03d}" for i in range(50)],
                              desde=DESDE, hasta=HASTA)


def test_rechaza_rango_invertido():
    with pytest.raises(ValidationError):
        EntradaProductMetrics(product_ids=["P001"], desde=HASTA, hasta=DESDE)


def test_rechaza_rangos_absurdamente_largos():
    """Un rango de veinte años no es un análisis: es un escaneo completo de la
    tabla que va a tardar y no le sirve a nadie."""
    with pytest.raises(ValidationError):
        EntradaProductMetrics(product_ids=["P001"],
                              desde=date(2000, 1, 1), hasta=date(2026, 1, 1))


def test_elimina_duplicados_en_vez_de_fallar():
    """Que el modelo repita un producto es un error inocente y previsible.
    Normalizarlo es mejor que rechazar la llamada entera y gastar un reintento.
    """
    e = EntradaProductMetrics(product_ids=["P001", "P001", "P002"],
                              desde=DESDE, hasta=HASTA)
    assert e.product_ids == ["P001", "P002"]


# --- Ejecución ---------------------------------------------------------------

@pytest.mark.db
@pytest.mark.skipif(not hay_base_disponible(), reason="SQL Server no está levantado")
def test_devuelve_metricas_de_los_productos_pedidos():
    estado = _estado()
    entrada = EntradaProductMetrics(product_ids=["P002", "P003"],
                                    desde=DESDE, hasta=HASTA)
    resultado = ejecutar_product_metrics(entrada, estado)

    assert len(resultado) == 2
    assert {m.product_id for m in resultado} == {"P002", "P003"}
    assert all(m.fuente.startswith("sql:") for m in resultado)


@pytest.mark.db
@pytest.mark.skipif(not hay_base_disponible(), reason="SQL Server no está levantado")
def test_la_ejecucion_consume_presupuesto_de_herramientas():
    estado = _estado(max_llamadas_tools=5)
    entrada = EntradaProductMetrics(product_ids=["P002"], desde=DESDE, hasta=HASTA)
    ejecutar_product_metrics(entrada, estado)
    assert estado.llamadas_tools == 1


@pytest.mark.db
@pytest.mark.skipif(not hay_base_disponible(), reason="SQL Server no está levantado")
def test_sin_presupuesto_no_se_ejecuta():
    """Agotado el presupuesto, la tool no corre y el estado queda advertido.

    Es el freno que impide que un loop de replanificación consuma la máquina.
    """
    estado = _estado(max_llamadas_tools=1)
    estado.registrar_llamada_tool()

    entrada = EntradaProductMetrics(product_ids=["P002"], desde=DESDE, hasta=HASTA)
    resultado = ejecutar_product_metrics(entrada, estado)

    assert resultado == []
    assert any("límite" in w.lower() or "limite" in w.lower()
               for w in estado.advertencias), estado.advertencias


@pytest.mark.db
@pytest.mark.skipif(not hay_base_disponible(), reason="SQL Server no está levantado")
def test_un_producto_inexistente_no_rompe_la_ejecucion():
    """El modelo puede alucinar un id con formato válido pero sin existir.

    La tool devuelve lo que encontró y deja constancia de lo que no. Fallar
    entera desperdiciaría los datos que sí obtuvo.
    """
    estado = _estado()
    entrada = EntradaProductMetrics(product_ids=["P002", "P999"],
                                    desde=DESDE, hasta=HASTA)
    resultado = ejecutar_product_metrics(entrada, estado)

    assert [m.product_id for m in resultado] == ["P002"]
    assert any("P999" in w for w in estado.advertencias), estado.advertencias


@pytest.mark.db
@pytest.mark.skipif(not hay_base_disponible(), reason="SQL Server no está levantado")
def test_la_ejecucion_queda_registrada_en_el_trace():
    estado = _estado()
    entrada = EntradaProductMetrics(product_ids=["P002"], desde=DESDE, hasta=HASTA)
    ejecutar_product_metrics(entrada, estado)
    assert any(p.tool == "product_metrics" for p in estado.trace)


# --- Contrato para el modelo -------------------------------------------------

def test_la_tool_publica_su_esquema_para_tool_calling():
    """El modelo necesita el esquema JSON para saber qué argumentos mandar.

    Se deriva del mismo Pydantic que valida la entrada: un esquema escrito a
    mano se desincroniza del validador, y ahí el modelo manda algo que la
    documentación permitía y el código rechaza.
    """
    from agent.tools.product_metrics import esquema_para_llm

    esquema = esquema_para_llm()
    assert esquema["type"] == "function"
    assert esquema["function"]["name"] == "product_metrics"
    props = esquema["function"]["parameters"]["properties"]
    assert {"product_ids", "desde", "hasta"} <= set(props)
    assert "description" in esquema["function"]
