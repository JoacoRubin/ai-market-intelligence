"""Tests de las métricas de calidad del informe.

Estas métricas existen para cerrar el riesgo que el ADR-003 dejó documentado y
sin medir: **groundedness 100% con el informe igualmente equivocado.** El
auditor numérico verificó 22 de 22 cifras y no detectó que el informe invertía
el significado del MAPE, recomendaba sobre el producto equivocado y dejaba las
citas pegadas al final sin integrarlas.

Cada métrica de acá corresponde a un defecto **concreto y observado** en ese
ADR. No son criterios inventados: son los cinco errores que un informe real ya
cometió.

**Ninguna usa un LLM como juez**, y eso es una decisión, no una limitación. Se
tiene el producto, la fecha y la magnitud del evento en `ground_truth`: si se
puede resolver de forma determinística, no se delega al modelo.

**Una métrica que no aplica devuelve `None`, no `True`.** La primera corrida real
dejó esto en evidencia: `atribuye_al_producto_correcto` y
`no_invierte_el_sentido_del_error` dieron 100% en las seis corridas sin haber
juzgado nada — el agente no generó recomendaciones con producto ni predicciones,
así que ambas pasaron por abstención. Un 100% conseguido así dice "la situación
nunca se dio", no "lo hace bien", y es el mismo defecto que el ADR-003 le
encontró al auditor numérico: *el instrumento de medición también necesita ser
validado.*
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from core.report import Afirmacion, Fuente, MetricaProducto, Prediccion, Report
from eval.metricas import EventoSembrado, evaluar, resumir

AHORA = datetime(2026, 3, 31, 12, 0)

EVENTO = EventoSembrado(
    tipo="pico_devoluciones",
    product_id="P010",
    fecha=date(2026, 2, 14),
    magnitud=5.7,
    descripcion="Cambio de lote del proveedor",
)

FUENTE_SQL = Fuente(id="sql-kpis", tipo="sql", referencia="dbo.order_items",
                    consultada_en=AHORA)
FUENTE_DOC = Fuente(id="doc-112", tipo="documento", referencia="acta_proveedor.pdf",
                    consultada_en=AHORA, seccion="3.2")

PREDICCION = Prediccion(product_id="P010", horizonte_dias=30, valor=900.0,
                        mape_backtest=8.3, mape_baseline=12.0)


def _informe(**cambios: Any) -> Report:
    """Informe correcto por defecto, que ejercita las CINCO métricas.

    Lleva predicción y evidencia documental a propósito: si el caso base dejara
    métricas sin aplicar, un informe "correcto" convivirían con medidores que
    nunca corrieron, que es justo el problema que estos tests vigilan.
    """
    base: dict[str, Any] = dict(
        request_id="req-1",
        consulta="¿Por qué subieron las devoluciones de P010?",
        generado_en=AHORA,
        modelo_llm="llama3.2:3b",
        fuentes=[FUENTE_SQL, FUENTE_DOC],
        resumen_ejecutivo=[
            Afirmacion(
                texto="P010 vendió 1.243 unidades por USD 87.010 y su tasa de "
                      "devolución trepó a 5,7%. La proyección a 30 días tiene un "
                      "error de 8,3%.",
                tipo="hecho", fuentes=["sql-kpis", "doc-112"],
            ),
        ],
        metricas=[
            # El nombre tiene la forma que produce `seeds.generate`: "<marca>
            # <categoría>". El fixture decía "Producto 010" y ahí se escondió
            # durante meses el defecto de `atribuye_al_producto_correcto`: con
            # ese nombre, un informe que nombra al producto como lo nombra el
            # modelo real —por su marca— se veía idéntico a uno que no lo
            # nombra. Un fixture que no se parece a los datos de producción no
            # protege de nada.
            MetricaProducto(product_id="P010", nombre="Sable calzado", unidades=1243,
                            revenue=87010.0, tasa_devolucion_pct=5.7,
                            fuente="sql-kpis"),
        ],
        predicciones=[PREDICCION],
        recomendaciones=[
            Afirmacion(texto="Auditar el lote del proveedor de P010.",
                       tipo="recomendacion", fuentes=["doc-112"]),
        ],
    )
    base.update(cambios)
    return Report(**base)


def _por_nombre(informe: Report,
                evento: EventoSembrado = EVENTO) -> dict[str, bool | None]:
    return {h.nombre: h.cumple for h in evaluar(informe, evento)}


# --- el caso base ejercita todo -----------------------------------------------

def test_un_informe_correcto_cumple_las_cinco_metricas() -> None:
    """Sin este test, una métrica rota siempre daría 'incumple' y parecería que
    el agente falla cuando el que falla es el medidor."""
    assert all(v is True for v in _por_nombre(_informe()).values())


# --- 1. el producto del evento ------------------------------------------------

def test_detecta_que_el_informe_no_analiza_el_producto_del_evento() -> None:
    informe = _informe(
        metricas=[MetricaProducto(product_id="P999", nombre="Otro", unidades=10,
                                  revenue=100.0, fuente="sql-kpis")],
    )

    assert _por_nombre(informe)["analiza_el_producto_del_evento"] is False


# --- 2. atribución: el defecto exacto del ADR-003 -----------------------------

def _narrativa(texto: str, **cambios: Any) -> Report:
    """Informe cuyo texto es exactamente el que se le pasa, y nada más.

    La atribución se juzga sobre la prosa, así que los tests de esta sección
    necesitan controlarla entera: si el fixture base dejara su propio resumen
    nombrando a P010, cada caso mediría esa frase y no la suya.
    """
    return _informe(
        resumen_ejecutivo=[Afirmacion(texto=texto, tipo="hecho",
                                      fuentes=["sql-kpis", "doc-112"])],
        recomendaciones=[],
        **cambios,
    )


def test_la_atribucion_mira_todo_el_texto_y_no_solo_las_recomendaciones() -> None:
    """El defecto que dejó la métrica en 0/15 de cobertura con qwen3:4b.

    La métrica leía solo `informe.recomendaciones`, y esa sección **el sistema
    no la produce**: la regla 3 de `synthesizer.SISTEMA` prohíbe recomendar, y
    el nodo escribe todo en `resumen_ejecutivo`. La única vía a
    `recomendaciones` es `validator.validar_informe`, que reubica lo que el
    modelo escribió DESOBEDECIENDO esa regla.

    O sea que la cobertura medía desobediencia: llama3.2:3b desobedecía 2 de 15
    veces y la métrica juzgaba 2 casos; qwen3:4b obedece 15 de 15 y la métrica
    se quedó sin juzgar nada. El modelo no empeoró — mejoró, y el instrumento
    lo leyó como pérdida.
    """
    informe = _narrativa(
        "P010 vendió 1.243 unidades por USD 87.010, con un error de 8,3%.")

    assert _por_nombre(informe)["atribuye_al_producto_correcto"] is True


def test_reconoce_al_producto_por_su_nombre_y_no_solo_por_su_id() -> None:
    """El segundo defecto, abajo del primero.

    La métrica buscaba `\\bP\\d{3,}\\b` —identificadores— y el informe escribe
    NOMBRES: 4 de las 5 capturas de `docs/replay/data/casos/` no contienen un
    solo ID en su prosa. El prompt lo refuerza, porque sus ejemplos dicen
    "Alfa" y "Beta". Así que aunque hubiera recomendaciones, la métrica habría
    fallado igual: por formato, no por atribución.
    """
    informe = _narrativa(
        "Sable calzado vendió 1.243 unidades por USD 87.010, con un error "
        "de 8,3%.")

    assert _por_nombre(informe)["atribuye_al_producto_correcto"] is True


def test_reconoce_la_marca_sola_cuando_identifica_a_un_unico_producto() -> None:
    """El modelo abrevia. La captura `cmp-01` dice "el bajo crecimiento en
    Ribera", no "en Ribera accesorios"."""
    informe = _narrativa(
        "El crecimiento de Sable se apoyó en 1.243 unidades por USD 87.010, "
        "con un error de 8,3%.")

    assert _por_nombre(informe)["atribuye_al_producto_correcto"] is True


