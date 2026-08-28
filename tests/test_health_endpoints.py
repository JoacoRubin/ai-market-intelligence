"""Liveness no toca dependencias; readiness sí expresa disponibilidad."""

import pytest
from fastapi.testclient import TestClient

from apps.api import main


def test_liveness_no_consulta_la_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main,
        "hay_base_disponible",
        lambda: (_ for _ in ()).throw(AssertionError("no debe consultar SQL")),
    )

    with TestClient(main.app) as cliente:
        respuesta = cliente.get("/health/live")

    assert respuesta.status_code == 200
    assert respuesta.json()["estado"] == "ok"


def test_readiness_devuelve_503_si_la_base_no_esta_disponible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "hay_base_disponible", lambda: False)

    with TestClient(main.app) as cliente:
        respuesta = cliente.get("/health/ready")

    assert respuesta.status_code == 503
    assert respuesta.json()["estado"] == "degradado"


def test_health_legacy_conserva_su_contrato_200_degradado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "hay_base_disponible", lambda: False)

    with TestClient(main.app) as cliente:
        respuesta = cliente.get("/health")

    assert respuesta.status_code == 200
    assert respuesta.json()["estado"] == "degradado"
