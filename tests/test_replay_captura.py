"""Tests del harness de captura para el replay estático.

Lo que se prueba acá es la parte PURA: convertir un `AnalysisState` ya ejecutado
en un payload serializable. Sin base de datos y sin modelo, en milisegundos.

Correr el grafo real es otra cosa y se prueba aparte (marcado `db` + `llm`): ahí
lo que se verifica es que el agente funciona, y eso ya lo cubre el golden set.
Mezclar ambas cosas daría un test que tarda minutos y que nadie correría —
justamente el problema que `ClienteLLM` existe para evitar.

La regla que estos tests defienden: **el replay no inventa nada**. Todo lo que
muestra sale de una ejecución real, y si un dato no estaba en el estado, no
aparece en la captura.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from agent.state import AnalysisState, Intencion, PasoPlan, Periodo
from core.report import Afirmacion, Fuente, MetricaProducto, Report
from replay.captura import Captura, Manifiesto

AHORA = datetime(2026, 8, 10, 14, 30, 0)
MODELO = "llama3.2:3b"


# --- material de prueba ------------------------------------------------------

def _informe() -> Report:
    """Informe mínimo pero VÁLIDO: con fuente declarada y cita que la resuelve.

    Se construye uno real y no un mock porque `Report` valida trazabilidad en el
    constructor. Un doble se saltearía justamente lo que hace valioso al replay.
    """
    return Report(
        request_id="req-abc123",
        consulta="Compará P001 y P002 en los últimos 30 días",
        generado_en=AHORA,
        modelo_llm=MODELO,
        fuentes=[Fuente(
            id="sql-kpis", tipo="sql",
            referencia="dbo.order_items JOIN dbo.orders",
            consultada_en=AHORA,
        )],
        resumen_ejecutivo=[Afirmacion(
            texto="P001 vendió 340 unidades contra 210 de P002",
            tipo="hecho", fuentes=["sql-kpis"],
        )],
        metricas=[MetricaProducto(
            product_id="P001", nombre="Producto 001", unidades=340,
            revenue=15300.0, margen_pct=32.5, fuente="sql-kpis",
        )],
    )


def _estado_completo() -> AnalysisState:
    """Estado como queda después de una corrida exitosa del grafo."""
    estado = AnalysisState(
        request_id="req-abc123",
        consulta="Compará P001 y P002 en los últimos 30 días",
        intencion=Intencion.PRODUCT_PERFORMANCE,
        entidades=["P001", "P002"],
        periodo=Periodo(desde=date(2026, 1, 1), hasta=date(2026, 1, 31)),
        plan=[PasoPlan(tool="product_metrics",
                       argumentos={"product_ids": ["P001", "P002"]},
                       razon="los KPIs se calculan por SQL, no por el modelo")],
        informe=_informe(),
    )
    estado.registrar_paso("router", 12_400, tool=None)
    estado.registrar_paso("planner", 3)
    estado.registrar_paso("ejecutor", 87, tool="product_metrics")
    estado.registrar_paso("synthesizer", 41_200)
    estado.registrar_paso("validator", 5)
    estado.llamadas_tools = 1
    return estado


def _estado_fuera_de_alcance() -> AnalysisState:
    """El grafo corta en el router y no hay informe. NO es un error."""
    estado = AnalysisState(
        request_id="req-def456",
        consulta="Contame un chiste",
        intencion=Intencion.FUERA_DE_ALCANCE,
    )
    estado.registrar_paso("router", 9_800)
    return estado


# --- lo que el replay muestra ------------------------------------------------

def test_preserva_los_tiempos_reales_de_cada_nodo() -> None:
    """Los `duracion_ms` del trace viajan intactos.

    Es el corazón del replay: la animación del grafo se anima con estos números.
    Si el harness los redondeara o los recalculara, el replay estaría mostrando
    una ejecución que nunca ocurrió.
    """
    captura = Captura.desde_estado("cmp-01", _estado_completo(),
                                   capturada_en=AHORA, modelo_llm=MODELO)

    assert [(p.nodo, p.duracion_ms) for p in captura.trace] == [
        ("router", 12_400),
        ("planner", 3),
        ("ejecutor", 87),
        ("synthesizer", 41_200),
        ("validator", 5),
    ]
    assert captura.duracion_total_ms == 53_695


def test_preserva_el_informe_con_sus_fuentes_y_citas() -> None:
    """Sin las fuentes no hay trazabilidad, y sin trazabilidad no hay demo."""
    captura = Captura.desde_estado("cmp-01", _estado_completo(),
                                   capturada_en=AHORA, modelo_llm=MODELO)

    assert captura.informe is not None
    assert [f.id for f in captura.informe.fuentes] == ["sql-kpis"]
    assert captura.informe.resumen_ejecutivo[0].fuentes == ["sql-kpis"]
    assert captura.informe.metricas[0].unidades == 340


def test_conserva_el_plan_con_la_razon_de_cada_herramienta() -> None:
    """El `razon` de cada paso es lo que hace auditable la elección del agente.

    Un replay que muestre 'llamó a product_metrics' sin decir por qué, muestra
    la mecánica y esconde el criterio. El criterio es lo interesante.
    """
    captura = Captura.desde_estado("cmp-01", _estado_completo(),
                                   capturada_en=AHORA, modelo_llm=MODELO)

    assert captura.plan[0].tool == "product_metrics"
    assert "SQL" in captura.plan[0].razon


def test_registra_el_presupuesto_consumido() -> None:
    """Cuántas herramientas usó y cuántas veces replanificó."""
    captura = Captura.desde_estado("cmp-01", _estado_completo(),
                                   capturada_en=AHORA, modelo_llm=MODELO)

    assert captura.llamadas_tools == 1
    assert captura.reintentos == 0


def test_declara_el_modelo_y_el_momento_de_la_captura() -> None:
    """Es lo que sostiene la honestidad del replay.

    Se publica como ejecución grabada, no como sistema en vivo. Sin estos dos
    campos esa declaración no se puede hacer con precisión.
    """
    captura = Captura.desde_estado("cmp-01", _estado_completo(),
                                   capturada_en=AHORA, modelo_llm=MODELO)

    assert captura.modelo_llm == MODELO
    assert captura.capturada_en == AHORA


# --- el caso que casi nadie muestra -----------------------------------------

def test_fuera_de_alcance_produce_una_captura_valida_sin_informe() -> None:
    """Que el agente diga "esto no me corresponde" es una CAPACIDAD.

    El harness tiene que poder capturarlo igual que un caso exitoso. Si tratara
    la ausencia de informe como un error, el replay solo podría mostrar caminos
    felices — y un agente que siempre responde algo es exactamente el problema
    que este sistema evita.
    """
    captura = Captura.desde_estado("out-01", _estado_fuera_de_alcance(),
                                   capturada_en=AHORA, modelo_llm=MODELO)

    assert captura.informe is None
    assert captura.intencion == "fuera_de_alcance"
    assert captura.error is None
    assert captura.trace[0].nodo == "router"


def test_propaga_las_advertencias_del_estado() -> None:
    estado = _estado_completo()
    estado._advertir("La predicción de P001 no tiene backtesting.")

    captura = Captura.desde_estado("cmp-01", estado,
                                   capturada_en=AHORA, modelo_llm=MODELO)

    assert "La predicción de P001 no tiene backtesting." in captura.advertencias


# --- contrato con el sitio estático -----------------------------------------

def test_la_captura_sobrevive_un_viaje_de_ida_y_vuelta_por_json() -> None:
    """El sitio estático consume exactamente este JSON.

    Si un campo no serializa —una fecha, un enum, un modelo anidado— el sitio
    recibe algo distinto de lo que el harness creyó escribir, y el error aparece
    en el navegador y no acá.
    """
    original = Captura.desde_estado("cmp-01", _estado_completo(),
                                    capturada_en=AHORA, modelo_llm=MODELO)

    ida = original.model_dump_json()
    vuelta = Captura.model_validate_json(ida)

    assert vuelta == original


def test_el_json_serializado_no_tiene_tipos_que_javascript_no_entienda() -> None:
    """Fechas y enums tienen que salir como strings, no como objetos Python."""
    captura = Captura.desde_estado("cmp-01", _estado_completo(),
                                   capturada_en=AHORA, modelo_llm=MODELO)

    crudo = json.loads(captura.model_dump_json())

    assert crudo["intencion"] == "product_performance"
    assert crudo["periodo"]["desde"] == "2026-01-01"
    assert crudo["capturada_en"].startswith("2026-08-10")


# --- el manifiesto -----------------------------------------------------------

def test_el_manifiesto_lista_las_capturas_y_dice_como_reproducirlas() -> None:
    """El manifiesto es la declaración pública de qué es este demo.

    Sin el comando de reproducción, el replay es una afirmación sin respaldo.
    Con él, cualquiera puede verificar que la ejecución grabada es real.
    """
    capturas = [
        Captura.desde_estado("cmp-01", _estado_completo(),
                             capturada_en=AHORA, modelo_llm=MODELO),
        Captura.desde_estado("out-01", _estado_fuera_de_alcance(),
                             capturada_en=AHORA, modelo_llm=MODELO),
    ]

    manifiesto = Manifiesto.desde_capturas(capturas, capturado_en=AHORA)

    assert manifiesto.total == 2
    assert [c.id for c in manifiesto.casos] == ["cmp-01", "out-01"]
    assert manifiesto.modelo_llm == MODELO
    assert "docker compose up" in manifiesto.reproducible_con


def test_el_manifiesto_resume_cada_caso_sin_arrastrar_el_informe_entero() -> None:
    """El índice se carga primero y tiene que ser liviano.

    Si el manifiesto trajera los informes completos, el navegador descargaría
    todas las ejecuciones para mostrar una lista de títulos.
    """
    capturas = [Captura.desde_estado("cmp-01", _estado_completo(),
                                     capturada_en=AHORA, modelo_llm=MODELO)]

    manifiesto = Manifiesto.desde_capturas(capturas, capturado_en=AHORA)
    crudo = json.loads(manifiesto.model_dump_json())

    assert "informe" not in crudo["casos"][0]
    assert crudo["casos"][0]["consulta"].startswith("Compará P001")
    assert crudo["casos"][0]["duracion_total_ms"] == 53_695


def test_el_manifiesto_rechaza_una_lista_vacia() -> None:
    """Publicar un replay sin ejecuciones es publicar una promesa vacía."""
    with pytest.raises(ValueError, match="al menos una captura"):
        Manifiesto.desde_capturas([], capturado_en=AHORA)


def test_el_manifiesto_rechaza_capturas_de_modelos_distintos() -> None:
    """Un manifiesto declara UN modelo. Mezclar corridas lo vuelve mentira.

    El replay se publica diciendo "generado por llama3.2:3b". Si la mitad de las
    ejecuciones salieron de otro modelo, esa frase es falsa y nadie lo notaría
    mirando el sitio. Se corta acá, donde todavía se ve.
    """
    capturas = [
        Captura.desde_estado("cmp-01", _estado_completo(),
                             capturada_en=AHORA, modelo_llm="llama3.2:3b"),
        Captura.desde_estado("out-01", _estado_fuera_de_alcance(),
                             capturada_en=AHORA, modelo_llm="otro-modelo:8b"),
    ]

    with pytest.raises(ValueError, match="un solo modelo"):
        Manifiesto.desde_capturas(capturas, capturado_en=AHORA)
