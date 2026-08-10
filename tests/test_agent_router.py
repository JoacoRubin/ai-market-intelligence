"""Tests del nodo IntentRouter.

Es el nodo donde el spike encontró el problema más serio: `llama3.2:3b`
clasificó mal la intención en 3 de 3 casos, siempre como `company_research`.
El JSON era válido contra el esquema —Pydantic lo habría aprobado— y el
contenido estaba mal. Validez de esquema no implica correctitud semántica.

Estos tests corren con un doble del modelo y verifican lo que el router hace con
la respuesta: normalizarla, defenderse de valores imposibles, aplicar defaults
razonables y degradar sin romperse cuando el modelo falla.

Que el modelo ACIERTE es otra cosa, y se mide aparte con el golden set
(`tests/test_agent_evals.py`, marcado `slow`). Son dos preguntas distintas y
merecen dos suites distintas: una corre en milisegundos en cada commit, la otra
tarda minutos y se corre cuando se toca el prompt.
"""

from datetime import date

import pytest

from agent.llm import ClienteFalso, ClienteQueFalla
from agent.nodes.router import HOY_POR_DEFECTO, enrutar
from agent.state import AnalysisState, Intencion


def _estado(consulta: str = "Compará P001 y P002 en los últimos 30 días") -> AnalysisState:
    return AnalysisState(request_id="req-001", consulta=consulta)


def _respuesta(**kw):
    base = {
        "intencion": "product_performance",
        "product_ids": ["P001", "P002"],
        "dias": 30,
    }
    base.update(kw)
    return base


# --- Camino feliz ------------------------------------------------------------

def test_traslada_la_intencion_al_estado():
    estado = _estado()
    enrutar(estado, ClienteFalso([_respuesta()]))
    assert estado.intencion == Intencion.PRODUCT_PERFORMANCE
    assert estado.entidades == ["P001", "P002"]


def test_calcula_el_periodo_desde_los_dias_pedidos():
    estado = _estado()
    enrutar(estado, ClienteFalso([_respuesta(dias=30)]), hoy=date(2026, 3, 31))
    assert estado.periodo.hasta == date(2026, 3, 31)
    assert estado.periodo.desde == date(2026, 3, 2)


def test_registra_el_paso_en_el_trace():
    estado = _estado()
    enrutar(estado, ClienteFalso([_respuesta()]))
    assert any(p.nodo == "router" for p in estado.trace)


# --- Defensa contra lo que el modelo puede devolver --------------------------

def test_una_intencion_desconocida_no_rompe_el_grafo():
    """El modelo puede inventar un valor fuera del enum aunque el esquema lo
    restrinja. Se degrada a `fuera_de_alcance` y se advierte, en vez de
    explotar en un nodo posterior."""
    estado = _estado()
    enrutar(estado, ClienteFalso([_respuesta(intencion="analisis_cuantico")]))
    assert estado.intencion == Intencion.FUERA_DE_ALCANCE
    assert any("intención" in w.lower() for w in estado.advertencias)


def test_descarta_identificadores_con_formato_invalido():
    """El router no consulta la base, pero sí filtra lo que no puede ser un id.

    Dejar pasar basura hasta la tool solo mueve el error de lugar y gasta una
    llamada del presupuesto.
    """
    estado = _estado()
    enrutar(estado, ClienteFalso([
        _respuesta(product_ids=["P001", "'; DROP TABLE--", "producto A"])
    ]))
    assert estado.entidades == ["P001"]
    assert any("descart" in w.lower() for w in estado.advertencias)


def test_sin_entidades_validas_marca_fuera_de_alcance():
    """Sin productos no hay nada que consultar. Seguir adelante llevaría al
    sintetizador a redactar sobre la nada, que es como nacen los informes
    inventados."""
    estado = _estado("Contame un chiste")
    enrutar(estado, ClienteFalso([
        _respuesta(intencion="product_performance", product_ids=[])
    ]))
    assert estado.intencion == Intencion.FUERA_DE_ALCANCE


def test_dias_absurdos_se_acotan():
    estado = _estado()
    enrutar(estado, ClienteFalso([_respuesta(dias=99999)]),
            hoy=date(2026, 3, 31))
    assert (estado.periodo.hasta - estado.periodo.desde).days <= 366 * 3


def test_dias_ausente_usa_el_default():
    estado = _estado()
    enrutar(estado, ClienteFalso([{"intencion": "product_performance",
                                   "product_ids": ["P001"]}]),
            hoy=date(2026, 3, 31))
    assert (estado.periodo.hasta - estado.periodo.desde).days == 29


def test_demasiadas_entidades_se_recortan():
    estado = _estado()
    enrutar(estado, ClienteFalso([
        _respuesta(product_ids=[f"P{i:03d}" for i in range(1, 30)])
    ]))
    assert len(estado.entidades) <= 10
    assert any("recort" in w.lower() for w in estado.advertencias)


# --- Degradación -------------------------------------------------------------

def test_si_el_modelo_no_responde_el_grafo_no_muere():
    """Un LLM caído no puede tumbar el sistema.

    El estado queda marcado como fuera de alcance con el error registrado, y el
    grafo puede decidir qué hacer. Propagar la excepción dejaría al usuario con
    un 500 y sin explicación.
    """
    estado = _estado()
    enrutar(estado, ClienteQueFalla())
    assert estado.intencion == Intencion.FUERA_DE_ALCANCE
    assert estado.error is not None
    assert any("modelo" in w.lower() for w in estado.advertencias)


def test_un_json_incompleto_no_rompe():
    estado = _estado()
    enrutar(estado, ClienteFalso([{}]))
    assert estado.intencion == Intencion.FUERA_DE_ALCANCE


# --- El prompt ---------------------------------------------------------------

def test_el_prompt_incluye_ejemplos():
    """Los few-shots son la corrección al problema medido en el spike.

    Si alguien los borra al 'limpiar' el prompt, este test lo detecta. Un prompt
    sin ejemplos es un cambio de comportamiento disfrazado de refactor.
    """
    estado = _estado()
    cliente = ClienteFalso([_respuesta()])
    enrutar(estado, cliente)

    sistema = cliente.llamadas[0]["sistema"]
    assert "product_performance" in sistema
    assert "company_research" in sistema
    assert sistema.lower().count("ejemplo") >= 3, (
        "el prompt del router perdió sus few-shots"
    )


def test_el_prompt_aclara_que_comparar_productos_no_es_investigar_empresas():
    """La confusión exacta que el modelo tuvo en el spike, atacada de frente."""
    estado = _estado()
    cliente = ClienteFalso([_respuesta()])
    enrutar(estado, cliente)
    sistema = cliente.llamadas[0]["sistema"].lower()
    assert "producto" in sistema and "empresa" in sistema


@pytest.mark.parametrize("hoy", [date(2026, 3, 31), HOY_POR_DEFECTO])
def test_la_fecha_de_referencia_es_inyectable(hoy):
    """Nada de `datetime.now()` adentro del nodo: un test que depende del reloj
    del sistema falla solo un martes cualquiera."""
    estado = _estado()
    enrutar(estado, ClienteFalso([_respuesta(dias=7)]), hoy=hoy)
    assert estado.periodo.hasta == hoy
