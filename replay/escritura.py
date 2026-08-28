"""Volcado del replay a disco.

La forma de los archivos está pensada para cómo los va a pedir el navegador:

    manifiesto.json      el índice liviano; se carga primero
    casos/<id>.json      una ejecución completa; se carga al elegirla
    pdf/<id>.pdf         el informe tal como sale de la API

Partir el índice de las ejecuciones no es prolijidad: si todo viniera en un solo
archivo, mostrar una lista de cinco títulos obligaría a descargar cinco informes
completos con sus métricas, citas y trazas.

El PDF se escribe con el MISMO renderer que usa la API (`core.report_pdf`). Si
el replay tuviera su propio generador, en dos semanas el PDF del sitio diría
algo distinto del que descarga un usuario real — y el proyecto entero se apoya
en que eso no pase.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from core.report_pdf import render_pdf
from replay.captura import Captura, Manifiesto
from replay.procedencia import ProcedenciaReplay


def escribir(
    capturas: list[Captura],
    *,
    destino: Path,
    capturado_en: datetime,
    procedencia: ProcedenciaReplay | None = None,
) -> Manifiesto:
    """Escribe el manifiesto, las ejecuciones y los PDF. Devuelve el manifiesto.

    Valida antes de tocar el disco: `Manifiesto.desde_capturas` rechaza una
    lista vacía o corridas de modelos distintos. Escribir primero y validar
    después dejaría un replay a medias publicable.
    """
    manifiesto = Manifiesto.desde_capturas(
        capturas,
        capturado_en=capturado_en,
        procedencia=procedencia,
    )

    destino.mkdir(parents=True, exist_ok=True)
    (destino / "casos").mkdir(exist_ok=True)
    (destino / "pdf").mkdir(exist_ok=True)

    (destino / "manifiesto.json").write_text(
        manifiesto.model_dump_json(indent=2), encoding="utf-8"
    )

    for captura in capturas:
        (destino / "casos" / f"{captura.id}.json").write_text(
            captura.model_dump_json(indent=2), encoding="utf-8"
        )

        # Sin informe no hay PDF, y no es un fallo: un caso fuera de alcance
        # termina así a propósito. Reventar acá dejaría afuera justamente la
        # captura que muestra al agente negándose.
        if captura.informe is None:
            continue

        with (destino / "pdf" / f"{captura.id}.pdf").open("wb") as f:
            render_pdf(captura.informe, f)

    return manifiesto


def leer_manifiesto(destino: Path) -> Manifiesto:
    """Relee lo escrito. Útil para verificar una publicación sin regenerarla."""
    crudo = json.loads((destino / "manifiesto.json").read_text(encoding="utf-8"))
    return Manifiesto(**crudo)
