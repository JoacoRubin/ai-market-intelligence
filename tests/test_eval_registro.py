"""Tests del registro de corridas del eval.

El eval imprimía a stdout y no guardaba nada. La primera corrida —la que motivó
reescribir el prompt del sintetizador— existe solo en prosa, dentro del mensaje
del commit 98b5a17. Números, ninguno.

Un instrumento de medición sin historial no puede contestar la única pregunta
que importa después de tocar algo: **¿mejoró?** Puede decir "pasa el umbral" o
"no pasa", que es un veredicto; no puede decir "subió de 50% a 83%", que es una
medición.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from core.report import Fuente, PasoTrace, Report
from eval.metricas import EventoSembrado, Hallazgo, Proporcion
from eval.registro import documento_de_corrida, guardar

AHORA = datetime(2026, 8, 12, 15, 30, 0)

EVENTO = EventoSembrado(
    tipo="pico_ventas", product_id="P033", fecha=date(2025, 6, 9),
    magnitud=31.2, descripcion="Campaña de descuento",
)

HALLAZGOS = [
    Hallazgo("analiza_el_producto_del_evento", True, "el informe analiza ['P033']"),
    Hallazgo("usa_la_evidencia_documental", False, "citó ningún documento de 2"),
    Hallazgo("no_invierte_el_sentido_del_error", None, "el informe no reporta MAPE"),
]

PROPORCIONES = {
    "analiza_el_producto_del_evento": Proporcion(6, 6, 6),
    "usa_la_evidencia_documental": Proporcion(3, 6, 6),
    "no_invierte_el_sentido_del_error": Proporcion(0, 0, 6),
}

UMBRALES = {
    "analiza_el_producto_del_evento": 0.80,
    "usa_la_evidencia_documental": 0.90,
    "no_invierte_el_sentido_del_error": 0.90,
}


def _documento(**cambios: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        corridas=[(EVENTO, "analisis", _informe(), HALLAZGOS)],
        proporciones=PROPORCIONES,
        umbrales=UMBRALES,
        generado_en=AHORA,
    )
    return documento_de_corrida(**{**base, **cambios})


def _informe() -> Report:
    return _informe_con_modelo("llama3.2:3b")


def _informe_con_modelo(modelo: str) -> Report:
    return Report(
        request_id="eval-pico_ventas-P033",
        consulta="Analizá el desempeño de P033 durante 2025-06",
        generado_en=AHORA,
        modelo_llm=modelo,
        fuentes=[Fuente(id="sql-kpis", tipo="sql", referencia="dbo.orders",
                        consultada_en=AHORA)],
        resumen_ejecutivo=[],
        metricas=[],
        recomendaciones=[],
        # El trace es lo que permite medir latencia por caso. Dos pasos con
        # duraciones distintas: si el registro sumara mal, un solo paso no lo
        # mostraría.
        trace=[PasoTrace(nodo="router", duracion_ms=9_437),
               PasoTrace(nodo="synthesizer", duracion_ms=512)],
    )


# --- qué se guarda ------------------------------------------------------------

def test_guarda_el_valor_la_cobertura_y_el_umbral_de_cada_metrica() -> None:
    """Sin el umbral al lado, el valor no se puede volver a interpretar.

    Un 50% guardado suelto no dice si pasó o no: el umbral podría haber sido
    40% en esa corrida. El registro tiene que ser legible sin el código de la
    época.
    """
    metricas = {m["nombre"]: m for m in _documento()["metricas"]}

    assert metricas["usa_la_evidencia_documental"] == {
        "nombre": "usa_la_evidencia_documental",
        "valor": 0.5, "cumplidos": 3, "aplicables": 6, "total": 6,
        "umbral": 0.90, "alcanza": False,
    }


def test_una_metrica_que_no_aplico_guarda_null_y_no_un_cero() -> None:
    """La distinción que costó una versión entera del eval.

    `None` significa que la métrica no juzgó nada. Un 0 diría que juzgó y salió
    todo mal, y un 1 que salió todo bien. Las tres cosas son distintas.
    """
    metricas = {m["nombre"]: m for m in _documento()["metricas"]}
    sin_aplicar = metricas["no_invierte_el_sentido_del_error"]

    assert sin_aplicar["valor"] is None
    assert sin_aplicar["aplicables"] == 0
    assert sin_aplicar["alcanza"] is None


def test_guarda_el_detalle_por_caso_y_no_solo_el_promedio() -> None:
    """El promedio dice que algo anda mal; el detalle dice dónde.

    Que P012 fallara por no citar la ficha del producto no se ve en un 17%.
    """
    caso = _documento()["casos"][0]

    assert caso["evento"] == {
        "tipo": "pico_ventas", "product_id": "P033", "fecha": "2025-06-09",
    }
    assert {"nombre": "usa_la_evidencia_documental", "cumple": False,
            "detalle": "citó ningún documento de 2"} in caso["hallazgos"]


def test_un_caso_sin_informe_queda_registrado_como_tal() -> None:
    """Un hueco es un dato. Si el agente no produjo informe, el registro tiene
    que poder distinguirlo de un caso que se evaluó y salió mal."""
    documento = _documento(corridas=[(EVENTO, "analisis", None, [])])

    assert documento["casos"][0]["informe"] is False
    assert documento["casos"][0]["hallazgos"] == []


def test_guarda_que_consulta_se_le_hizo_al_agente() -> None:
    """Desde que el eval mezcla consultas de análisis con consultas de
    proyección, un promedio sobre todos los casos junta dos poblaciones. Sin
    este campo el registro no permite separarlas después."""
    documento = _documento(
        corridas=[(EVENTO, "analisis", _informe(), HALLAZGOS),
                  (EVENTO, "proyeccion", _informe(), HALLAZGOS)])

    assert [c["consulta"] for c in documento["casos"]] == [
        "analisis", "proyeccion"]


def test_guarda_el_modelo_que_se_evaluo() -> None:
    """Comparar dos corridas de modelos distintos y llamarlo progreso es el
    error que este campo previene."""
    assert _documento()["modelo_llm"] == "llama3.2:3b"


# --- la procedencia: contra qué código se midió -------------------------------

def test_guarda_el_commit_y_si_el_arbol_estaba_limpio() -> None:
    """Sin esto el número no se puede reproducir ni atribuir.

    `limpio=False` es la marca de que la corrida se hizo sobre cambios sin
    commitear: el resultado sigue siendo válido, pero el commit registrado NO
    alcanza para volver a obtenerlo. Decirlo es lo que separa un registro de
    una decoración.
    """
    documento = _documento()

    assert isinstance(documento["commit"], str)
    assert isinstance(documento["arbol_limpio"], bool)


def test_la_procedencia_se_puede_capturar_antes_de_la_corrida() -> None:
    """El instrumento se ensuciaba a sí mismo.

    La corrida del commit 60925fb se lanzó con el árbol commiteado y quedó
    marcada `arbol_limpio: false`. La causa era el propio registro: al evaluar
    la procedencia recién al GUARDAR, el archivo de salida ya estaba escrito y
    ensuciaba el árbol que se estaba describiendo.

    La procedencia describe **con qué código se midió**, así que se captura
    cuando la corrida empieza, no cuando termina.
    """
    documento = _documento(
        procedencia_inicial={"commit": "abc1234", "arbol_limpio": True})

    assert documento["commit"] == "abc1234"
    assert documento["arbol_limpio"] is True


def test_sin_procedencia_explicita_se_captura_sola() -> None:
    """No se rompe a quien la llame sin el dato."""
    documento = _documento()

    assert "commit" in documento and "arbol_limpio" in documento


def test_procedencia_informa_el_commit_y_el_estado_del_arbol() -> None:
    from eval.registro import procedencia

    p = procedencia()

    assert set(p) == {"commit", "arbol_limpio"}
    assert isinstance(p["arbol_limpio"], bool)


# --- persistencia -------------------------------------------------------------

def test_guardar_escribe_un_json_legible_por_corrida(tmp_path: Path) -> None:
    """Un archivo por corrida, indentado, y no una línea JSONL.

    El historial de corridas es parte del entregable: alguien lo va a leer en
    GitHub sin herramientas. Un JSONL de líneas de cuatro mil caracteres es
    técnicamente un registro y prácticamente un blob.
    """
    destino = guardar(_documento(), tmp_path)

    assert destino.parent == tmp_path
    assert destino.suffix == ".json"
    assert "\n  " in destino.read_text(encoding="utf-8")
    assert json.loads(destino.read_text(encoding="utf-8"))["modelo_llm"] == "llama3.2:3b"


def test_el_nombre_del_archivo_ordena_cronologicamente(tmp_path: Path) -> None:
    """Ordenar por nombre tiene que ser ordenar por fecha: es como se van a
    comparar dos corridas sin escribir una herramienta."""
    primero = guardar(_documento(generado_en=datetime(2026, 8, 12, 9, 0, 0)), tmp_path)
    segundo = guardar(_documento(generado_en=datetime(2026, 8, 12, 15, 30, 0)), tmp_path)

    assert sorted([segundo.name, primero.name]) == [primero.name, segundo.name]


def test_dos_corridas_no_se_pisan(tmp_path: Path) -> None:
    """Append-only: una corrida nueva no borra la anterior. Si se pisaran, el
    historial tendría siempre un solo elemento y no sería un historial."""
    guardar(_documento(generado_en=datetime(2026, 8, 12, 9, 0, 0)), tmp_path)
    guardar(_documento(generado_en=datetime(2026, 8, 12, 15, 30, 0)), tmp_path)

    assert len(list(tmp_path.glob("*.json"))) == 2


def test_guardar_crea_el_directorio_si_no_existe(tmp_path: Path) -> None:
    """La primera corrida en una máquina nueva no puede fallar por un mkdir."""
    destino = guardar(_documento(), tmp_path / "corridas")

    assert destino.exists()


def test_el_documento_es_serializable_sin_conversores(tmp_path: Path) -> None:
    """Si algo del documento necesitara `default=str`, el registro tendría
    fechas que a veces son texto y a veces no. Se serializa tal cual o no se
    guarda."""
    json.dumps(_documento())


# --- comparación entre corridas -----------------------------------------------

def test_no_se_puede_comparar_una_corrida_con_un_modelo_distinto() -> None:
    """El chequeo que evita la conclusión más tentadora y más falsa: cambiar el
    modelo, ver subir el número y atribuírselo al prompt."""
    from eval.registro import comparar

    anterior = _documento()
    actual = _documento()
    actual["modelo_llm"] = "qwen3:4b"

    with pytest.raises(ValueError, match="modelo"):
        comparar(anterior, actual)


def test_comparar_devuelve_la_diferencia_por_metrica() -> None:
    from eval.registro import comparar

    anterior = _documento()
    actual = _documento(proporciones={
        **PROPORCIONES, "usa_la_evidencia_documental": Proporcion(6, 6, 6),
    })

    diferencias = {d["nombre"]: d for d in comparar(anterior, actual)}

    assert diferencias["usa_la_evidencia_documental"]["antes"] == 0.5
    assert diferencias["usa_la_evidencia_documental"]["ahora"] == 1.0
    assert diferencias["usa_la_evidencia_documental"]["delta"] == 0.5


def test_una_metrica_que_no_aplico_en_ninguna_de_las_dos_no_tiene_delta() -> None:
    """Restar `None` menos `None` y escribir 0 diría que no cambió nada. No
    cambió nada porque no se midió nada, que no es lo mismo."""
    from eval.registro import comparar

    diferencias = {d["nombre"]: d for d in comparar(_documento(), _documento())}

    assert diferencias["no_invierte_el_sentido_del_error"]["delta"] is None


# --- lo que costó y lo que tardó ----------------------------------------------
#
# Hasta acá el registro guardaba SOLO calidad, y eso contesta "¿anda bien?" sin
# contestar "¿conviene?". Un informe perfecto a un dólar la consulta y otro 95%
# igual a tres centavos no son el mismo producto, aunque el golden set les dé
# casi lo mismo. Desde que hay un proveedor que cobra, la diferencia es la
# decisión.

def test_guarda_los_tokens_consumidos_por_la_corrida() -> None:
    """Tokens y no dólares: los tokens son un hecho, el precio es una tabla.

    Guardando tokens, una corrida vieja se puede recalcular con la tarifa de
    hoy. Guardando solo dólares, el número queda congelado contra una tabla que
    nadie anotó.
    """
    from agent.llm import Uso

    doc = _documento(uso=Uso(tokens_entrada=35_190, tokens_salida=1_995,
                             tokens_cacheados=0, llamadas=30))

    assert doc["uso"]["tokens_entrada"] == 35_190
    assert doc["uso"]["tokens_salida"] == 1_995
    assert doc["uso"]["llamadas"] == 30


def test_guarda_el_costo_y_contra_que_tarifa_se_calculo() -> None:
    """El costo sin la fecha de la tarifa es un número que no se puede auditar."""
    from agent.llm import Uso
    from eval.costo import FECHA_TARIFAS

    doc = _documento(uso=Uso(tokens_entrada=1_000_000, tokens_salida=0))

    assert doc["uso"]["costo_usd"] == 0.0  # llama3.2:3b es local
    assert doc["uso"]["tarifas_al"] == FECHA_TARIFAS


def test_un_modelo_sin_tarifa_guarda_los_tokens_igual() -> None:
    """Perder la corrida entera por no saber un precio sería peor que registrarla.

    Midió la calidad bien; lo único que no sabe es cuánto salió. Los tokens
    quedan, y el costo se puede recalcular el día que se cargue la tarifa.
    """
    from agent.llm import Uso

    doc = _documento(uso=Uso(tokens_entrada=500, tokens_salida=100))
    doc_ajeno = _documento(
        corridas=[(EVENTO, "analisis", _informe_con_modelo("gpt-5.6-terra"),
                   HALLAZGOS)],
        uso=Uso(tokens_entrada=500, tokens_salida=100),
    )

    assert doc["uso"]["tokens_entrada"] == 500
    assert doc_ajeno["uso"]["costo_usd"] is None
    assert doc_ajeno["uso"]["tokens_entrada"] == 500


def test_una_corrida_sin_uso_lo_deja_en_null_y_no_en_cero() -> None:
    """Cero tokens diría que corrió sin llamar al modelo. No es lo que pasó:
    es que nadie los contó. `ClienteOllama` no reporta uso a propósito."""
    assert _documento()["uso"] is None


def test_guarda_cuanto_tardo_cada_caso() -> None:
    """La latencia es la tercera columna de la decisión, con calidad y costo.

    Un modelo que acierta más pero tarda cuatro veces más puede ser el
    equivocado, y sin este número esa conversación no se puede tener.
    """
    doc = _documento()
    assert doc["casos"][0]["duracion_ms"] == 9_437 + 512


def test_un_caso_sin_informe_no_inventa_una_duracion() -> None:
    """Sin informe no hay trace. `None` y no `0`: no tardó cero, no se midió."""
    doc = _documento(corridas=[(EVENTO, "analisis", None, HALLAZGOS)])
    assert doc["casos"][0]["duracion_ms"] is None


# --- comparar modelos, dicho con todas las letras -----------------------------

def test_comparar_modelos_si_acepta_corridas_de_modelos_distintos() -> None:
    """`comparar()` se niega, y hace bien. Esta función existe para ese caso.

    La guarda de `comparar()` no se relaja: se agrega un camino cuyo NOMBRE
    declara que se están comparando modelos. Era exactamente lo que pedía el
    comentario original —"para comparar modelos hay que decir que se están
    comparando modelos"—, y aflojar el guard hubiera tirado abajo la única
    defensa contra atribuirle al prompt una mejora que fue del modelo.
    """
    from eval.registro import comparar_modelos

    local = _documento()
    pago = _documento(corridas=[
        (EVENTO, "analisis", _informe_con_modelo("claude-opus-5"), HALLAZGOS)])

    resultado = comparar_modelos(local, pago)

    assert resultado["modelos"] == ["llama3.2:3b", "claude-opus-5"]


def test_comparar_modelos_deja_ver_la_diferencia_por_metrica() -> None:
    from eval.registro import comparar_modelos

    local = _documento()
    pago = _documento(
        corridas=[(EVENTO, "analisis", _informe_con_modelo("claude-opus-5"),
                   HALLAZGOS)],
        proporciones={**PROPORCIONES,
                      "usa_la_evidencia_documental": Proporcion(6, 6, 6)},
    )

    metricas = {m["nombre"]: m for m in comparar_modelos(local, pago)["metricas"]}
    assert metricas["usa_la_evidencia_documental"]["delta"] == 0.5


def test_comparar_modelos_pone_el_costo_al_lado_de_la_calidad() -> None:
    """Es todo el punto del ejercicio.

    La calidad sola dice cuál es mejor. La calidad al lado del costo dice cuál
    conviene, que es una pregunta distinta y es la que se lleva a una reunión.
    """
    from agent.llm import Uso
    from eval.registro import comparar_modelos

    local = _documento(uso=Uso(tokens_entrada=35_190, tokens_salida=1_995))
    pago = _documento(
        corridas=[(EVENTO, "analisis", _informe_con_modelo("claude-opus-5"),
                   HALLAZGOS)],
        uso=Uso(tokens_entrada=35_190, tokens_salida=1_995),
    )

    costos = comparar_modelos(local, pago)["costo_usd"]
    assert costos["antes"] == 0.0
    assert costos["ahora"] == pytest.approx(0.2258, abs=1e-4)


def test_comparar_modelos_no_finge_un_delta_de_costo_desconocido() -> None:
    """Si una de las dos corridas no sabe cuánto costó, el delta no existe."""
    from eval.registro import comparar_modelos

    resultado = comparar_modelos(_documento(), _documento())
    assert resultado["costo_usd"]["delta"] is None
