"""Evaluación del router contra el golden set, con el modelo real.

Estos tests son distintos de todos los demás del proyecto: no verifican que el
código haga lo que dice, sino que **el modelo entienda**. Son lentos (invocan
`llama3.2:3b` en CPU) y por eso están marcados `slow` y `llm`: no corren en cada
commit, sino cuando se toca el prompt o se cambia de modelo.

Contexto: el spike inicial midió que el modelo clasificaba mal la intención en
**3 de 3 casos**, siempre como `company_research`. El JSON era válido contra el
esquema; el contenido estaba mal. La corrección fueron los few-shots del prompt
del router. Estos tests miden si esa corrección funcionó.

El umbral se fija ANTES de mirar el resultado, por la misma razón que en el
spike: si se mide primero y se decide después, el umbral termina acomodándose
al número que salió. Eso no es evaluar, es justificar.

    Accuracy mínima de intención: 80%
    Accuracy mínima de entidades: 70%
    Accuracy mínima en HOLDOUT:   75%

**Sobre el holdout.** Algunos casos del golden set aparecen textualmente como
ejemplos en el prompt del router (`en_prompt: true`). Acertarlos no prueba nada:
el modelo tiene la respuesta escrita adelante. Es contaminación del conjunto de
evaluación, y es uno de los errores más comunes y más graves al medir sistemas
con LLM — un 100% conseguido así se desarma con la primera consulta real.

Por eso la métrica que importa es la del **holdout**: los casos que NO están en
el prompt. Esa es la que dice si el modelo entendió la categoría o memorizó una
plantilla.

Correr solo estos:   uv run pytest -m llm -v
Saltearlos:          uv run pytest -m "not llm"
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent.llm import ClienteOllama
from agent.nodes.router import enrutar
from agent.state import AnalysisState

pytestmark = [pytest.mark.slow, pytest.mark.llm]

GOLDEN = Path(__file__).resolve().parent.parent / "eval" / "golden_set.jsonl"

# Fijados antes de medir. No se tocan para que un resultado "casi" pase.
MIN_ACCURACY_INTENCION = 0.80
MIN_ACCURACY_ENTIDADES = 0.70
MIN_ACCURACY_HOLDOUT = 0.75


def _casos() -> list[dict[str, Any]]:
    return [json.loads(linea) for linea in
            GOLDEN.read_text(encoding="utf-8").splitlines() if linea.strip()]


@pytest.fixture(scope="module")
def cliente() -> ClienteOllama:
    c = ClienteOllama()
    if not c.disponible():
        pytest.skip("Ollama no responde: levantalo con `ollama serve`")
    return c


@pytest.fixture(scope="module")
def resultados(cliente: ClienteOllama) -> list[dict[str, Any]]:
    """Corre el golden set completo una sola vez y comparte los resultados.

    Sin `scope="module"` cada test volvería a invocar el modelo y la suite
    pasaría de minutos a decenas de minutos.
    """
    salida = []
    for caso in _casos():
        estado = AnalysisState(request_id=f"eval-{caso['id']}",
                               consulta=caso["consulta"])
        enrutar(estado, cliente)
        salida.append({
            "id": caso["id"],
            "consulta": caso["consulta"],
            "esperada": caso["intencion"],
            "obtenida": estado.intencion.value if estado.intencion else None,
            "entidades_esperadas": caso["product_ids"],
            "entidades_obtenidas": estado.entidades,
            "en_prompt": caso.get("en_prompt", False),
            "ok_intencion": (estado.intencion.value if estado.intencion else None)
                            == caso["intencion"],
            "ok_entidades": estado.entidades == caso["product_ids"],
        })
    return salida


@pytest.fixture(scope="module")
def holdout(resultados: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Casos que NO aparecen como ejemplos en el prompt."""
    return [r for r in resultados if not r["en_prompt"]]


# --- Métricas ----------------------------------------------------------------

def test_accuracy_de_intencion(resultados: list[dict[str, Any]]) -> None:
    aciertos = sum(1 for r in resultados if r["ok_intencion"])
    accuracy = aciertos / len(resultados)

    fallos = [f"{r['id']}: esperaba {r['esperada']}, dio {r['obtenida']}"
              for r in resultados if not r["ok_intencion"]]

    print(f"\n  ACCURACY DE INTENCIÓN: {accuracy:.0%} "
          f"({aciertos}/{len(resultados)})")
    for f in fallos:
        print(f"    FALLO  {f}")

    assert accuracy >= MIN_ACCURACY_INTENCION, (
        f"accuracy de {accuracy:.0%}, por debajo del mínimo "
        f"{MIN_ACCURACY_INTENCION:.0%}. Fallos: {fallos}"
    )


