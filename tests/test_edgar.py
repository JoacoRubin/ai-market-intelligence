"""Tests de `core/edgar.py`: resolución nombre→empresa y el fallback de
conceptos XBRL de revenue — todo contra `httpx.get` mockeado, mismo patrón
que `tests/test_agent_llm.py` usa para `httpx.post` sobre `ClienteOllama`.
Sin marker `edgar`: con la red mockeada corren en milisegundos, no hace
falta saltearlos por defecto.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from core import edgar


class _RespuestaFalsa:
    """Doble mínimo de `httpx.Response`: solo lo que el cliente usa."""

    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=httpx.Request("GET", "http://x"),
                response=httpx.Response(self.status_code),
            )

    def json(self) -> dict[str, Any]:
        return self._payload


def _limpiar_cache() -> None:
    """`lru_cache` no debe filtrar estado entre tests."""
    edgar._tickers_crudos.cache_clear()
    edgar._por_ticker.cache_clear()
    edgar._por_nombre.cache_clear()


@pytest.fixture(autouse=True)
def _sin_cache() -> None:
    _limpiar_cache()
    yield
    _limpiar_cache()


TICKERS_GOLDEN_SET = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corporation"},
    "2": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."},
    "3": {"cik_str": 1018724, "ticker": "AMZN", "title": "Amazon.com, Inc."},
}


# --- resolución nombre→empresa -----------------------------------------------

@pytest.mark.parametrize(
    ("consulta", "ticker_esperado"),
    [
        ("Apple", "AAPL"),          # emp-01 del golden set
        ("Microsoft", "MSFT"),      # emp-01
        ("Tesla", "TSLA"),          # emp-02
        ("Amazon", "AMZN"),         # hold-04
    ],
)
def test_resolver_empresa_matchea_los_casos_reales_del_golden_set(
    monkeypatch: pytest.MonkeyPatch, consulta: str, ticker_esperado: str,
) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _RespuestaFalsa(TICKERS_GOLDEN_SET))
    resuelta = edgar.resolver_empresa(consulta)
    assert resuelta is not None
    assert resuelta.ticker == ticker_esperado


def test_resolver_empresa_pela_sufijos_societarios_en_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'Tesla, Inc.' tiene coma+sufijo — pelar en una sola pasada no alcanza."""
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _RespuestaFalsa(TICKERS_GOLDEN_SET))
    assert edgar.resolver_empresa("Tesla, Inc.") is not None
    assert edgar.resolver_empresa("Tesla, Inc.").ticker == "TSLA"


def test_resolver_empresa_por_ticker_directo(monkeypatch: pytest.MonkeyPatch) -> None:
    """El usuario real de esta sesión escribió 'Comparame MELI y AMZN' — un
    ticker no matchea por nombre (el título de la SEC nunca es un ticker),
    así que hace falta el camino directo."""
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _RespuestaFalsa(TICKERS_GOLDEN_SET))
    resuelta = edgar.resolver_empresa("AMZN")
    assert resuelta is not None
    assert resuelta.cik == 1018724


def test_resolver_empresa_no_encontrada_devuelve_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _RespuestaFalsa(TICKERS_GOLDEN_SET))
    assert edgar.resolver_empresa("Empresa Que No Existe") is None


def test_resolver_empresa_nombre_en_minuscula_no_se_confunde_con_ticker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El guardrail de 'parece ticker' exige mayúsculas Y longitud 1-5 — un
    nombre común no debería colarse por ese camino ni por casualidad."""
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _RespuestaFalsa(TICKERS_GOLDEN_SET))
    assert edgar.resolver_empresa("apple") is not None  # cae al camino de nombre
    assert edgar.resolver_empresa("apple").ticker == "AAPL"


def test_resolver_empresa_no_adivina_entre_candidatos_similares(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'Apple' matchea por SUBSTRING tanto 'Apple Inc.' como 'Apple
    Hospitality REIT, Inc.' — una REIT real, no relacionada. El match es
    por IGUALDAD tras normalizar, así que el nombre completo y distinto no
    debe resolver a la empresa incorrecta."""
    payload = {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 1616000, "ticker": "APLE", "title": "Apple Hospitality REIT, Inc."},
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _RespuestaFalsa(payload))
    # "Apple" normaliza exacto a "apple" (matchea Apple Inc., el sufijo "Inc."
    # se pela) — nunca a la REIT, cuyo nombre normalizado es "apple
    # hospitality reit", una cadena distinta.
    resuelta = edgar.resolver_empresa("Apple")
    assert resuelta is not None
    assert resuelta.ticker == "AAPL"


