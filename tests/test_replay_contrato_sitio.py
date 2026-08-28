"""Contrato entre los modelos de Python y el JavaScript del sitio.

Este es el único punto del proyecto donde la trazabilidad se puede romper sin
que nada falle: el sitio lee el JSON por nombre de campo, y si un campo se
renombra en Pydantic, Python sigue feliz y el navegador muestra un hueco. El
error aparece en la pantalla de otra persona, no en la corrida que lo causó.

Estos tests cierran ese agujero desde tres lados:

1. Todo campo que `replay.js` lee existe en el JSON que producen los modelos.
2. Los nombres de etapa que el sitio pinta como "usa el modelo" son nodos reales
   del grafo.
3. Todo comando que el sitio le sugiere al visitante existe en `tasks.ps1`.

El tercero parece trivial y no lo es: un mensaje de error que recomienda un
comando inexistente es peor que no decir nada.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from agent.state import AnalysisState, Intencion, PasoPlan, Periodo
from core.report import (
    Afirmacion,
    Fuente,
    MetricaProducto,
    Prediccion,
    Report,
)
from replay.captura import Captura, Manifiesto

RAIZ = Path(__file__).resolve().parent.parent
SITIO = RAIZ / "docs" / "replay"
JS = SITIO / "replay.js"

AHORA = datetime(2026, 8, 10, 14, 30, 0)


# --- 1. los campos que el JS lee --------------------------------------------

def _captura_completa() -> Captura:
    """Una captura con TODOS los campos poblados.

    Poblarlos todos es el punto: un campo que quede en su valor por defecto no
    probaría que el sitio lo encuentra.
    """
    informe = Report(
        request_id="req-abc",
        consulta="Compará P001 y P002",
        generado_en=AHORA,
        modelo_llm="llama3.2:3b",
        fuentes=[
            Fuente(id="sql-kpis", tipo="sql", referencia="dbo.order_items",
                   consultada_en=AHORA),
            Fuente(id="doc-07", tipo="documento", referencia="informe_sector.pdf",
                   consultada_en=AHORA, seccion="3.2", url="https://ejemplo/doc"),
        ],
        resumen_ejecutivo=[
            Afirmacion(texto="P001 vendió más", tipo="hecho", fuentes=["sql-kpis"]),
        ],
        contexto_mercado=[
            Afirmacion(texto="El sector creció", tipo="hecho", fuentes=["doc-07"]),
        ],
        recomendaciones=[
            Afirmacion(texto="Reforzar stock de P001", tipo="recomendacion",
                       fuentes=["sql-kpis"]),
        ],
        metricas=[MetricaProducto(
            product_id="P001", nombre="Producto 001", unidades=340, revenue=15300.0,
            margen_pct=32.5, crecimiento_pct=12.1, tasa_devolucion_pct=2.4,
            fuente="sql-kpis",
        )],
        predicciones=[Prediccion(
            product_id="P001", horizonte_dias=30, valor=410.0,
            intervalo_inferior=380.0, intervalo_superior=440.0,
            mape_backtest=11.2, mape_baseline=18.7, modelo_version="ridge-v3",
        )],
        limitaciones=["Los datos son sintéticos."],
    )

    estado = AnalysisState(
        request_id="req-abc",
        consulta="Compará P001 y P002",
        intencion=Intencion.PRODUCT_PERFORMANCE,
        entidades=["P001", "P002"],
        periodo=Periodo(desde=date(2026, 1, 1), hasta=date(2026, 1, 31)),
        plan=[PasoPlan(tool="product_metrics", argumentos={"a": 1}, razon="porque sí")],
        informe=informe,
    )
    estado.registrar_paso("router", 12_400)
    estado.registrar_paso("ejecutor", 87, tool="product_metrics")
    estado.llamadas_tools = 1

    return Captura.desde_estado("cmp-01", estado, capturada_en=AHORA,
                                modelo_llm="llama3.2:3b")


# Cada ruta es un campo que `replay.js` lee por nombre. Si alguna deja de
# existir, el sitio muestra un hueco en silencio — por eso están escritas acá y
# no inferidas: la lista es el contrato, y se actualiza a mano y a conciencia.
CAMPOS_CAPTURA = [
    "id", "consulta", "intencion", "entidades", "llamadas_tools", "reintentos",
    "plan", "trace", "informe", "advertencias", "error", "duracion_total_ms",
    "periodo.desde", "periodo.hasta",
    "plan.0.tool", "plan.0.razon",
    "trace.0.nodo", "trace.0.duracion_ms", "trace.1.tool",
    "informe.resumen_ejecutivo.0.texto", "informe.resumen_ejecutivo.0.tipo",
    "informe.resumen_ejecutivo.0.fuentes",
    "informe.contexto_mercado", "informe.recomendaciones",
    "informe.metricas.0.product_id", "informe.metricas.0.nombre",
    "informe.metricas.0.unidades", "informe.metricas.0.revenue",
    "informe.metricas.0.margen_pct", "informe.metricas.0.crecimiento_pct",
    "informe.metricas.0.tasa_devolucion_pct",
    "informe.predicciones.0.product_id", "informe.predicciones.0.horizonte_dias",
    "informe.predicciones.0.valor", "informe.predicciones.0.mape_backtest",
    "informe.predicciones.0.mape_baseline",
    "informe.fuentes.0.id", "informe.fuentes.0.tipo",
    "informe.fuentes.0.referencia", "informe.fuentes.0.consultada_en",
    "informe.fuentes.1.url",
    "informe.advertencias", "informe.limitaciones",
]

CAMPOS_MANIFIESTO = [
    "capturado_en", "modelo_llm", "total", "reproducible_con",
    "casos.0.id", "casos.0.consulta", "casos.0.intencion",
    "casos.0.duracion_total_ms",
]


def _resolver(datos: Any, ruta: str) -> Any:
    actual = datos
    for tramo in ruta.split("."):
        if tramo.isdigit():
            actual = actual[int(tramo)]
        else:
            assert tramo in actual, f"falta el campo '{tramo}' en la ruta '{ruta}'"
            actual = actual[tramo]
    return actual


@pytest.mark.parametrize("ruta", CAMPOS_CAPTURA)
def test_la_captura_expone_cada_campo_que_el_sitio_lee(ruta: str) -> None:
    import json
    datos = json.loads(_captura_completa().model_dump_json())
    _resolver(datos, ruta)


@pytest.mark.parametrize("ruta", CAMPOS_MANIFIESTO)
def test_el_manifiesto_expone_cada_campo_que_el_sitio_lee(ruta: str) -> None:
    import json
    manifiesto = Manifiesto.desde_capturas([_captura_completa()], capturado_en=AHORA)
    datos = json.loads(manifiesto.model_dump_json())
    _resolver(datos, ruta)


# --- 2. las etapas que el sitio marca como "usa el modelo" ------------------

def test_las_etapas_llm_del_sitio_son_nodos_reales_del_grafo() -> None:
    """El sitio pinta de naranja `router` y `synthesizer`.

    Si el grafo renombra un nodo, el sitio seguiría pintando el nombre viejo —
    o sea, mostraría TODAS las etapas como determinísticas y la tesis central de
    la página ('dos de seis usan el modelo') quedaría muda sin que nada falle.
    """
    js = JS.read_text(encoding="utf-8")
    crudo = re.search(r"ETAPAS_LLM\s*=\s*new Set\(\[(.*?)\]\)", js, re.S)
    assert crudo, "no se encontró ETAPAS_LLM en replay.js"

    etapas = set(re.findall(r"'([^']+)'", crudo.group(1)))
    assert etapas == {"router", "synthesizer"}

    grafo = (RAIZ / "agent" / "graph.py").read_text(encoding="utf-8")
    nodos = set(re.findall(r'add_node\(\s*"([^"]+)"', grafo))

    assert etapas <= nodos, (
        f"el sitio marca como LLM etapas que el grafo no tiene: {etapas - nodos}"
    )


# --- 3. los comandos que el sitio le sugiere al visitante -------------------

def test_todo_comando_que_el_sitio_sugiere_existe_en_tasks() -> None:
    """Un mensaje de error que recomienda un comando inexistente es peor que
    no decir nada: manda a la persona a un callejón y le hace dudar del resto.
    """
    js = JS.read_text(encoding="utf-8")
    sugeridos = set(re.findall(r"tasks\.ps1 ([a-z-]+)", js))
    assert sugeridos, "el sitio no sugiere ningún comando; el test perdió sentido"

    tasks = (RAIZ / "tasks.ps1").read_text(encoding="utf-8-sig")
    declarados = set(re.findall(r'^\s*"([a-z-]+)"\s*\{', tasks, re.M))

    assert sugeridos <= declarados, (
        f"el sitio sugiere comandos que no existen en tasks.ps1: "
        f"{sorted(sugeridos - declarados)}"
    )


def test_el_comando_de_reproduccion_del_manifiesto_existe_en_tasks() -> None:
    """Lo mismo para la promesa que viaja en el manifiesto y se publica."""
    manifiesto = Manifiesto.desde_capturas([_captura_completa()], capturado_en=AHORA)

    tasks = (RAIZ / "tasks.ps1").read_text(encoding="utf-8-sig")
    declarados = set(re.findall(r'^\s*"([a-z-]+)"\s*\{', tasks, re.M))

    for comando in re.findall(r"tasks\.ps1 ([a-z-]+)", manifiesto.reproducible_con):
        assert comando in declarados, f"tasks.ps1 no declara '{comando}'"


# --- 4. el sitio está completo ----------------------------------------------

def test_el_sitio_tiene_sus_tres_archivos() -> None:
    for nombre in ("index.html", "estilos.css", "replay.js"):
        assert (SITIO / nombre).is_file(), f"falta {nombre}"


def test_el_sitio_lleva_de_vuelta_al_repositorio() -> None:
    """Sin esto el sitio es un callejón sin salida.

    El criterio de la fase 7 es que alguien entienda el valor en dos minutos, y
    hasta el 2026-08-28 lo lograba: entendía, y después no tenía adónde ir. Cero
    links al código, cero autoría. Un portfolio del que no se puede salir hacia
    el trabajo ni hacia quien lo hizo cumple la mitad de su función.

    Se afirma sobre el repositorio y no sobre un contacto porque el mail no va
    en una página indexable: desde el repositorio se llega al perfil, y desde el
    perfil a la persona.
    """
    html = (SITIO / "index.html").read_text(encoding="utf-8")

    assert "github.com/JoacoRubin/ai-market-intelligence" in html, (
        "el sitio no linkea al repositorio: quien lo lee no puede llegar al código"
    )


def test_el_sitio_no_pide_recursos_externos() -> None:
    """Cero dependencias de red.

    El proyecto entero corre sin servicios de terceros. Un sitio que se cae
    porque una CDN cambió una URL contradiría lo único que este demo demuestra.
    """
    html = (SITIO / "index.html").read_text(encoding="utf-8")
    css = (SITIO / "estilos.css").read_text(encoding="utf-8")

    for texto, donde in ((html, "index.html"), (css, "estilos.css")):
        assert "//fonts." not in texto, f"{donde} carga una fuente externa"
        assert "cdn." not in texto, f"{donde} carga algo de una CDN"
        assert "@import url(http" not in texto, f"{donde} importa CSS remoto"
