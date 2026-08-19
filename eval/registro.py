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
from typing import Any

from agent.llm import Uso
from core.report import Report
from eval.costo import FECHA_TARIFAS, costo_usd
from eval.metricas import EventoSembrado, Hallazgo, Proporcion

# El directorio vive dentro del repo a propósito: el historial de mediciones es
# tan parte del proyecto como el código que mide.
CORRIDAS = Path(__file__).resolve().parent / "corridas"


def procedencia() -> dict[str, object]:
    """Contra qué código se midió.

    **Se llama al EMPEZAR la corrida, no al guardarla.** Evaluarla al final
    describe un árbol que la propia salida ya ensució: la corrida del commit
    `60925fb` se lanzó con todo commiteado y quedó marcada `arbol_limpio:
    false` porque el JSON del registro se había escrito un instante antes.
    El instrumento se ensuciaba a sí mismo y después se declaraba irreproducible.

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


def _caso(evento: EventoSembrado, consulta: str, informe: Report | None,
          hallazgos: list[Hallazgo]) -> dict[str, object]:
    return {
        "evento": {
            "tipo": evento.tipo,
            "product_id": evento.product_id,
            "fecha": evento.fecha.isoformat(),
        },
        # Qué se le preguntó al agente. Desde que el eval mezcla consultas de
        # análisis con consultas de proyección, un promedio sobre todos los
        # casos junta dos poblaciones: sin este campo no se pueden separar.
        "consulta": consulta,
        # Un hueco es un dato: sin informe no hay nada que medir, y eso tiene
        # que poder distinguirse de un caso que se evaluó y salió mal.
        "informe": informe is not None,
        # La latencia es la tercera columna de la decisión, junto a calidad y
        # costo: un modelo que acierta más pero tarda cuatro veces más puede ser
        # el equivocado. `None` y no `0` cuando no hubo informe — no tardó cero,
        # no se midió.
        "duracion_ms": (
            sum(p.duracion_ms for p in informe.trace) if informe else None
        ),
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


def _uso(uso: Uso | None, modelo: str | None) -> dict[str, object] | None:
    """Lo que consumió la corrida, en tokens, más el costo que eso implica.

    Se guardan **tokens** y no solo dólares: los tokens son un hecho que reporta
    el proveedor, el precio es una tabla que se mueve. Con los tokens, una
    corrida vieja se puede recalcular con la tarifa de hoy; con solo dólares, el
    número queda congelado contra una tabla que nadie anotó.

    `None` entero cuando nadie contó, que no es lo mismo que cero: `ClienteOllama`
    no reporta uso a propósito, porque no cobra nada.
    """
    if uso is None:
        return None
    return {
        "tokens_entrada": uso.tokens_entrada,
        "tokens_salida": uso.tokens_salida,
        "tokens_cacheados": uso.tokens_cacheados,
        "llamadas": uso.llamadas,
        # `None` si no hay tarifa cargada: un modelo del que no sabemos el
        # precio no es gratis. Los tokens quedan igual, y el costo se puede
        # recalcular el día que se cargue.
        "costo_usd": costo_usd(uso, modelo) if modelo else None,
        "tarifas_al": FECHA_TARIFAS,
    }


def documento_de_corrida(
    corridas: list[tuple[EventoSembrado, str, Report | None, list[Hallazgo]]],
    proporciones: dict[str, Proporcion],
    umbrales: dict[str, float],
    generado_en: datetime,
    procedencia_inicial: dict[str, object] | None = None,
    uso: Uso | None = None,
) -> dict[str, object]:
    """Arma el documento de una corrida, listo para `json.dumps` sin conversores.

    Nada de `default=str`: si algo necesitara un conversor, el registro tendría
    fechas que a veces son texto y a veces no, y compararlas sería adivinar.
    """
    informes = [informe for *_, informe, _ in corridas if informe is not None]
    modelo = informes[0].modelo_llm if informes else None
    return {
        "generado_en": generado_en.isoformat(timespec="seconds"),
        # Comparar dos corridas de modelos distintos y llamarlo progreso es el
        # error que este campo previene.
        "modelo_llm": modelo,
        # Qué consumió y qué costó. Sin esto el registro contesta "¿anda bien?"
        # y no contesta "¿conviene?", que es la pregunta que decide si un
        # sistema se pone en producción.
        "uso": _uso(uso, modelo),
        # Capturada al arrancar la corrida cuando quien llama la pasa; si no,
        # se toma acá y describe el árbol de este instante.
        **(procedencia_inicial or procedencia()),
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


def comparar(anterior: dict[str, Any],
             actual: dict[str, Any]) -> list[dict[str, Any]]:
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

    antes = {m["nombre"]: m["valor"] for m in anterior["metricas"]}
    diferencias = []
    for metrica in actual["metricas"]:
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


def comparar_modelos(anterior: dict[str, Any],
                     actual: dict[str, Any]) -> dict[str, Any]:
    """Compara dos corridas de modelos distintos, y lo dice con todas las letras.

    `comparar()` se niega a hacer esto, y hace bien: sin esa guarda, cambiar el
    modelo, ver subir el número y atribuírselo al prompt es el error más
    tentador del oficio. La guarda **no se relaja**. Se agrega este camino
    aparte, cuyo nombre declara qué se está haciendo — que era exactamente lo
    que pedía el comentario original: *para comparar modelos hay que decir que
    se están comparando modelos*.

    La diferencia con `comparar()` no es técnica, es de intención. Ahí el modelo
    es constante y lo que varía es el código, así que un delta es una mejora o
    una regresión. Acá varía el modelo, así que un delta no es progreso: es un
    **trade-off**, y por eso se devuelve con el costo al lado.

    Y el costo al lado no es un adorno. La calidad sola dice cuál es mejor; la
    calidad junto al costo dice cuál conviene, que es otra pregunta y es la que
    se lleva a una reunión.
    """
    antes = {m["nombre"]: m["valor"] for m in anterior["metricas"]}
    metricas = []
    for metrica in actual["metricas"]:
        previo, ahora = antes.get(metrica["nombre"]), metrica["valor"]
        metricas.append({
            "nombre": metrica["nombre"],
            "antes": previo,
            "ahora": ahora,
            # Mismo criterio que `comparar()`: restar None menos None y escribir
            # 0 diría "no cambió nada". No cambió nada porque no se midió nada.
            "delta": None if previo is None or ahora is None else ahora - previo,
        })

    return {
        "modelos": [anterior["modelo_llm"], actual["modelo_llm"]],
        "metricas": metricas,
        "costo_usd": _delta_costo(anterior, actual),
        "duracion_ms": _delta(_latencia_total(anterior), _latencia_total(actual)),
    }


def _delta(previo: float | None, ahora: float | None) -> dict[str, Any]:
    return {
        "antes": previo,
        "ahora": ahora,
        "delta": None if previo is None or ahora is None else ahora - previo,
    }


def _delta_costo(anterior: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    """El costo de cada corrida y su diferencia.

    Si alguna de las dos no sabe cuánto costó, el delta no existe. Fingir un
    cero ahí diría "cuestan lo mismo", que es justo la conclusión equivocada
    cuando una de las dos es un modelo pago sin tarifa cargada.
    """
    def _de(doc: dict[str, Any]) -> float | None:
        uso = doc.get("uso")
        return uso.get("costo_usd") if uso else None

    return _delta(_de(anterior), _de(actual))


def _latencia_total(doc: dict[str, Any]) -> int | None:
    """Suma la duración de los casos que la tengan. `None` si ninguno la tiene."""
    duraciones = [c["duracion_ms"] for c in doc.get("casos", [])
                  if c.get("duracion_ms") is not None]
    return sum(duraciones) if duraciones else None