# --- hechos financieros: fallback de conceptos de revenue --------------------

def _companyfacts(us_gaap: dict[str, Any]) -> dict[str, Any]:
    return {"facts": {"us-gaap": us_gaap}}


def _item_anual(valor: float, fy: int = 2025, form: str = "10-K") -> dict[str, Any]:
    return {
        "val": valor, "fy": fy, "fp": "FY", "form": form,
        "end": f"{fy}-12-31", "filed": f"{fy + 1}-01-15", "accn": "0000320193-25-000001",
    }


def test_hechos_clave_usa_el_primer_concepto_de_revenue_presente(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apple no taguea 'Revenues' desde ASC 606 — el fallback tiene que
    encontrar 'RevenueFromContractWithCustomerExcludingAssessedTax'."""
    payload = _companyfacts({
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "units": {"USD": [_item_anual(383_285_000_000)]}
        },
    })
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _RespuestaFalsa(payload))
    empresa = edgar.EmpresaResuelta(nombre="Apple Inc.", ticker="AAPL", cik=320193)

    hechos = edgar.hechos_clave(empresa)

    revenue = next(h for h in hechos if h.concepto == "revenue")
    assert revenue.concepto_xbrl == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert edgar.en_millones(revenue) == pytest.approx(383_285.0)


def test_hechos_clave_prueba_conceptos_en_orden_y_para_en_el_primero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _companyfacts({
        "Revenues": {"units": {"USD": [_item_anual(100)]}},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "units": {"USD": [_item_anual(999)]}
        },
    })
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _RespuestaFalsa(payload))
    empresa = edgar.EmpresaResuelta(nombre="X", ticker="X", cik=1)

    hechos = edgar.hechos_clave(empresa)

    revenue = next(h for h in hechos if h.concepto == "revenue")
    assert revenue.concepto_xbrl == "Revenues"  # el primero de la lista, no el segundo


def test_hechos_clave_ignora_filings_no_anuales(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un 10-Q (trimestral) no cuenta como el dato anual — solo 10-K/FY."""
    payload = _companyfacts({
        "Assets": {"units": {"USD": [
            {**_item_anual(500), "form": "10-Q", "fp": "Q3", "end": "2025-09-30"},
            _item_anual(1000),
        ]}},
    })
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _RespuestaFalsa(payload))
    empresa = edgar.EmpresaResuelta(nombre="X", ticker="X", cik=1)

    hechos = edgar.hechos_clave(empresa)

    activos = next(h for h in hechos if h.concepto == "activos totales")
    assert activos.valor == 1000


def test_hechos_clave_concepto_ausente_no_rompe_los_demas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin ganancia neta reportada bajo ese tag, revenue y activos igual
    salen — mismo espíritu no-fatal que `faltantes` en product_metrics.py."""
    payload = _companyfacts({
        "Revenues": {"units": {"USD": [_item_anual(100)]}},
    })
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _RespuestaFalsa(payload))
    empresa = edgar.EmpresaResuelta(nombre="X", ticker="X", cik=1)

    hechos = edgar.hechos_clave(empresa)

    assert {h.concepto for h in hechos} == {"revenue"}


def test_hay_edgar_disponible_no_propaga_excepciones(monkeypatch: pytest.MonkeyPatch) -> None:
    def _falla(*a: Any, **k: Any) -> Any:
        raise httpx.ConnectError("sin red")
    monkeypatch.setattr(httpx, "get", _falla)
    assert edgar.hay_edgar_disponible() is False
