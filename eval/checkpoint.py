"""Guardado incremental de una corrida del golden set, para poder reanudarla.

El punto 6 de la metodología de ADR-003 pide "guardado incremental de
resultados". El harness no lo tenía: `documento_de_corrida` se escribía recién
después de los quince casos, así que una corrida de 55 minutos interrumpida en
el minuto 50 no dejaba absolutamente nada. Pasó dos veces el 2026-08-27.

**Lo difícil no es guardar: es decidir contra qué se puede reanudar.** Un
checkpoint reutilizado a través de un cambio de código o de modelo produciría
una sola tabla de métricas describiendo dos sistemas distintos, y nada en el
resultado lo delataría. Es exactamente la clase de bug que este proyecto acaba
de pagar caro —el eval corría `llama3.2:3b` en local mientras Docker corría
`qwen3:4b`— así que la identidad del archivo incluye **commit y modelo**, y un
cambio en cualquiera de los dos empieza de cero.

No se versiona: es un parcial, y el resultado que vale es el de
`eval/corridas/`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.report import Report
from eval.metricas import EventoSembrado, Hallazgo

CHECKPOINTS = Path(__file__).resolve().parent / "checkpoints"


@dataclass(frozen=True)
class CasoGuardado:
    """Un caso ya corrido, tal como lo devolvería el grafo.

    `informe` es `Report | None` y el `None` se conserva a propósito: es un
    caso que NO produjo informe. Saltearlo al reanudar borraría el hueco, y
    las métricas se calcularían sobre catorce casos como si fueran quince.
    """

    evento: EventoSembrado
    clase: str
    informe: Report | None
    hallazgos: list[Hallazgo]


def clave_de_caso(evento: EventoSembrado, clase: str) -> str:
    """Identifica un caso dentro de una corrida.

    Incluye la clase porque `analisis` y `proyeccion` corren sobre el MISMO
    evento: sin ella el segundo pisaría al primero.
    """
    return f"{clase}|{evento.tipo}|{evento.product_id}|{evento.fecha.isoformat()}"


def _archivo(carpeta: Path, commit: str, modelo: str) -> Path:
    # El modelo entra en el nombre saneado: `qwen3:4b` tiene dos puntos, que en
    # Windows abren un flujo de datos alternativo en vez de un archivo.
    seguro = modelo.replace(":", "_").replace("/", "_")
    return carpeta / f"parcial-{commit}-{seguro}.json"


def cargar(carpeta: Path, commit: str, modelo: str) -> dict[str, CasoGuardado]:
    """Los casos ya corridos para ESE commit y ESE modelo. Vacío si no hay.

    Que no exista no es un error: es una corrida que empieza de cero.
    """
    archivo = _archivo(carpeta, commit, modelo)
    if not archivo.exists():
        return {}

    crudo: dict[str, Any] = json.loads(archivo.read_text(encoding="utf-8"))
    casos: dict[str, CasoGuardado] = {}
    for clave, d in crudo.get("casos", {}).items():
        informe = Report.model_validate(d["informe"]) if d["informe"] else None
        casos[clave] = CasoGuardado(
            evento=EventoSembrado.model_validate(d["evento"]),
            clase=d["clase"],
            informe=informe,
            hallazgos=[Hallazgo(**h) for h in d["hallazgos"]],
        )
    return casos


def guardar_caso(carpeta: Path, commit: str, modelo: str,
                 evento: EventoSembrado, clase: str, informe: Report | None,
                 hallazgos: list[Hallazgo]) -> None:
    """Agrega un caso al parcial y lo escribe entero.

    Se reescribe el archivo completo y no se hace append: son quince casos, el
    costo es irrelevante frente al minuto y medio que tarda cada uno, y un JSON
    íntegro después de cada escritura es lo que hace que el parcial sirva
    justamente cuando el proceso muere.
    """
    carpeta.mkdir(parents=True, exist_ok=True)
    archivo = _archivo(carpeta, commit, modelo)

    crudo: dict[str, Any] = ({"commit": commit, "modelo": modelo, "casos": {}}
                             if not archivo.exists()
                             else json.loads(archivo.read_text(encoding="utf-8")))

    crudo["casos"][clave_de_caso(evento, clase)] = {
        "evento": evento.model_dump(mode="json"),
        "clase": clase,
        "informe": informe.model_dump(mode="json") if informe else None,
        "hallazgos": [
            {"nombre": h.nombre, "cumple": h.cumple, "detalle": h.detalle}
            for h in hallazgos
        ],
    }

    # Escritura atómica: si el proceso muere a mitad de un `write_text`, el
    # parcial queda truncado y la reanudación explota al parsearlo — el único
    # momento en que este archivo tiene que funcionar.
    temporal = archivo.with_suffix(".tmp")
    temporal.write_text(json.dumps(crudo, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    temporal.replace(archivo)


def descartar(carpeta: Path, commit: str, modelo: str) -> None:
    """Borra el parcial. Se llama cuando la corrida completa ya se registró.

    Dejarlo sería peor que inútil: la próxima corrida del mismo commit
    reanudaría desde él, y una re-medición deliberada devolvería los números
    viejos sin invocar al modelo una sola vez.
    """
    _archivo(carpeta, commit, modelo).unlink(missing_ok=True)
