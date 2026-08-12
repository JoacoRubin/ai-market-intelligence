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

import pytest

from core.report import Fuente, Report
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


def _documento(**cambios):
    base = dict(
        corridas=[(EVENTO, _informe(), HALLAZGOS)],
        proporciones=PROPORCIONES,
        umbrales=UMBRALES,
        generado_en=AHORA,
    )
    return documento_de_corrida(**{**base, **cambios})


def _informe() -> Report:
    return Report(
        request_id="eval-pico_ventas-P033",
        consulta="Analizá el desempeño de P033 durante 2025-06",
        generado_en=AHORA,
        modelo_llm="llama3.2:3b",
        fuentes=[Fuente(id="sql-kpis", tipo="sql", referencia="dbo.orders",
                        consultada_en=AHORA)],
        resumen_ejecutivo=[],
        metricas=[],
        recomendaciones=[],
    )


# --- qué se guarda ------------------------------------------------------------

def test_guarda_el_valor_la_cobertura_y_el_umbral_de_cada_metrica():
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


def test_una_metrica_que_no_aplico_guarda_null_y_no_un_cero():
    """La distinción que costó una versión entera del eval.

    `None` significa que la métrica no juzgó nada. Un 0 diría que juzgó y salió
    todo mal, y un 1 que salió todo bien. Las tres cosas son distintas.
    """
    metricas = {m["nombre"]: m for m in _documento()["metricas"]}
    sin_aplicar = metricas["no_invierte_el_sentido_del_error"]

    assert sin_aplicar["valor"] is None
    assert sin_aplicar["aplicables"] == 0
    assert sin_aplicar["alcanza"] is None


def test_guarda_el_detalle_por_caso_y_no_solo_el_promedio():
    """El promedio dice que algo anda mal; el detalle dice dónde.

    Que P012 fallara por no citar la ficha del producto no se ve en un 17%.
    """
    caso = _documento()["casos"][0]

    assert caso["evento"] == {
        "tipo": "pico_ventas", "product_id": "P033", "fecha": "2025-06-09",
    }
    assert {"nombre": "usa_la_evidencia_documental", "cumple": False,
            "detalle": "citó ningún documento de 2"} in caso["hallazgos"]


def test_un_caso_sin_informe_queda_registrado_como_tal():
    """Un hueco es un dato. Si el agente no produjo informe, el registro tiene
    que poder distinguirlo de un caso que se evaluó y salió mal."""
    documento = _documento(corridas=[(EVENTO, None, [])])

    assert documento["casos"][0]["informe"] is False
    assert documento["casos"][0]["hallazgos"] == []


def test_guarda_el_modelo_que_se_evaluo():
    """Comparar dos corridas de modelos distintos y llamarlo progreso es el
    error que este campo previene."""
    assert _documento()["modelo_llm"] == "llama3.2:3b"


# --- la procedencia: contra qué código se midió -------------------------------

def test_guarda_el_commit_y_si_el_arbol_estaba_limpio():
    """Sin esto el número no se puede reproducir ni atribuir.

    `limpio=False` es la marca de que la corrida se hizo sobre cambios sin
    commitear: el resultado sigue siendo válido, pero el commit registrado NO
    alcanza para volver a obtenerlo. Decirlo es lo que separa un registro de
    una decoración.
    """
    documento = _documento()

    assert isinstance(documento["commit"], str)
    assert isinstance(documento["arbol_limpio"], bool)


def test_la_procedencia_se_puede_capturar_antes_de_la_corrida():
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


def test_sin_procedencia_explicita_se_captura_sola():
    """No se rompe a quien la llame sin el dato."""
    documento = _documento()

    assert "commit" in documento and "arbol_limpio" in documento


def test_procedencia_informa_el_commit_y_el_estado_del_arbol():
    from eval.registro import procedencia

    p = procedencia()

    assert set(p) == {"commit", "arbol_limpio"}
    assert isinstance(p["arbol_limpio"], bool)


# --- persistencia -------------------------------------------------------------

def test_guardar_escribe_un_json_legible_por_corrida(tmp_path):
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


def test_el_nombre_del_archivo_ordena_cronologicamente(tmp_path):
    """Ordenar por nombre tiene que ser ordenar por fecha: es como se van a
    comparar dos corridas sin escribir una herramienta."""
    primero = guardar(_documento(generado_en=datetime(2026, 8, 12, 9, 0, 0)), tmp_path)
    segundo = guardar(_documento(generado_en=datetime(2026, 8, 12, 15, 30, 0)), tmp_path)

    assert sorted([segundo.name, primero.name]) == [primero.name, segundo.name]


def test_dos_corridas_no_se_pisan(tmp_path):
    """Append-only: una corrida nueva no borra la anterior. Si se pisaran, el
    historial tendría siempre un solo elemento y no sería un historial."""
    guardar(_documento(generado_en=datetime(2026, 8, 12, 9, 0, 0)), tmp_path)
    guardar(_documento(generado_en=datetime(2026, 8, 12, 15, 30, 0)), tmp_path)

    assert len(list(tmp_path.glob("*.json"))) == 2


def test_guardar_crea_el_directorio_si_no_existe(tmp_path):
    """La primera corrida en una máquina nueva no puede fallar por un mkdir."""
    destino = guardar(_documento(), tmp_path / "corridas")

    assert destino.exists()


def test_el_documento_es_serializable_sin_conversores(tmp_path):
    """Si algo del documento necesitara `default=str`, el registro tendría
    fechas que a veces son texto y a veces no. Se serializa tal cual o no se
    guarda."""
    json.dumps(_documento())


# --- comparación entre corridas -----------------------------------------------

def test_no_se_puede_comparar_una_corrida_con_un_modelo_distinto():
    """El chequeo que evita la conclusión más tentadora y más falsa: cambiar el
    modelo, ver subir el número y atribuírselo al prompt."""
    from eval.registro import comparar

    anterior = _documento()
    actual = _documento()
    actual["modelo_llm"] = "qwen3:4b"

    with pytest.raises(ValueError, match="modelo"):
        comparar(anterior, actual)


def test_comparar_devuelve_la_diferencia_por_metrica():
    from eval.registro import comparar

    anterior = _documento()
    actual = _documento(proporciones={
        **PROPORCIONES, "usa_la_evidencia_documental": Proporcion(6, 6, 6),
    })

    diferencias = {d["nombre"]: d for d in comparar(anterior, actual)}

    assert diferencias["usa_la_evidencia_documental"]["antes"] == 0.5
    assert diferencias["usa_la_evidencia_documental"]["ahora"] == 1.0
    assert diferencias["usa_la_evidencia_documental"]["delta"] == 0.5


def test_una_metrica_que_no_aplico_en_ninguna_de_las_dos_no_tiene_delta():
    """Restar `None` menos `None` y escribir 0 diría que no cambió nada. No
    cambió nada porque no se midió nada, que no es lo mismo."""
    from eval.registro import comparar

    diferencias = {d["nombre"]: d for d in comparar(_documento(), _documento())}

    assert diferencias["no_invierte_el_sentido_del_error"]["delta"] is None