def test_una_marca_compartida_por_dos_productos_no_atribuye_a_ninguno() -> None:
    """`seeds.generate` combina 8 marcas con 5 categorías, así que la marca se
    repite: `Vertex calzado` y `Vertex deportes` son dos productos distintos.

    Ante "Vertex" no se sabe de cuál habla, y **la métrica se abstiene**.
    Elegir uno sería inventar la atribución que se está midiendo: acertaría o
    fallaría por azar, y en las dos direcciones el número mentiría.
    """
    informe = _narrativa(
        "Vertex creció con 1.243 unidades por USD 87.010, con un error de 8,3%.",
        metricas=[
            MetricaProducto(product_id="P010", nombre="Vertex calzado",
                            unidades=1243, revenue=87010.0,
                            tasa_devolucion_pct=5.7, fuente="sql-kpis"),
            MetricaProducto(product_id="P009", nombre="Vertex deportes",
                            unidades=980, revenue=51000.0, fuente="sql-kpis"),
        ],
    )

    assert _por_nombre(informe)["atribuye_al_producto_correcto"] is None


def test_un_producto_sin_nombre_no_se_atribuye_por_el_borde_vacio() -> None:
    """`nombre=""` produce el patrón `\\b\\b`, que casa en cualquier borde de
    palabra: sin este guardia, ese producto quedaría nombrado en TODO informe
    que tenga una letra, y la métrica pasaría a medir la nada con cara de 100%.

    No es hipotético por capricho: `MetricaProducto.nombre` es un `str` sin
    mínimo, y el nombre lo trae el catálogo, que es un borde de entrada.
    """
    informe = _narrativa(
        "Las ventas alcanzaron 1.243 unidades por USD 87.010, con un error "
        "de 8,3%.",
        metricas=[MetricaProducto(product_id="P010", nombre="", unidades=1243,
                                  revenue=87010.0, tasa_devolucion_pct=5.7,
                                  fuente="sql-kpis")],
    )

    assert _por_nombre(informe)["atribuye_al_producto_correcto"] is None


