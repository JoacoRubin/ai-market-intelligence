"""Tests de la API REST.

El diseño sigue el nivel 2 del modelo de madurez de Richardson: recursos como
sustantivos, verbos HTTP con su semántica real y códigos de estado que
significan algo. Más negociación de contenido para servir el mismo análisis
como JSON o como PDF.

Estos tests verifican el CONTRATO, no la implementación. Si mañana el análisis
lo produce un grafo de LangGraph en vez de un cálculo SQL directo, estos tests
tienen que seguir pasando sin tocar una línea: eso es lo que prueba que el
contrato está bien puesto.

La decisión de mayor peso está en `POST /analyses`, que responde **202 Accepted**
y no 201. Hoy el análisis es puro SQL y termina en milisegundos, pero cuando
entre el LLM va a tardar cerca de dos minutos en esta máquina. Fijar el contrato
asíncrono ahora evita romper a todos los consumidores después.
"""

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from core.db import hay_base_disponible

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not hay_base_disponible(),
        reason="SQL Server no está levantado (.\\tasks.ps1 db-up)",
    ),
]

DESDE = "2026-01-01"
HASTA = "2026-03-31"


@pytest.fixture(scope="module")
def cliente():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def productos(cliente) -> list[str]:
    r = cliente.get("/products", params={"limite": 3})
    assert r.status_code == 200
    return [p["id"] for p in r.json()["items"]]


@pytest.fixture
def analisis(cliente, productos) -> str:
    """Crea un análisis y devuelve su id."""
    r = cliente.post("/analyses", json={
        "product_ids": productos[:2], "desde": DESDE, "hasta": HASTA,
    })
    assert r.status_code == 202
    return r.json()["id"]


# --- Salud -------------------------------------------------------------------

def test_health_reporta_el_estado_de_las_dependencias(cliente):
    r = cliente.get("/health")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["estado"] == "ok"
    assert "base_de_datos" in cuerpo


# --- Recurso: products -------------------------------------------------------

def test_listar_productos(cliente):
    r = cliente.get("/products")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["total"] > 0
    assert len(cuerpo["items"]) > 0
    assert {"id", "brand", "category", "price"} <= set(cuerpo["items"][0])


def test_obtener_un_producto(cliente, productos):
    r = cliente.get(f"/products/{productos[0]}")
    assert r.status_code == 200
    assert r.json()["id"] == productos[0]


def test_producto_inexistente_devuelve_404(cliente):
    r = cliente.get("/products/NO-EXISTE")
    assert r.status_code == 404


# --- Sub-recurso: métricas ---------------------------------------------------

def test_metricas_de_un_producto(cliente, productos):
    r = cliente.get(f"/products/{productos[0]}/metrics",
                    params={"desde": DESDE, "hasta": HASTA})
    assert r.status_code == 200
    m = r.json()
    assert m["product_id"] == productos[0]
    assert m["unidades"] >= 0
    assert m["fuente"].startswith("sql:")


def test_metricas_con_rango_invertido_devuelve_422(cliente, productos):
    """`desde` posterior a `hasta` no es un error del servidor: es una entrada
    inválida, y el cliente tiene que enterarse con un 422, no con un 500."""
    r = cliente.get(f"/products/{productos[0]}/metrics",
                    params={"desde": HASTA, "hasta": DESDE})
    assert r.status_code == 422


def test_metricas_de_producto_inexistente_devuelve_404(cliente):
    r = cliente.get("/products/NO-EXISTE/metrics",
                    params={"desde": DESDE, "hasta": HASTA})
    assert r.status_code == 404


# --- Recurso: analyses -------------------------------------------------------

def test_crear_analisis_devuelve_202_y_location(cliente, productos):
    """202 Accepted, no 201 Created.

    El recurso existe desde el instante cero, pero todavía no está terminado.
    Cuando el LLM entre en juego esto va a tardar cerca de dos minutos; el
    contrato ya lo contempla y no habrá que romper a los consumidores.
    """
    r = cliente.post("/analyses", json={
        "product_ids": productos[:2], "desde": DESDE, "hasta": HASTA,
    })
    assert r.status_code == 202
    assert "location" in {k.lower() for k in r.headers}
    assert r.headers["location"].endswith(r.json()["id"])
    assert r.json()["estado"] in ("pendiente", "procesando", "completado")