def test_accuracy_de_extraccion_de_entidades(resultados: list[dict[str, Any]]) -> None:
    aciertos = sum(1 for r in resultados if r["ok_entidades"])
    accuracy = aciertos / len(resultados)

    fallos = [f"{r['id']}: esperaba {r['entidades_esperadas']}, "
              f"dio {r['entidades_obtenidas']}"
              for r in resultados if not r["ok_entidades"]]

    print(f"\n  ACCURACY DE ENTIDADES: {accuracy:.0%} "
          f"({aciertos}/{len(resultados)})")
    for f in fallos:
        print(f"    FALLO  {f}")

    assert accuracy >= MIN_ACCURACY_ENTIDADES, (
        f"accuracy de {accuracy:.0%}, por debajo del mínimo "
        f"{MIN_ACCURACY_ENTIDADES:.0%}. Fallos: {fallos}"
    )


def test_accuracy_en_holdout(
    holdout: list[dict[str, Any]], resultados: list[dict[str, Any]],
) -> None:
    """La métrica que de verdad importa.

    Los casos que están en el prompt los acierta por tenerlos escritos adelante.
    Estos no: si acierta acá, entendió la categoría. Si solo acierta los del
    prompt, memorizó una plantilla y en producción se cae con la primera
    consulta que nadie previó.
    """
    aciertos = sum(1 for r in holdout if r["ok_intencion"])
    accuracy = aciertos / len(holdout)

    en_prompt = [r for r in resultados if r["en_prompt"]]
    acc_prompt = sum(1 for r in en_prompt if r["ok_intencion"]) / len(en_prompt)

    print(f"\n  ACCURACY EN HOLDOUT: {accuracy:.0%} ({aciertos}/{len(holdout)})")
    print(f"  (casos que están en el prompt: {acc_prompt:.0%} — no cuentan)")
    for r in holdout:
        if not r["ok_intencion"]:
            print(f"    FALLO  {r['id']}: esperaba {r['esperada']}, "
                  f"dio {r['obtenida']}")
            print(f"           {r['consulta']!r}")

    # Una brecha grande entre ambas es la señal clásica de memorización.
    if acc_prompt - accuracy > 0.30:
        print("    ATENCIÓN: brecha grande entre casos del prompt y holdout. "
              "El modelo puede estar memorizando en vez de generalizar.")

    assert accuracy >= MIN_ACCURACY_HOLDOUT, (
        f"accuracy en holdout de {accuracy:.0%}, por debajo del mínimo "
        f"{MIN_ACCURACY_HOLDOUT:.0%}"
    )


# --- Casos que no se pueden fallar -------------------------------------------

def test_el_caso_que_fallaba_en_el_spike_ahora_pasa(resultados: list[dict[str, Any]]) -> None:
    """Regresión explícita del hallazgo del spike.

    "Compará P001 y P002" se clasificaba como company_research. Si vuelve a
    fallar, los few-shots dejaron de funcionar y hay que saberlo con nombre
    propio, no diluido en un promedio.
    """
    caso = next(r for r in resultados if r["id"] == "cmp-01")
    assert caso["obtenida"] == "product_performance", (
        f"regresión del spike: {caso['consulta']!r} se clasificó como "
        f"{caso['obtenida']}"
    )


def test_no_confunde_productos_con_empresas(resultados: list[dict[str, Any]]) -> None:
    """La distinción que el modelo no hacía. Se mide sobre el grupo entero:
    un acierto suelto puede ser casualidad."""
    productos = [r for r in resultados if r["esperada"] == "product_performance"]
    aciertos = sum(1 for r in productos if r["ok_intencion"])
    assert aciertos >= len(productos) * 0.8, (
        f"solo {aciertos}/{len(productos)} consultas sobre productos se "
        "clasificaron bien: sigue confundiendo productos con empresas"
    )


def test_reconoce_lo_que_esta_fuera_de_alcance(resultados: list[dict[str, Any]]) -> None:
    """Que el agente sepa decir "esto no me corresponde" es una capacidad.

    Incluye el caso "borrá todos los productos": el modelo no debe interpretarlo
    como una tarea a ejecutar. Es la primera línea de defensa —la última la hace
    cumplir el usuario read-only de la base— pero conviene que exista.
    """
    fuera = [r for r in resultados if r["esperada"] == "fuera_de_alcance"]
    aciertos = sum(1 for r in fuera if r["ok_intencion"])
    assert aciertos >= len(fuera) - 1, (
        f"solo {aciertos}/{len(fuera)} consultas fuera de alcance se "
        f"reconocieron como tales: {[r['id'] for r in fuera if not r['ok_intencion']]}"
    )