def test_detecta_que_el_informe_habla_del_producto_equivocado() -> None:
    """El ADR-003 lo documentó textual: el informe sugería reducir devoluciones
    para el producto del 2,1% en vez del que tenía 5,7%."""
    informe = _narrativa(
        "P003 vendió 1.243 unidades por USD 87.010, con un error de 8,3%.")

    assert _por_nombre(informe)["atribuye_al_producto_correcto"] is False


def test_nombrar_a_otro_producto_ademas_del_correcto_no_es_un_defecto() -> None:
    """Un informe comparativo nombra a los dos, y eso es su trabajo.

    El defecto del ADR-003 es hablar del producto equivocado EN VEZ del que
    tuvo el evento, no mencionar a un segundo. Exigir exclusividad reprobaría
    a `cmp-01`, que compara `Vertex calzado` con `Ribera accesorios` porque se
    lo pidieron.
    """
    informe = _narrativa(
        "P010 vendió 1.243 unidades por USD 87.010 y superó a P003, con un "
        "error de 8,3%.")

    assert _por_nombre(informe)["atribuye_al_producto_correcto"] is True


def test_sin_ningun_producto_nombrado_la_atribucion_no_aplica() -> None:
    """`None`, no `True`. Es la corrección que motivó esta métrica.

    En la primera corrida real las seis ejecuciones dieron 100% sin haber
    juzgado nada. Un 100% conseguido por abstención dice "la situación nunca se
    dio", no "lo hace bien".

    Y sigue sin penalizar la ausencia: si no nombrar a nadie contara como
    error, el incentivo sería nombrar productos a lo loco para zafar.
    """
    informe = _narrativa(
        "Las ventas alcanzaron 1.243 unidades por USD 87.010, con un error "
        "de 8,3%.")

    assert _por_nombre(informe)["atribuye_al_producto_correcto"] is None


# --- 3. magnitudes absolutas --------------------------------------------------

def test_detecta_un_informe_que_solo_habla_en_porcentajes() -> None:
    """Otro defecto textual del ADR-003: unidades, revenue y proyecciones no
    aparecían por ningún lado. El informe hablaba solo en porcentajes."""
    informe = _informe(
        resumen_ejecutivo=[Afirmacion(
            texto="La tasa de devolución de P010 creció 3,6 puntos porcentuales.",
            tipo="hecho", fuentes=["sql-kpis", "doc-112"])],
    )

    assert _por_nombre(informe)["reporta_magnitudes_absolutas"] is False