def test_consultar_un_analisis(cliente, analisis):
    r = cliente.get(f"/analyses/{analisis}")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["id"] == analisis
    assert cuerpo["estado"] == "completado"
    assert len(cuerpo["informe"]["metricas"]) == 2


def test_el_analisis_no_inventa_numeros(cliente, analisis, productos):
    """Los KPIs del análisis tienen que ser los mismos que devuelve el endpoint
    de métricas. Dos caminos hacia el mismo número no pueden discrepar."""
    a = cliente.get(f"/analyses/{analisis}").json()
    for metrica in a["informe"]["metricas"]:
        directo = cliente.get(
            f"/products/{metrica['product_id']}/metrics",
            params={"desde": DESDE, "hasta": HASTA},
        ).json()
        assert metrica["unidades"] == directo["unidades"]
        assert metrica["revenue"] == pytest.approx(directo["revenue"], abs=0.01)


def test_listar_analisis(cliente, analisis):
    r = cliente.get("/analyses")
    assert r.status_code == 200
    assert any(a["id"] == analisis for a in r.json()["items"])


def test_analisis_inexistente_devuelve_404(cliente):
    r = cliente.get("/analyses/no-existe")
    assert r.status_code == 404


def test_crear_analisis_con_producto_inexistente_devuelve_422(cliente):
    r = cliente.post("/analyses", json={
        "product_ids": ["NO-EXISTE"], "desde": DESDE, "hasta": HASTA,
    })
    assert r.status_code == 422


def test_crear_analisis_con_rango_invertido_devuelve_422(cliente, productos):
    r = cliente.post("/analyses", json={
        "product_ids": productos[:1], "desde": HASTA, "hasta": DESDE,
    })
    assert r.status_code == 422


def test_eliminar_un_analisis(cliente, analisis):
    assert cliente.delete(f"/analyses/{analisis}").status_code == 204
    assert cliente.get(f"/analyses/{analisis}").status_code == 404


# --- Negociación de contenido ------------------------------------------------

def test_el_mismo_recurso_se_sirve_como_pdf(cliente, analisis):
    """Un recurso, una URL, varias representaciones.

    El PDF no es otro recurso: es otra forma de mirar el mismo análisis. Si
    fuera un recurso aparte, tendría su propio ciclo de vida y podría quedar
    desincronizado del JSON.
    """
    r = cliente.get(f"/analyses/{analisis}", headers={"Accept": "application/pdf"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content.startswith(b"%PDF-")


def test_la_extension_pdf_funciona_sin_headers(cliente, analisis):
    """Un enlace de navegador no puede mandar el header Accept.

    Por eso la extensión en la URL convive con la negociación de contenido: no
    es redundancia, es reconocer cómo funcionan los clientes reales.
    """
    r = cliente.get(f"/analyses/{analisis}.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert "attachment" in r.headers.get("content-disposition", "")


def test_pdf_de_analisis_inexistente_devuelve_404(cliente):
    r = cliente.get("/analyses/no-existe.pdf")
    assert r.status_code == 404


def test_formato_no_soportado_devuelve_406(cliente, analisis):
    """406 Not Acceptable: el recurso existe, pero no en el formato pedido.
    Devolver JSON igual sería mentirle al cliente sobre lo que recibió."""
    r = cliente.get(f"/analyses/{analisis}",
                    headers={"Accept": "application/xml"})
    assert r.status_code == 406


# --- Contrato documentado ----------------------------------------------------

def test_la_api_publica_su_esquema_openapi(cliente):
    """La documentación no se escribe aparte: se deriva de los modelos Pydantic.
    Una API cuyo contrato hay que mantener a mano se desincroniza siempre."""
    r = cliente.get("/openapi.json")
    assert r.status_code == 200
    rutas = r.json()["paths"]
    assert "/analyses" in rutas
    assert "post" in rutas["/analyses"]
    assert "202" in rutas["/analyses"]["post"]["responses"]
