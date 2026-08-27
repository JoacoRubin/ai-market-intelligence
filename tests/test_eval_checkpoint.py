"""Tests del guardado incremental de una corrida del golden set.

El punto 6 de la metodologia de ADR-003 pide "guardado incremental de
resultados", y el harness no lo tenia: el registro se escribia recien en el
fixture `resumen`, despues de los quince casos. Una corrida de 55 minutos
interrumpida en el minuto 50 no dejaba nada — paso dos veces el 2026-08-27.

La regla que importa NO es "guardar": es **contra que se puede reanudar**. Un
checkpoint reutilizado a traves de un cambio de codigo o de modelo mezclaria
dos sistemas en una sola tabla de metricas, que es exactamente la clase de bug
que este proyecto acaba de pagar caro con `llama3.2:3b` local contra `qwen3:4b`
en Docker. Por eso la identidad del checkpoint incluye commit y modelo.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from core.report import Afirmacion, Fuente, Report
from eval.checkpoint import cargar, clave_de_caso, descartar, guardar_caso
from eval.metricas import EventoSembrado, Hallazgo


@pytest.fixture
def carpeta(tmp_path: Path) -> Path:
    return tmp_path / "checkpoints"


def _evento() -> EventoSembrado:
    return EventoSembrado(tipo="pico_ventas", product_id="P033",
                          fecha=date(2025, 6, 9))


def _informe() -> Report:
    return Report(
        request_id="eval-analisis-pico_ventas-P033",
        consulta="analisis",
        generado_en=datetime(2026, 8, 27, 12, 0, 0),
        modelo_llm="qwen3:4b",
        # El Report valida que toda fuente citada este declarada, asi que el
        # doble tiene que declararla igual que el sistema real.
        fuentes=[Fuente(id="sql:product_metrics", tipo="sql",
                        referencia="dbo.order_items",
                        consultada_en=datetime(2026, 8, 27, 12, 0, 0))],
        resumen_ejecutivo=[Afirmacion(texto="P033 vendio 243 unidades.",
                                      tipo="hecho", fuentes=["sql:product_metrics"])],
    )


def _hallazgos() -> list[Hallazgo]:
    return [
        Hallazgo(nombre="analiza_el_producto_del_evento", cumple=True,
                 detalle="el informe analiza ['P033']"),
        Hallazgo(nombre="atribuye_al_producto_correcto", cumple=None,
                 detalle="ninguna recomendacion nombra un producto"),
    ]


def test_un_caso_guardado_vuelve_igual(carpeta: Path) -> None:
    """Ida y vuelta completo: informe, hallazgos y el `None` de `cumple`."""
    guardar_caso(carpeta, "abc1234", "qwen3:4b", _evento(), "analisis",
                 _informe(), _hallazgos())

    recuperado = cargar(carpeta, "abc1234", "qwen3:4b")
    caso = recuperado[clave_de_caso(_evento(), "analisis")]

    assert caso.informe is not None
    assert caso.informe.request_id == "eval-analisis-pico_ventas-P033"
    assert caso.informe.resumen_ejecutivo[0].texto == "P033 vendio 243 unidades."
    assert [h.nombre for h in caso.hallazgos] == [
        "analiza_el_producto_del_evento", "atribuye_al_producto_correcto"]
    # El None de `cumple` es el dato mas fragil del round-trip: si volviera
    # como False, una metrica que NO aplico se contaria como incumplida y el
    # porcentaje bajaria sin que nada hubiera empeorado.
    assert caso.hallazgos[1].cumple is None
    assert caso.evento == _evento()
    assert caso.clase == "analisis"


def test_un_caso_sin_informe_se_guarda_como_hueco(carpeta: Path) -> None:
    """Un caso que no produjo informe tiene que reanudarse como hueco.

    Si al reanudar se lo salteara sin mas, el hueco desapareceria y las
    metricas se calcularian sobre catorce casos como si fueran quince.
    """
    guardar_caso(carpeta, "abc1234", "qwen3:4b", _evento(), "analisis", None, [])

    caso = cargar(carpeta, "abc1234", "qwen3:4b")[clave_de_caso(_evento(), "analisis")]

    assert caso.informe is None
    assert caso.hallazgos == []


def test_un_checkpoint_de_otro_commit_no_se_reutiliza(carpeta: Path) -> None:
    """Reanudar a traves de un cambio de codigo mezclaria dos sistemas.

    La tabla de metricas diria una sola cosa sobre dos versiones distintas del
    agente, y nada en el resultado lo delataria.
    """
    guardar_caso(carpeta, "abc1234", "qwen3:4b", _evento(), "analisis",
                 _informe(), _hallazgos())

    assert cargar(carpeta, "otro999", "qwen3:4b") == {}


def test_un_checkpoint_de_otro_modelo_no_se_reutiliza(carpeta: Path) -> None:
    """Mismo argumento que el commit, y el que este proyecto ya pago caro."""
    guardar_caso(carpeta, "abc1234", "qwen3:4b", _evento(), "analisis",
                 _informe(), _hallazgos())

    assert cargar(carpeta, "abc1234", "llama3.2:3b") == {}


def test_cargar_sin_checkpoint_devuelve_vacio(carpeta: Path) -> None:
    """No existir no es un error: es una corrida que empieza de cero."""
    assert cargar(carpeta, "abc1234", "qwen3:4b") == {}


def test_descartar_borra_el_checkpoint(carpeta: Path) -> None:
    """Una corrida completa deja el registro definitivo; el parcial sobra.

    Dejarlo seria peor que inutil: la proxima corrida del mismo commit
    reanudaria desde el, y una re-medicion deliberada devolveria los numeros
    viejos sin invocar al modelo una sola vez.
    """
    guardar_caso(carpeta, "abc1234", "qwen3:4b", _evento(), "analisis",
                 _informe(), _hallazgos())
    descartar(carpeta, "abc1234", "qwen3:4b")

    assert cargar(carpeta, "abc1234", "qwen3:4b") == {}


def test_descartar_lo_que_no_existe_no_falla(carpeta: Path) -> None:
    descartar(carpeta, "abc1234", "qwen3:4b")


def test_dos_clases_del_mismo_evento_no_se_pisan(carpeta: Path) -> None:
    """`analisis` y `proyeccion` corren sobre el MISMO evento.

    Si la clave fuera solo el evento, el segundo sobrescribiria al primero y
    la corrida reanudada tendria catorce casos en vez de quince.
    """
    guardar_caso(carpeta, "abc1234", "qwen3:4b", _evento(), "analisis",
                 _informe(), _hallazgos())
    guardar_caso(carpeta, "abc1234", "qwen3:4b", _evento(), "proyeccion",
                 _informe(), _hallazgos())

    assert len(cargar(carpeta, "abc1234", "qwen3:4b")) == 2