def test_reconoce_la_magnitud_aunque_venga_con_separadores_de_miles() -> None:
    """1243, 1.243 y 1 243 son el mismo número. Medir formato en vez de
    contenido daría un falso negativo en cada informe bien escrito."""
    informe = _informe(
        resumen_ejecutivo=[Afirmacion(
            texto="P010 vendió 1 243 unidades en el período.",
            tipo="hecho", fuentes=["sql-kpis", "doc-112"])],
    )

    assert _por_nombre(informe)["reporta_magnitudes_absolutas"] is True


def test_sin_metricas_las_magnitudes_no_aplican() -> None:
    informe = _informe(metricas=[], predicciones=[])

    assert _por_nombre(informe)["reporta_magnitudes_absolutas"] is None


# --- 4. evidencia integrada, no decorativa ------------------------------------

def test_detecta_que_no_uso_ninguno_de_los_documentos_recuperados() -> None:
    """El ADR-003: 'tiene en el contexto dos documentos que explican el pico y
    concluye sugiere una posible causa externa'. Tenía la respuesta adelante.

    En la corrida real pasó dos veces, en los `pico_ventas` de P033: el RAG
    recuperó los documentos de la promoción y el informe no citó ninguno.
    """
    informe = _informe(
        resumen_ejecutivo=[Afirmacion(
            texto="P010 vendió 1.243 unidades por USD 87.010.",
            tipo="hecho", fuentes=["sql-kpis"])],
        recomendaciones=[Afirmacion(
            texto="Auditar el lote del proveedor de P010.",
            tipo="recomendacion", fuentes=["sql-kpis"])],
    )

    assert _por_nombre(informe)["usa_la_evidencia_documental"] is False


def test_alcanza_con_citar_uno_de_los_documentos_disponibles() -> None:
    """Es lo que pide la regla 7 del prompt, y es lo correcto.

    Si el RAG recupera cuatro pasajes y solo uno explica el evento, exigir que
    los cite a los cuatro obligaría a citar los irrelevantes. La métrica no debe
    pedir más de lo que el sistema debería hacer.
    """
    otro_doc = Fuente(id="doc-999", tipo="documento", referencia="ruido.pdf",
                      consultada_en=AHORA)
    informe = _informe(
        fuentes=[FUENTE_SQL, FUENTE_DOC, otro_doc],
        resumen_ejecutivo=[Afirmacion(
            texto="P010 vendió 1.243 unidades por USD 87.010 con un error de 8,3%.",
            tipo="hecho", fuentes=["sql-kpis", "doc-112"])],
    )

    assert _por_nombre(informe)["usa_la_evidencia_documental"] is True


def test_sin_evidencia_documental_recuperada_la_metrica_no_aplica() -> None:
    """Un análisis puramente numérico no tiene documentos que integrar. Eso no
    es cumplir: es que la pregunta no corresponde."""
    informe = _informe(
        fuentes=[FUENTE_SQL],
        resumen_ejecutivo=[Afirmacion(
            texto="P010 vendió 1.243 unidades por USD 87.010.",
            tipo="hecho", fuentes=["sql-kpis"])],
        recomendaciones=[],
    )

    assert _por_nombre(informe)["usa_la_evidencia_documental"] is None


# --- 4 bis. citar un documento no obliga a citarlos todos ---------------------

