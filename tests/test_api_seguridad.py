"""Los dos controles de admisión de la API: credencial y capacidad.

Ninguno de estos tests necesita SQL Server. Es a propósito: son controles que
se aplican ANTES de tocar la base, y poder verificarlos sin infraestructura es
lo que hace que corran en cada commit y no solo en el job de guardrails.

Las dos decisiones de diseño que estos tests fijan:

**La credencial es opcional.** Sin `API_KEY` definida la API se comporta
exactamente como siempre. Eso no es una concesión: el modo local ya está
protegido por el bind a `127.0.0.1` del compose, y exigir una clave ahí solo
rompería `tasks.ps1`, la suite y el dashboard sin cerrar nada que estuviera
abierto. El control existe para el día que la API salga de loopback.

**Los health quedan siempre afuera.** El `HEALTHCHECK` del Dockerfile le pega a
`/health` sin credencial. Si la clave los alcanzara, el container quedaría
`unhealthy` para siempre y `docker-up` nunca daría verde — un control de
seguridad que rompe el despliegue se termina desactivando, que es la peor
forma de no tenerlo.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import apps.api.main as api
from apps.api.main import app

CLAVE = "clave-de-prueba-no-usar-en-ningun-lado"

# `GET /analyses` es el endpoint elegido para probar la credencial: está
# protegido, lee del almacén y NO toca la base, así que aísla el control de
# acceso de cualquier otra dependencia.
RUTA_PROTEGIDA = "/analyses"


@pytest.fixture
def cliente() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


# --- credencial: modo local (sin API_KEY) ------------------------------------

def test_sin_api_key_configurada_la_api_responde_como_siempre(
    cliente: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El contrato que hace que este cambio no rompa nada existente."""
    monkeypatch.delenv("API_KEY", raising=False)

    assert cliente.get(RUTA_PROTEGIDA).status_code == 200


def test_sin_api_key_un_header_de_mas_no_molesta(
    cliente: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un cliente que manda la clave contra una API que no la pide sigue andando."""
    monkeypatch.delenv("API_KEY", raising=False)

    r = cliente.get(RUTA_PROTEGIDA, headers={"X-API-Key": "cualquier-cosa"})

    assert r.status_code == 200


def test_una_api_key_en_blanco_cuenta_como_no_configurada(
    cliente: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`API_KEY=` en el `.env` es la forma natural de dejarla apagada.

    Sin el `.strip()`, un valor con espacios sería un secreto válido de verdad y
    la API quedaría exigiendo una clave que nadie sabe que existe.
    """
    monkeypatch.setenv("API_KEY", "   ")

    assert cliente.get(RUTA_PROTEGIDA).status_code == 200


# --- credencial: modo protegido ----------------------------------------------

def test_con_api_key_configurada_y_sin_header_rechaza(
    cliente: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_KEY", CLAVE)

    r = cliente.get(RUTA_PROTEGIDA)

    assert r.status_code == 401
    assert "X-API-Key" in r.json()["detail"]


def test_con_api_key_configurada_y_clave_incorrecta_rechaza(
    cliente: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_KEY", CLAVE)

    r = cliente.get(RUTA_PROTEGIDA, headers={"X-API-Key": "otra-cosa"})

    assert r.status_code == 401


def test_una_clave_que_es_prefijo_de_la_correcta_tambien_se_rechaza(
    cliente: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Comparación completa, no por prefijo."""
    monkeypatch.setenv("API_KEY", CLAVE)

    r = cliente.get(RUTA_PROTEGIDA, headers={"X-API-Key": CLAVE[:-1]})

    assert r.status_code == 401


def test_con_la_clave_correcta_deja_pasar(
    cliente: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_KEY", CLAVE)

    r = cliente.get(RUTA_PROTEGIDA, headers={"X-API-Key": CLAVE})

    assert r.status_code == 200


@pytest.mark.parametrize("ruta", ["/health", "/health/live", "/health/ready"])
def test_los_health_nunca_piden_credencial(
    cliente: TestClient, monkeypatch: pytest.MonkeyPatch, ruta: str
) -> None:
    """La excepción deliberada, y la razón por la que existe.

    Sin esto el HEALTHCHECK del Dockerfile —que no manda header— dejaría el
    container `unhealthy` para siempre.

    Se acepta 200 o 503: `/health/ready` responde 503 cuando la base no está, y
    eso es una respuesta legítima del endpoint. Lo que este test afirma es que
    NUNCA es 401.
    """
    monkeypatch.setenv("API_KEY", CLAVE)

    assert cliente.get(ruta).status_code in (200, 503)


def test_todas_las_rutas_de_negocio_estan_protegidas() -> None:
    """Guardián: una ruta nueva sin `dependencies=PROTEGIDO` rompe este test.

    Es el control que importa a largo plazo. Proteger las ocho rutas de hoy es
    fácil; lo difícil es que la novena, agregada dentro de seis meses, no quede
    abierta por olvido. Acá se afirma la REGLA —todo lo que no es health pide
    credencial— en vez de enumerar los casos conocidos.
    """
    sin_proteger = []
    for ruta in app.routes:
        path = getattr(ruta, "path", "")
        if not path.startswith(("/products", "/analyses")):
            continue
        dependencias = getattr(getattr(ruta, "dependant", None), "dependencies", [])
        if not any(
            getattr(d, "call", None) is api.requiere_clave for d in dependencias
        ):
            sin_proteger.append(f"{getattr(ruta, 'methods', '?')} {path}")

    assert sin_proteger == [], (
        f"rutas sin credencial: {sin_proteger}. Agregales dependencies=PROTEGIDO."
    )


# --- capacidad ----------------------------------------------------------------

def test_sin_capacidad_rechaza_con_429(
    cliente: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """429 y no 503: el servicio está sano, lo que falta es capacidad ahora.

    Un 503 le diría al cliente que el sistema está caído. Un 429 le dice la
    verdad —esperá y reintentá—, que además es lo que un cliente bien escrito
    sabe manejar solo.
    """
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(api, "hay_capacidad", lambda _almacen: False)

    r = cliente.post("/analyses", json={"consulta": "Comparar P001 y P002"})

    assert r.status_code == 429


def test_el_rechazo_por_capacidad_ocurre_antes_de_crear_el_recurso(
    cliente: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un análisis rechazado no deja rastro.

    Si el 429 se devolviera después de `almacen.guardar`, cada rechazo sumaría
    un análisis PENDIENTE que nadie va a atender — y esos pendientes cuentan
    para el propio límite. El control se realimentaría a sí mismo y la API
    quedaría rechazando para siempre.
    """
    monkeypatch.delenv("API_KEY", raising=False)
    total_antes = cliente.get("/analyses").json()["total"]
    monkeypatch.setattr(api, "hay_capacidad", lambda _almacen: False)

    assert cliente.post(
        "/analyses", json={"consulta": "Comparar P001 y P002"}
    ).status_code == 429

    monkeypatch.setattr(api, "hay_capacidad", lambda _almacen: True)
    assert cliente.get("/analyses").json()["total"] == total_antes
