"""Tests de la escritura del replay a disco.

Se prueba lo que se puede probar sin infraestructura: dado un conjunto de
capturas, qué archivos quedan y con qué contenido. Correr el grafo real para
producirlas es otra cosa —necesita SQL Server y Ollama— y no pertenece acá.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from core.report import Afirmacion, Fuente, Report
from replay.captura import Captura
from replay.escritura import escribir

AHORA = datetime(2026, 8, 10, 14, 30, 0)
MODELO = "llama3.2:3b"


def _captura(caso_id: str, con_informe: bool = True) -> Captura:
    informe = None
    if con_informe:
        informe = Report(
            request_id=f"req-{caso_id}",
            consulta="Compará P001 y P002",
            generado_en=AHORA,
            modelo_llm=MODELO,
            fuentes=[Fuente(id="sql-kpis", tipo="sql", referencia="dbo.orders",
                            consultada_en=AHORA)],
            resumen_ejecutivo=[Afirmacion(texto="P001 vendió más", tipo="hecho",
                                          fuentes=["sql-kpis"])],
        )
    return Captura(
        id=caso_id, consulta="Compará P001 y P002", capturada_en=AHORA,
        modelo_llm=MODELO, informe=informe,
    )


def test_escribe_un_json_por_caso_y_un_manifiesto(tmp_path: Path) -> None:
    escribir([_captura("cmp-01"), _captura("out-03", con_informe=False)],
             destino=tmp_path, capturado_en=AHORA)

    assert (tmp_path / "manifiesto.json").exists()
    assert (tmp_path / "casos" / "cmp-01.json").exists()
    assert (tmp_path / "casos" / "out-03.json").exists()


def test_el_manifiesto_escrito_es_json_valido_y_lista_los_casos(tmp_path: Path) -> None:
    escribir([_captura("cmp-01")], destino=tmp_path, capturado_en=AHORA)

    datos = json.loads((tmp_path / "manifiesto.json").read_text(encoding="utf-8"))

    assert datos["total"] == 1
    assert datos["casos"][0]["id"] == "cmp-01"
    assert "docker compose up" in datos["reproducible_con"]


def test_genera_el_pdf_solo_de_los_casos_que_tienen_informe(tmp_path: Path) -> None:
    """Sin informe no hay PDF, y eso no es un fallo.

    Un caso fuera de alcance termina sin informe a propósito. Si la escritura
    reventara ahí, el replay no podría mostrar el caso más interesante de todos.
    """
    escribir([_captura("cmp-01"), _captura("out-03", con_informe=False)],
             destino=tmp_path, capturado_en=AHORA)

    assert (tmp_path / "pdf" / "cmp-01.pdf").exists()
    assert not (tmp_path / "pdf" / "out-03.pdf").exists()


def test_el_pdf_escrito_es_un_pdf_de_verdad(tmp_path: Path) -> None:
    escribir([_captura("cmp-01")], destino=tmp_path, capturado_en=AHORA)

    crudo = (tmp_path / "pdf" / "cmp-01.pdf").read_bytes()

    assert crudo.startswith(b"%PDF-")


def test_crea_el_destino_si_no_existe(tmp_path: Path) -> None:
    destino = tmp_path / "docs" / "replay" / "data"

    escribir([_captura("cmp-01")], destino=destino, capturado_en=AHORA)

    assert (destino / "manifiesto.json").exists()


def test_el_json_de_cada_caso_trae_el_informe_completo(tmp_path: Path) -> None:
    """A diferencia del manifiesto, que es solo el índice."""
    escribir([_captura("cmp-01")], destino=tmp_path, capturado_en=AHORA)

    datos = json.loads((tmp_path / "casos" / "cmp-01.json").read_text(encoding="utf-8"))

    assert datos["informe"]["fuentes"][0]["id"] == "sql-kpis"
    assert datos["informe"]["resumen_ejecutivo"][0]["fuentes"] == ["sql-kpis"]
