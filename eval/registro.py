"""Registro histórico de las corridas del eval.

Antes de esto el eval imprimía a stdout y no guardaba nada. La primera corrida
—la que motivó reescribir el prompt del sintetizador— existe solo en prosa,
dentro del mensaje del commit 98b5a17. Números, ninguno.

Un instrumento sin historial contesta "¿pasa el umbral?", que es un veredicto.
No contesta "¿mejoró?", que es la pregunta que uno se hace después de tocar algo.
Para eso hace falta la corrida anterior, y para que la comparación signifique
algo hace falta saber **contra qué se midió**: qué modelo, qué commit y si el
árbol de trabajo estaba limpio.

Un archivo JSON por corrida, indentado, ordenable por nombre. No un JSONL: el
historial es parte del entregable y alguien lo va a leer en GitHub sin
herramientas. Una línea de cuatro mil caracteres es técnicamente un registro y
prácticamente un blob.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from core.report import Report
from eval.metricas import EventoSembrado, Hallazgo, Proporcion

# El directorio vive dentro del repo a propósito: el historial de mediciones es
# tan parte del proyecto como el código que mide.
CORRIDAS = Path(__file__).resolve().parent / "corridas"


def _procedencia() -> dict[str, object]:
    """Contra qué código se midió.

    `arbol_limpio=False` marca que la corrida se hizo sobre cambios sin
    commitear. El resultado sigue siendo válido, pero el commit registrado no
    alcanza para reproducirlo, y decirlo es lo que separa un registro de una
    decoración. La primera corrida con instrumento sano fue exactamente así.

    Si no hay git, el registro no se cae: se guarda igual y se dice que no se
    sabe. Un eval que falla por no poder anotar su procedencia sería peor que
    uno que la anota incompleta.
    """
    def _git(*args: str) -> str | None:
        try:
            salida = subprocess.run(
                ["git", *args], capture_output=True, text=True,
                cwd=Path(__file__).resolve().parent.parent, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return salida.stdout.strip() if salida.returncode == 0 else None

    commit = _git("rev-parse", "--short", "HEAD")
    estado = _git("status", "--porcelain")
    return {
        "commit": commit if commit is not None else "desconocido",
        "arbol_limpio": estado == "",
    }


def _caso(evento: EventoSembrado, informe: Report | None,
          hallazgos: list[Hallazgo]) -> dict[str, object]:
    return {
        "evento": {
            "tipo": evento.tipo,
            "product_id": evento.product_id,
            "fecha": evento.fecha.isoformat(),
        },
        # Un hueco es un dato: sin informe no hay nada que medir, y eso tiene
        # que poder distinguirse de un caso que se evaluó y salió mal.
        "informe": informe is not None,
        "hallazgos": [
            {"nombre": h.nombre, "cumple": h.cumple, "detalle": h.detalle}
            for h in hallazgos
        ],
    }


def _metrica(nombre: str, proporcion: Proporcion | None,
             umbral: float) -> dict[str, object]:
    """El valor, su cobertura y su umbral, juntos.

    Guardar un 50% suelto no permite volver a interpretarlo: el umbral pudo
    haber sido 40% en esa corrida. El registro tiene que leerse sin el código
    de la época.
    """
    valor = proporcion.valor if proporcion else None
    return {
        "nombre": nombre,
        "valor": valor,
        "cumplidos": proporcion.cumplidos if proporcion else 0,
        "aplicables": proporcion.aplicables if proporcion else 0,
        "total": proporcion.total if proporcion else 0,
        "umbral": umbral,
        # `None` y no `False`: una métrica que no juzgó nada no reprobó.
        "alcanza": None if valor is None else valor >= umbral,
    }


def documento_de_corrida(
    corridas: list[tuple[EventoSembrado, Report | None, list[Hallazgo]]],
    proporciones: dict[str, Proporcion],
    umbrales: dict[str, float],
    generado_en: datetime,
) -> dict[str, object]:
    """Arma el documento de una corrida, listo para `json.dumps` sin conversores.

    Nada de `default=str`: si algo necesitara un conversor, el registro tendría
    fechas que a veces son texto y a veces no, y compararlas sería adivinar.
    """
    informes = [informe for _, informe, _ in corridas if informe is not None]
    return {
        "generado_en": generado_en.isoformat(timespec="seconds"),
        # Comparar dos corridas de modelos distintos y llamarlo progreso es el
        # error que este campo previene.
        "modelo_llm": informes[0].modelo_llm if informes else None,
        **_procedencia(),
        "casos": [_caso(*c) for c in corridas],
        "metricas": [
            _metrica(nombre, proporciones.get(nombre), umbral)
            for nombre, umbral in sorted(umbrales.items())
        ],
    }


def guardar(documento: dict[str, object], destino: Path = CORRIDAS) -> Path:
    """Escribe la corrida y devuelve el archivo. Append-only: nunca pisa otra.

    El nombre ordena cronológicamente porque así se comparan dos corridas sin
    escribir una herramienta: `ls` y listo.
    """
    destino.mkdir(parents=True, exist_ok=True)
    marca = str(documento["generado_en"]).replace(":", "").replace("-", "")
    archivo = destino / f"{marca}.json"
    archivo.write_text(
        json.dumps(documento, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return archivo


def comparar(anterior: dict[str, object],
             actual: dict[str, object]) -> list[dict[str, object]]:
    """Diferencia métrica por métrica entre dos corridas.

    Se niega a comparar corridas de modelos distintos, y eso no es rigidez: es
    el chequeo que evita la conclusión más tentadora y más falsa del oficio
    —cambiar el modelo, ver subir el número y atribuírselo al prompt—. Para
    comparar modelos hay que decir que se están comparando modelos.
    """
    if anterior["modelo_llm"] != actual["modelo_llm"]:
        raise ValueError(
            f"no se comparan corridas de modelos distintos: "
            f"{anterior['modelo_llm']} contra {actual['modelo_llm']}. "
            f"La diferencia no sería atribuible a ningún cambio del sistema."
        )

    antes = {m["nombre"]: m["valor"] for m in anterior["metricas"]}  # type: ignore[union-attr]
    diferencias = []
    for metrica in actual["metricas"]:  # type: ignore[union-attr]
        previo, ahora = antes.get(metrica["nombre"]), metrica["valor"]
        diferencias.append({
            "nombre": metrica["nombre"],
            "antes": previo,
            "ahora": ahora,
            # Restar None menos None y escribir 0 diría "no cambió nada". No
            # cambió nada porque no se midió nada, que no es lo mismo.
            "delta": None if previo is None or ahora is None else ahora - previo,
        })
    return diferencias
