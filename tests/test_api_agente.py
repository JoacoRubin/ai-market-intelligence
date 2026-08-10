"""Tests de la API con el agente conectado.

La API acepta dos formas de crear un análisis:

  1. **Estructurada**: `product_ids` + rango de fechas. Ya se sabe qué se
     quiere, así que el router NO se invoca: son 77 segundos de LLM que no
     aportan nada.
  2. **Lenguaje natural**: `consulta`. Ahí sí interviene el agente completo.

Las dos terminan en el mismo grafo y producen el mismo recurso. Eso es lo que
permite que el PDF, el JSON y la web sigan siendo una sola cosa.

El cliente del modelo se inyecta con el sistema de dependencias de FastAPI, así
que estos tests corren en milisegundos con un doble. Sin eso, cada test
esperaría los ~2m44s que tarda el agente real.
"""

import pytest
from fastapi.testclient import TestClient

from agent.llm import ClienteFalso, ClienteQueFalla
from apps.api.main import app, obtener_cliente_llm
from core.db import hay_base_disponible

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not hay_base_disponible(),
                       reason="SQL Server no está levantado"),
]

DESDE, HASTA = "2026-01-01", "2026-03-31"


def _router(intencion="product_performance", dias=90):
    return {"intencion": intencion, "dias": dias}


def _sintesis(*textos):
    return {"conclusiones": list(textos)}


@pytest.fixture
def cliente_falso():
    """Inyecta un doble del modelo en la app."""
    doble = ClienteFalso([_router(), _sintesis("Actividad registrada")])
    app.dependency_overrides[obtener_cliente_llm] = lambda: doble
    yield doble
    app.dependency_overrides.clear()


@pytest.fixture
def api():
    with TestClient(app) as c:
        yield c


# --- Forma estructurada: sin router ------------------------------------------

def test_con_product_ids_no_se_invoca_al_router(api, cliente_falso):
    """Si ya vienen los identificadores y el rango, interpretar es innecesario.

    Cada llamada evitada al modelo son decenas de segundos en esta máquina. El
    router se saltea y el grafo arranca en la planificación.
    """
    r = api.post("/analyses", json={
        "product_ids": ["P002", "P003"], "desde": DESDE, "hasta": HASTA,
    })
    assert r.status_code == 202

    tipos = [ll["tipo"] for ll in cliente_falso.llamadas]
    # Solo la síntesis usó el modelo. El router no.
    assert tipos.count("estructurado") == 1


def test_la_forma_estructurada_produce_un_informe_completo(api, cliente_falso):
    aid = api.post("/analyses", json={
        "product_ids": ["P002", "P003"], "desde": DESDE, "hasta": HASTA,
    }).json()["id"]

    cuerpo = api.get(f"/analyses/{aid}").json()
    assert cuerpo["estado"] == "completado"
    assert len(cuerpo["informe"]["metricas"]) == 2


# --- Forma en lenguaje natural -----------------------------------------------

def test_acepta_una_consulta_en_lenguaje_natural(api, cliente_falso):
    r = api.post("/analyses", json={
        "consulta": "Compará P002 y P003 en los últimos 90 días",
    })
    assert r.status_code == 202

    aid = r.json()["id"]
    cuerpo = api.get(f"/analyses/{aid}").json()
    assert cuerpo["estado"] == "completado"
    assert cuerpo["intencion"] == "product_performance"
    assert cuerpo["product_ids"] == ["P002", "P003"]


def test_la_consulta_en_lenguaje_natural_si_invoca_al_router(api, cliente_falso):
    api.post("/analyses", json={
        "consulta": "Compará P002 y P003 en los últimos 90 días",
    })
    assert len(cliente_falso.llamadas) == 2  # router + síntesis


def test_una_consulta_fuera_de_alcance_no_produce_informe(api):
    doble = ClienteFalso([_router(intencion="fuera_de_alcance", dias=0)])
    app.dependency_overrides[obtener_cliente_llm] = lambda: doble
    try:
        aid = api.post("/analyses", json={"consulta": "Contame un chiste"}).json()["id"]
        cuerpo = api.get(f"/analyses/{aid}").json()
        assert cuerpo["informe"] is None
        assert cuerpo["intencion"] == "fuera_de_alcance"
        assert cuerpo["advertencias"]
    finally:
        app.dependency_overrides.clear()


# --- Validación de la solicitud ----------------------------------------------

def test_una_solicitud_vacia_es_rechazada(api):
    """Sin consulta ni identificadores no hay nada que analizar."""
    assert api.post("/analyses", json={}).status_code == 422


def test_no_se_pueden_mezclar_las_dos_formas(api):
    """Mandar las dos deja ambiguo qué manda. Rechazar es más honesto que
    elegir una en silencio y que el usuario descubra después cuál se usó."""
    r = api.post("/analyses", json={
        "consulta": "Compará P002 y P003",
        "product_ids": ["P010"], "desde": DESDE, "hasta": HASTA,
    })
    assert r.status_code == 422


def test_product_ids_sin_fechas_es_rechazado(api):
    assert api.post("/analyses", json={
        "product_ids": ["P002"]
    }).status_code == 422


# --- Degradación -------------------------------------------------------------

def test_con_el_modelo_caido_la_forma_estructurada_sigue_funcionando(api):
    """La degradación que justifica todo el diseño.

    Si Ollama no responde, la forma estructurada no necesita el router y el
    sintetizador cae en su respaldo determinístico. El usuario recibe un informe
    con números verificados, más seco pero correcto. Un 500 no le sirve a nadie.
    """
    # Se envuelve en una lambda a propósito: pasar la clase directamente hace
    # que FastAPI intente derivar un modelo de respuesta de su `__init__`, y
    # `Exception | None` no es un tipo válido de Pydantic.
    app.dependency_overrides[obtener_cliente_llm] = lambda: ClienteQueFalla()
    try:
        aid = api.post("/analyses", json={
            "product_ids": ["P002", "P003"], "desde": DESDE, "hasta": HASTA,
        }).json()["id"]
        cuerpo = api.get(f"/analyses/{aid}").json()

        assert cuerpo["estado"] == "completado"
        assert len(cuerpo["informe"]["resumen_ejecutivo"]) > 0
        assert "respaldo" in cuerpo["informe"]["modelo_llm"].lower()
    finally:
        app.dependency_overrides.clear()


# --- El recurso expone el trabajo del agente ---------------------------------

def test_el_analisis_expone_el_trace_del_grafo(api, cliente_falso):
    """"Cómo se obtuvo" del blueprint: qué etapa corrió y cuánto tardó."""
    aid = api.post("/analyses", json={
        "product_ids": ["P002"], "desde": DESDE, "hasta": HASTA,
    }).json()["id"]

    cuerpo = api.get(f"/analyses/{aid}").json()
    nodos = [p["nodo"] for p in cuerpo["informe"]["trace"]]
    assert "sql_tool" in nodos
    assert "synthesizer" in nodos


def test_el_pdf_del_analisis_del_agente_se_descarga(api, cliente_falso):
    aid = api.post("/analyses", json={
        "product_ids": ["P002"], "desde": DESDE, "hasta": HASTA,
    }).json()["id"]

    r = api.get(f"/analyses/{aid}.pdf")
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")
