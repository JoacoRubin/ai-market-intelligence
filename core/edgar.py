"""Cliente de SEC EDGAR: datos financieros oficiales de empresas que cotizan
en EE. UU. — gratis, sin API key, sin límite diario (10 req/seg por IP).

Vive en `core` y no en `agent/`, por el mismo motivo que `core/kpis.py`: es
I/O externo (acá HTTP en vez de SQL), y la *tool* (`agent/tools/
research_company.py`) es el contrato con el grafo, no el "cómo". El planner
y el router no importan este módulo — solo la tool.

Dos pasos, cada uno con su propia superficie de fallo:

1. **Resolver un nombre a CIK** (`resolver_empresa`): "Apple" → CIK 320193.
   Determinístico, contra el listado oficial de tickers de la SEC. Un empate
   ambiguo se trata como "no encontrada", nunca se adivina — ver
   `docs/adr/ADR-004-sin-text-to-sql.md`, mismo principio de lista blanca
   aplicado acá a nombres en vez de a SQL.
2. **Traer los hechos financieros** (`hechos_clave`): el CIK ya resuelto
   arma la URL, nunca un nombre libre — el LLM elige QUÉ empresa, nunca CÓMO
   se consulta.

Sin retry ni backoff, misma disciplina que `core/db.py`/`agent/llm.py`: un
timeout corto y un fallo se vuelve advertencia no-fatal, no una excepción
que tumbe el grafo. El proyecto no tiene `tenacity` como dependencia
directa, y un límite de 10 req/seg sin techo diario no lo justifica para 2
empresas por consulta.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any, cast

import httpx
from pydantic import BaseModel

# Dos hosts, no uno — verificado en vivo, no asumido de memoria: el listado
# de tickers vive en www.sec.gov (data.sec.gov/files/... devuelve un 404 de
# S3, "NoSuchKey" — es el bucket equivocado); los hechos XBRL de una empresa
# puntual viven en data.sec.gov/api/xbrl/... Confundir los dos hace que
# "SEC EDGAR no responde" sea el síntoma de un bug propio, no de la red.
BASE_WWW = "https://www.sec.gov"
BASE_DATOS = "https://data.sec.gov"
TIMEOUT_HECHOS = 10
TIMEOUT_SALUD = 5

# La SEC exige un User-Agent con contacto real o bloquea la IP — no es
# opcional, a diferencia de casi cualquier otra API pública. El default sirve
# para desarrollo; en producción se espera `SEC_EDGAR_USER_AGENT` propio.
HEADERS = {
    "User-Agent": os.getenv(
        "SEC_EDGAR_USER_AGENT",
        "ai-market-intelligence contacto@ejemplo.com",
    )
}

# Sufijos societarios que se pelan del nombre antes de comparar — "Tesla,
# Inc." y "Tesla" tienen que normalizar igual. Se pelan en LOOP, no en una
# pasada: "Tesla, Inc." tiene coma+sufijo, sacar uno solo deja el otro
# pegado (mismo error de "una pasada no alcanza" que ya documentó
# core/numeros.py sobre las referencias).
SUFIJOS_SOCIETARIOS = (
    "incorporated", "inc", "corporation", "corp", "company", "co",
    "limited", "ltd", "plc", "llc",
    # No es un sufijo societario en sentido estricto, pero cumple el mismo
    # rol acá: "Amazon.com, Inc." es el título oficial de la SEC para
    # Amazon, y nadie dice "Amazon.com" al preguntar — dice "Amazon". Sin
    # pelarlo, "Amazon" normaliza a "amazon" y el título oficial a "amazon
    # com", y el match por igualdad falla sobre el caso real más obvio del
    # golden set (`hold-04`).
    "com",
)

# Desde ASC 606 (~2018), muchos filers de EE. UU. —Apple incluida— no
# taguean `Revenues` sino `RevenueFromContractWithCustomerExcludingAssessedTax`.
# NO alcanza con quedarse con el primer tag que tenga ALGÚN dato: una
# empresa que migró de tag sigue teniendo años viejos bajo el abandonado.
# `hechos_clave` compara el candidato de cada uno y se queda con el más
# reciente de todos — verificado en una corrida real ("Apple y Tesla") que
# sin esto mezclaba un revenue de 2018 con una ganancia neta de 2025 en el
# mismo informe.
CONCEPTOS_REVENUE = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
)
CONCEPTO_GANANCIA = "NetIncomeLoss"
CONCEPTO_ACTIVOS = "Assets"


class EmpresaResuelta(BaseModel):
    """Una empresa identificada de forma inequívoca contra el listado de la SEC."""

    nombre: str
    ticker: str
    cik: int


class HechoFinanciero(BaseModel):
    """Un valor XBRL puntual — el equivalente de `MetricaProducto` para una
    empresa externa, con la salvedad de que el número es oficial y externo,
    no calculado por SQL propio."""

    empresa: str
    ticker: str
    cik: int
    concepto: str          # etiqueta legible: "revenue", "ganancia neta", "activos totales"
    concepto_xbrl: str      # el tag XBRL real usado, para trazabilidad
    valor: float            # unidad nativa del filing (USD enteros)
    fiscal_year: int
    fiscal_period: str
    filed: str               # fecha ISO del filing — NUNCA meterla en prosa con
                             # números: numeros.py no matchea guiones y la parte
                             # en tres tokens sueltos.
    form: str
    accn: str


def en_millones(hecho: HechoFinanciero) -> float:
    """Escala el valor crudo de XBRL (USD enteros) a millones — la unidad en
    la que se redacta la plantilla de texto en `core/conclusiones.py`.

    Se usa DESDE LOS DOS LADOS (acá se define, `conclusiones.py` la usa para
    redactar, `validator.py` la usa para poblar `disponibles`): calcular el
    número dos veces en dos lugares es exactamente cómo terminan divergiendo.
    Si el texto dice "USD 383.285 millones" pero el validador compara contra
    383285000000.0 sin escalar, no matchean ni con tolerancia — son 6 órdenes
    de magnitud distintos.
    """
    return hecho.valor / 1_000_000


def _normalizar(nombre: str) -> str:
    """Compara nombres de empresa ignorando puntuación, mayúsculas y sufijos
    societarios."""
    palabras = re.sub(r"[^\w\s]", " ", nombre.lower()).split()
    while palabras and palabras[-1] in SUFIJOS_SOCIETARIOS:
        palabras.pop()
    return " ".join(palabras)


@lru_cache(maxsize=1)
def _tickers_crudos() -> dict[str, Any]:
    """~1MB, estático por vida del proceso — mismo criterio que
    `driver_disponible()`/`catalogo_tools()`. Una excepción acá NO se
    cachea (comportamiento normal de `lru_cache`): un fallo de red no deja
    el proceso envenenado, el próximo llamado reintenta solo."""
    r = httpx.get(f"{BASE_WWW}/files/company_tickers.json", headers=HEADERS, timeout=TIMEOUT_HECHOS)
    r.raise_for_status()
    return cast(dict[str, Any], r.json())


@lru_cache(maxsize=1)
def _por_ticker() -> dict[str, EmpresaResuelta]:
    resultado: dict[str, EmpresaResuelta] = {}
    for entrada in _tickers_crudos().values():
        resuelta = EmpresaResuelta(
            nombre=entrada["title"], ticker=entrada["ticker"], cik=int(entrada["cik_str"]),
        )
        resultado[resuelta.ticker.upper()] = resuelta
    return resultado


@lru_cache(maxsize=1)
def _por_nombre() -> dict[str, EmpresaResuelta]:
    resultado: dict[str, EmpresaResuelta] = {}
    for entrada in _tickers_crudos().values():
        resuelta = EmpresaResuelta(
            nombre=entrada["title"], ticker=entrada["ticker"], cik=int(entrada["cik_str"]),
        )
        clave = _normalizar(resuelta.nombre)
        # Si dos empresas normalizan igual (colisión real, aunque rara), la
        # primera del JSON de la SEC gana — no se sobreescribe en silencio.
        resultado.setdefault(clave, resuelta)
    return resultado


def resolver_empresa(nombre: str) -> EmpresaResuelta | None:
    """Nombre en lenguaje natural o ticker → empresa inequívoca, o `None`.

    Un usuario escribe tanto "Amazon" como "AMZN" — los dos son reales (el
    caso probado en esta sesión fue literalmente "Comparame MELI y AMZN").
    El listado de la SEC solo tiene razones sociales en el título, nunca el
    ticker, así que "MELI" no matchearía nunca por nombre. Se prueba
    ticker PRIMERO, y solo para tokens que tienen forma de ticker (mayúsculas,
    1 a 5 caracteres) — un nombre de empresa normal nunca cae en esa forma,
    así que no hay riesgo de que "Apple" se lea como ticker por accidente.

    Match por IGUALDAD tras normalizar, no por substring: "Apple" matchearía
    por substring tanto "Apple Inc." como "Apple Hospitality REIT, Inc." —
    una REIT real, no relacionada. Sin igualdad exacta, no se adivina.
    """
    candidato = nombre.strip()
    if not candidato:
        return None
    if candidato.isupper() and 1 <= len(candidato) <= 5:
        por_ticker = _por_ticker().get(candidato)
        if por_ticker:
            return por_ticker
    return _por_nombre().get(_normalizar(candidato))


def _valor_anual_mas_reciente(hechos_concepto: dict[str, Any]) -> dict[str, Any] | None:
    """Del concepto XBRL, el dato anual (10-K, fiscal_period FY) más reciente.

    `end` está presente tanto en conceptos de duración (Revenues, un rango
    start→end) como de instante (Assets, una foto al cierre) — ordenar por
    ahí sirve para los dos sin distinguir el tipo de concepto.
    """
    candidatos = [
        item for item in hechos_concepto.get("units", {}).get("USD", [])
        if item.get("fp") == "FY" and item.get("form") == "10-K" and item.get("end")
    ]
    if not candidatos:
        return None
    return cast("dict[str, Any]", max(candidatos, key=lambda i: i["end"]))


def _a_hecho(
    empresa: EmpresaResuelta, concepto: str, concepto_xbrl: str, item: dict[str, Any]
) -> HechoFinanciero:
    return HechoFinanciero(
        empresa=empresa.nombre, ticker=empresa.ticker, cik=empresa.cik,
        concepto=concepto, concepto_xbrl=concepto_xbrl,
        valor=float(item["val"]), fiscal_year=int(item.get("fy") or 0),
        fiscal_period=str(item.get("fp", "FY")), filed=str(item.get("filed", "")),
        form=str(item.get("form", "10-K")), accn=str(item.get("accn", "")),
    )


def hechos_clave(empresa: EmpresaResuelta) -> list[HechoFinanciero]:
    """Revenue (con fallback de tag), ganancia neta y activos totales del
    10-K más reciente. Un concepto ausente no aborta los demás — mismo
    espíritu que `faltantes` en `product_metrics.py`."""
    r = httpx.get(
        f"{BASE_DATOS}/api/xbrl/companyfacts/CIK{empresa.cik:010d}.json",
        headers=HEADERS, timeout=TIMEOUT_HECHOS,
    )
    r.raise_for_status()
    us_gaap = r.json().get("facts", {}).get("us-gaap", {})

    hechos: list[HechoFinanciero] = []

    # NO alcanza con "el primer tag que tenga algún dato": una empresa que
    # migró de tag (Apple dejó de reportar `Revenues` en 2018 al adoptar
    # ASC 606) sigue teniendo AÑOS de historial viejo bajo el tag
    # abandonado. Tomar el primero con datos daba un revenue de 2018 al
    # lado de una ganancia neta de 2025 — mismo informe, dos años fiscales
    # distintos, verificado en una corrida real contra "Apple y Tesla".
    # Hay que comparar el candidato de CADA tag y quedarse con el más
    # reciente de todos, no con el primero que aparece.
    mejor_revenue: dict[str, Any] | None = None
    concepto_revenue: str | None = None
    for concepto_xbrl in CONCEPTOS_REVENUE:
        item = _valor_anual_mas_reciente(us_gaap.get(concepto_xbrl, {}))
        if item and (mejor_revenue is None or item["end"] > mejor_revenue["end"]):
            mejor_revenue, concepto_revenue = item, concepto_xbrl
    if mejor_revenue and concepto_revenue:
        hechos.append(_a_hecho(empresa, "revenue", concepto_revenue, mejor_revenue))

    item = _valor_anual_mas_reciente(us_gaap.get(CONCEPTO_GANANCIA, {}))
    if item:
        hechos.append(_a_hecho(empresa, "ganancia neta", CONCEPTO_GANANCIA, item))

    item = _valor_anual_mas_reciente(us_gaap.get(CONCEPTO_ACTIVOS, {}))
    if item:
        hechos.append(_a_hecho(empresa, "activos totales", CONCEPTO_ACTIVOS, item))

    return hechos


def hay_edgar_disponible() -> bool:
    """Mismo patrón que `hay_base_disponible()`/`ollama_responde()`: intenta
    la operación real con timeout corto, nunca deja propagar la excepción."""
    try:
        httpx.get(
            f"{BASE_WWW}/files/company_tickers.json", headers=HEADERS, timeout=TIMEOUT_SALUD,
        ).raise_for_status()
        return True
    except Exception:
        return False