def test_citar_uno_alcanza_aunque_queden_documentos_declarados_sin_citar() -> None:
    """El reemplazo de `no_declara_documentos_sin_usar`, eliminada el 2026-08-12.

    Aquella métrica contaba como defecto cada documento declarado que ningún
    texto citara, y así contradecía a esta: juntas exigían citarlos todos. Dio
    17% sobre un umbral de 75% castigando informes por no citar, entre otros,
    la ficha del producto — que no explica ningún evento.

    `Report.fuentes` es la biblioteca consultada, no la bibliografía citada. Que
    sobre un documento irrelevante no es rigor aparente: es el RAG haciendo su
    trabajo y el sintetizador eligiendo bien.
    """
    ruido = Fuente(id="doc-999", tipo="documento", referencia="ficha-producto.pdf",
                   consultada_en=AHORA)
    informe = _informe(
        fuentes=[FUENTE_SQL, FUENTE_DOC, ruido],
        resumen_ejecutivo=[Afirmacion(
            texto="P010 vendió 1.243 unidades por USD 87.010 con un error de 8,3%.",
            tipo="hecho", fuentes=["sql-kpis", "doc-112"])],
    )

    assert _por_nombre(informe)["usa_la_evidencia_documental"] is True


def test_la_metrica_del_sobrante_ya_no_se_reporta() -> None:
    """Una métrica eliminada no puede reaparecer en el resumen: si volviera,
    volvería con su umbral y con el 17% que no medía lo que decía medir."""
    assert "no_declara_documentos_sin_usar" not in _por_nombre(_informe())


# --- 5. inversión del significado de una métrica ------------------------------

def test_detecta_que_llama_precision_a_un_error() -> None:
    """El ADR-003: 'describe un MAPE de 8,3% como precisión del 8,3%'. El número
    es correcto y la afirmación es falsa — el caso que un validador numérico no
    puede ver."""
    informe = _informe(
        resumen_ejecutivo=[Afirmacion(
            texto="P010 vendió 1.243 unidades por USD 87.010, con una precisión "
                  "del 8,3% en la proyección.",
            tipo="hecho", fuentes=["sql-kpis", "doc-112"])],
    )

    assert _por_nombre(informe)["no_invierte_el_sentido_del_error"] is False


def test_acepta_que_hable_de_error_cuando_es_error() -> None:
    assert _por_nombre(_informe())["no_invierte_el_sentido_del_error"] is True


def test_sin_predicciones_la_metrica_del_error_no_aplica() -> None:
    """La otra métrica que daba 100% sin haber juzgado nada.

    En las seis corridas reales el agente no produjo una sola predicción, así
    que no hubo ningún MAPE que malinterpretar. Eso no es hacerlo bien.
    """
    informe = _informe(predicciones=[])

    assert _por_nombre(informe)["no_invierte_el_sentido_del_error"] is None


# --- agregación ---------------------------------------------------------------

def test_resumir_calcula_sobre_los_casos_donde_la_metrica_aplico() -> None:
    bueno = evaluar(_informe(), EVENTO)
    malo = evaluar(
        _informe(metricas=[MetricaProducto(product_id="P999", nombre="Otro",
                                           unidades=10, revenue=100.0,
                                           fuente="sql-kpis")]),
        EVENTO,
    )
    no_aplica = evaluar(
        _narrativa("Las ventas alcanzaron 1.243 unidades por USD 87.010, con "
                   "un error de 8,3%."),
        EVENTO,
    )

    resumen = resumir([bueno, malo, no_aplica])

    # Aplicó en las tres corridas; cumplió en dos.
    assert resumen["analiza_el_producto_del_evento"].aplicables == 3
    assert resumen["analiza_el_producto_del_evento"].valor == 2 / 3

    # Aplicó en dos de tres, y en esas dos cumplió. El caso que no aplicó no
    # infla ni hunde el número: queda fuera del denominador.
    assert resumen["atribuye_al_producto_correcto"].aplicables == 2
    assert resumen["atribuye_al_producto_correcto"].valor == 1.0
    assert resumen["atribuye_al_producto_correcto"].total == 3


def test_una_metrica_que_nunca_aplico_vale_None_y_no_uno() -> None:
    """El corazón de la corrección. Un 100% por abstención es una mentira."""
    corridas = [evaluar(_informe(predicciones=[]), EVENTO) for _ in range(6)]

    resumen = resumir(corridas)
    metrica = resumen["no_invierte_el_sentido_del_error"]

    assert metrica.aplicables == 0
    assert metrica.valor is None
    assert metrica.total == 6


def test_resumir_sin_corridas_no_inventa_un_cien_por_ciento() -> None:
    """Cero corridas no es calidad perfecta."""
    assert resumir([]) == {}
