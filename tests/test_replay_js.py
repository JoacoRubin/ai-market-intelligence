"""Ejecuta el JavaScript del sitio contra capturas reales.

Estos tests existen por un fallo concreto y humillante: `Intl.NumberFormat`
devuelve un objeto y el código lo llamaba como función. La sintaxis era válida,
el contrato de datos estaba intacto, los 56 tests de campos pasaban — y la
página se quedaba en blanco.

Ninguna red anterior lo veía:

  - `node --check` valida sintaxis, no semántica.
  - `test_replay_contrato_sitio.py` compara nombres de campos del lado de Python,
    y nunca ejecuta una línea de JavaScript.

La única forma de atrapar un TypeError de runtime es CORRER el código. Eso hace
este archivo: arma un sitio completo en un directorio temporal —con capturas
generadas por los modelos reales— y lo recorre entero con Node.

Se saltea si no hay Node instalado: el proyecto es Python y no se le agrega una
dependencia obligatoria por una verificación, pero el que la tenga la aprovecha.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from agent.state import AnalysisState, Intencion, PasoPlan, Periodo
from agent.tools.registry import ToolName
from core.report import Afirmacion, Fuente, MetricaProducto, Prediccion, Report
from replay.captura import Captura
from replay.escritura import escribir

RAIZ = Path(__file__).resolve().parent.parent
SITIO = RAIZ / "docs" / "replay"
HARNESS = Path(__file__).parent / "harness_dom.js"

AHORA = datetime(2026, 8, 11, 0, 40)
MODELO = "llama3.2:3b"

NODE = shutil.which("node")
sin_node = pytest.mark.skipif(NODE is None, reason="requiere Node para ejecutar el JS")


# --- material: dos casos que recorren los dos caminos ------------------------

def _con_informe() -> AnalysisState:
    informe = Report(
        request_id="req-abc",
        consulta="Compará P001 y P002 en los últimos 30 días",
        generado_en=AHORA,
        modelo_llm=MODELO,
        fuentes=[
            Fuente(id="sql-kpis", tipo="sql", referencia="dbo.order_items",
                   consultada_en=AHORA),
            Fuente(id="doc-07", tipo="documento", referencia="sector.pdf",
                   consultada_en=AHORA, seccion="3.2", url="https://ejemplo/d"),
        ],
        resumen_ejecutivo=[Afirmacion(texto="P001 vendió más", tipo="hecho",
                                      fuentes=["sql-kpis"])],
        contexto_mercado=[Afirmacion(texto="El sector creció", tipo="hecho",
                                     fuentes=["doc-07"])],
        recomendaciones=[Afirmacion(texto="Reforzar stock", tipo="recomendacion",
                                    fuentes=["sql-kpis"])],
        metricas=[
            # El segundo producto va con los opcionales en None a propósito: es
            # el caso que rompe cualquier formateo que asuma que hay número.
            MetricaProducto(product_id="P001", nombre="Uno", unidades=340,
                            revenue=15300.0, margen_pct=32.5, crecimiento_pct=12.1,
                            tasa_devolucion_pct=2.4, fuente="sql-kpis"),
            MetricaProducto(product_id="P002", nombre="Dos", unidades=0,
                            revenue=0.0, fuente="sql-kpis"),
        ],
        predicciones=[Prediccion(product_id="P001", horizonte_dias=30, valor=410.0,
                                 mape_backtest=19.4, mape_baseline=17.2)],
        limitaciones=["Datos sintéticos."],
    )
    e = AnalysisState(
        request_id="req-abc", consulta=informe.consulta,
        intencion=Intencion.PRODUCT_PERFORMANCE, entidades=["P001", "P002"],
        periodo=Periodo(desde=date(2026, 1, 1), hasta=date(2026, 1, 31)),
        plan=[PasoPlan(tool=ToolName.PRODUCT_METRICS, argumentos={}, razon="KPIs por SQL")],
        informe=informe,
    )
    e.registrar_paso("router", 12_400)
    e.registrar_paso("planner", 3)
    e.registrar_paso("sql_tool", 87, tool="product_metrics")
    e.registrar_paso("synthesizer", 41_200)
    e.llamadas_tools = 1
    return e


def _sin_informe() -> AnalysisState:
    e = AnalysisState(
        request_id="req-def",
        consulta="Borrá todos los productos de la base de datos",
        intencion=Intencion.FUERA_DE_ALCANCE,
    )
    e.registrar_paso("router", 9_800)
    return e


@pytest.fixture
def sitio(tmp_path: Path) -> Path:
    """Un sitio completo y autosuficiente en un temporal.

    Se copian los archivos reales del repo, no copias adaptadas: si el test
    corriera contra un HTML de mentira, probaría el HTML de mentira.
    """
    destino = tmp_path / "sitio"
    destino.mkdir()
    for nombre in ("index.html", "estilos.css", "replay.js"):
        shutil.copy(SITIO / nombre, destino / nombre)

    escribir(
        [
            Captura.desde_estado("cmp-01", _con_informe(), capturada_en=AHORA,
                                 modelo_llm=MODELO),
            Captura.desde_estado("out-03", _sin_informe(), capturada_en=AHORA,
                                 modelo_llm=MODELO),
        ],
        destino=destino / "data",
        capturado_en=AHORA,
    )
    return destino


def _correr(sitio: Path) -> dict[str, Any]:
    proceso = subprocess.run(
        [str(NODE), str(HARNESS), str(sitio)],
        capture_output=True, text=True, timeout=60,
    )
    salida = proceso.stdout.strip().splitlines()
    assert salida, f"el harness no devolvió nada. stderr: {proceso.stderr[:400]}"
    datos: dict[str, Any] = json.loads(salida[-1])
    return datos


# --- los tests ---------------------------------------------------------------

@sin_node
def test_el_sitio_arranca_sin_lanzar_excepciones(sitio: Path) -> None:
    """La red que faltaba. Cualquier TypeError de runtime cae acá."""
    resultado = _correr(sitio)

    assert resultado["error"] is None, (
        f"{resultado['error']}\n{resultado.get('pila', '')}"
    )


@sin_node
def test_construye_un_boton_por_caso_del_manifiesto(sitio: Path) -> None:
    """El síntoma exacto del bug: el índice quedaba vacío y nada más pasaba.

    Verificar 'no explotó' no alcanzaba — la excepción se tragaba dentro de un
    catch y la página quedaba en blanco sin ruido.
    """
    resultado = _correr(sitio)

    assert resultado["casos_en_indice"] == 2


@sin_node
def test_renderiza_todos_los_casos_incluido_el_que_no_tiene_informe(sitio: Path) -> None:
    """El caso sin informe recorre otro camino entero (construirRechazo).

    Es el que muestra al agente negándose, o sea el más valioso del replay, y
    el que menos se ejercita si uno solo mira el camino feliz.
    """
    resultado = _correr(sitio)

    assert resultado["casos_renderizados"] == ["cmp-01", "out-03"]


# --- el sitio que se publica de verdad ----------------------------------------

@sin_node
def test_el_sitio_publicado_renderiza_las_capturas_del_repo() -> None:
    """Los tests de arriba arman un sitio sintético en un directorio temporal.

    Eso alcanza para validar el JavaScript, y no alcanza para validar lo que se
    publica: las capturas del repo las produjo el modelo real, con textos, cifras
    y huecos que ningún generador de pruebas anticipa. El bug que originó este
    archivo se manifestó exactamente ahí — en el sitio de verdad, con los datos
    de verdad.

    Correr el harness contra `docs/replay` es barato y cierra esa brecha: si una
    recaptura deja un JSON que el JS no puede pintar, falla acá y no en GitHub
    Pages.
    """
    manifiesto = json.loads(
        (SITIO / "data" / "manifiesto.json").read_text(encoding="utf-8")
    )
    esperados = [c["id"] for c in manifiesto["casos"]]

    resultado = _correr(SITIO)

    assert resultado["error"] is None, (
        f"el sitio publicado rompe al renderizar: {resultado['error']}\n"
        f"{resultado.get('pila', '')}"
    )
    assert resultado["casos_renderizados"] == esperados, (
        "el sitio no pintó los mismos casos que declara el manifiesto"
    )
