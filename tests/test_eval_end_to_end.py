"""Evaluación de punta a punta: ¿el informe es bueno, no solo verificable?

El eval del router mide si el agente **entiende**. Este mide si **responde
bien**, que es otra cosa y hasta ahora no se medía en ninguna parte.

Nace de un riesgo que el ADR-003 dejó abierto y por escrito: la auditoría
numérica dio 22 de 22 cifras verificadas, cero inventadas, y el informe seguía
recomendando sobre el producto equivocado, llamándole precisión a un error y
dejando sin usar dos documentos que explicaban la anomalía.

    "Un validador numérico es necesario y no es suficiente."

**El oráculo es `dbo.ground_truth`**, la tabla de eventos sembrados que el
usuario del agente tiene prohibido leer. El eval entra por `conectar_admin`. Que
el evaluador use otra puerta es lo que vuelve válida la medición.

**Los umbrales están fijados antes de la primera corrida**, y esa es la regla de
la casa: si se mide primero y se decide después, el umbral se acomoda al número
que salió. Eso no es evaluar, es justificar. Son normativos —lo que se considera
aceptable— y no descriptivos. Que la primera corrida falle es información sobre
el sistema, no un defecto del eval.

Correr solo estos:   uv run pytest -m llm -v -s
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import pytest

from agent.graph import analizar
from agent.llm import ClienteOllama
from core.db import hay_base_disponible
from core.report import Report
from eval.ground_truth import (
    casos_de_evaluacion,
    consulta_con_proyeccion,
    consulta_para,
    leer_eventos,
)
from eval.metricas import (
    EventoSembrado,
    Hallazgo,
    Proporcion,
    evaluar,
    resumir,
)
from eval.registro import documento_de_corrida, guardar, procedencia

# Lo que devuelve cada consulta del golden set: el evento sembrado, su clase,
# el informe (None si el agente no produjo ninguno) y los hallazgos medidos.
Corrida = tuple[EventoSembrado, str, Report | None, list[Hallazgo]]

pytestmark = [pytest.mark.slow, pytest.mark.llm, pytest.mark.db]

# Fijados ANTES de medir. No se tocan para que un resultado "casi" pase.
#
# El umbral de `usa_la_evidencia_documental` sale de que ignorar la evidencia
# desobedece una instrucción explícita del prompt (regla 7). No se eligió mirando
# ningún resultado.
#
# Hubo un sexto umbral, `no_declara_documentos_sin_usar` en 0.75, retirado el
# 2026-08-12 junto con su métrica. No se bajó para que pasara: se eliminó porque
# medía el recall del RAG creyendo medir el rigor del informe. El motivo largo
# está en la docstring de `_usa_la_evidencia_documental`.
UMBRALES = {
    "analiza_el_producto_del_evento": 0.80,
    "atribuye_al_producto_correcto": 0.90,
    "reporta_magnitudes_absolutas": 0.75,
    "usa_la_evidencia_documental": 0.90,
    "no_invierte_el_sentido_del_error": 0.90,
}

# Cada caso invoca el grafo completo: entre 70 y 130 segundos en esta CPU, más el
# primero, que carga los embeddings en frío.
#
# Se pasó de 6 a 12 —todos los casos únicos que existen— después de medir con
# seis y quedarse sin poder concluir. Con `usa_la_evidencia_documental` en 3 de
# 6, la diferencia entre "el sistema falla la mitad de las veces" y "salió así"
# no se distingue: el intervalo es demasiado ancho. Y las proporciones por tipo
# de evento se apoyaban en 2 y 1 caso.
#
# El costo es media hora en vez de un cuarto. Es mucho, y sigue siendo menos que
# volver a discutir si un 50% era real.
MAX_CASOS = 12

# Casos que además piden una proyección, uno por tipo de evento.
#
# `no_invierte_el_sentido_del_error` estuvo cuatro corridas marcada NUNCA
# APLICÓ. No era el agente: el planner solo planifica `forecast_sales` cuando la
# consulta pide una proyección, y `consulta_para` no la pide. El agente hacía lo
# correcto y la métrica juzgaba un informe que no podía existir.
#
# Van como casos APARTE y no cambiando el enunciado de los 12: cambiar la
# pregunta invalidaría como referencia la única corrida buena que hay. El costo
# real es la síntesis; el forecast en sí tarda 0,09 s.
CASOS_CON_PROYECCION = 3


@pytest.fixture(scope="module")
def cliente() -> ClienteOllama:
    c = ClienteOllama()
    if not c.disponible():
        pytest.skip("Ollama no responde: levantalo con `ollama serve`")
    return c


@pytest.fixture(scope="module")
def procedencia_inicial() -> dict[str, Any]:
    """Con qué código se mide, capturado ANTES de la primera consulta.

    Tomarla al final describiría un árbol que el propio archivo de registro ya
    ensució, y la corrida quedaría marcada irreproducible por su propia salida.
    """
    return procedencia()


@pytest.fixture(scope="module")
def corridas(cliente: ClienteOllama, procedencia_inicial: dict[str, Any]) -> list[Corrida]:
    """Interroga al agente sobre cada evento sembrado, una sola vez.

    Sin `scope="module"` cada test volvería a correr el grafo y la evaluación
    pasaría de diez minutos a una hora.
    """
    if not hay_base_disponible():
        pytest.skip("SQL Server no responde: levantalo con `.\\tasks.ps1 db-up`")

    # Deduplicar ANTES de cortar por MAX_CASOS, no después: los eventos 3 y 4 de
    # la corrida del 2026-08-12 eran la misma consulta y ocuparon dos de los seis
    # lugares. Cortar primero deja el duplicado adentro y a un caso distinto
    # afuera.
    eventos = casos_de_evaluacion(leer_eventos())[:MAX_CASOS]
    if not eventos:
        pytest.skip("no hay eventos sembrados: corré `.\\tasks.ps1 seed`")

    # Un caso de proyección por tipo de evento, tomando el primero de cada uno.
    # Repartirlos por tipo y no agarrar los tres primeros evita que la única
    # medición del forecast salga toda del mismo tipo de anomalía.
    con_proyeccion = []
    for tipo in dict.fromkeys(e.tipo for e in eventos):
        primero = next(e for e in eventos if e.tipo == tipo)
        con_proyeccion.append(primero)
        if len(con_proyeccion) == CASOS_CON_PROYECCION:
            break

    casos = ([(e, "analisis", consulta_para(e)) for e in eventos]
             + [(e, "proyeccion", consulta_con_proyeccion(e))
                for e in con_proyeccion])

    from rag.build import cargar_indice
    indice = cargar_indice()

    resultados: list[Corrida] = []
    for i, (evento, clase, consulta) in enumerate(casos, 1):
        # El progreso se imprime porque cada caso tarda más de un minuto. Diez
        # minutos de silencio no se distinguen de un proceso colgado, y la
        # reacción natural es cortarlo.
        print(f"  [{i}/{len(casos)}] {evento.tipo:<20} {evento.product_id} "
              f"{evento.fecha}  {clase}", flush=True)
        arranque = time.perf_counter()

        estado = analizar(
            consulta, cliente,
            request_id=f"eval-{clase}-{evento.tipo}-{evento.product_id}",
            indice=indice,
        )
        print(f"        {time.perf_counter() - arranque:>6.1f}s", flush=True)

        if estado.informe is None:
            # Sin informe no hay nada que medir, y contarlo como aprobado
            # inflaría todas las métricas. Se registra el hueco.
            resultados.append((evento, clase, None, []))
            continue
        resultados.append(
            (evento, clase, estado.informe, evaluar(estado.informe, evento)))

    return resultados


def _rotulo(cumple: bool | None) -> str:
    """Tres estados, tres rótulos. `n/a` NO es un aprobado."""
    return {True: "PASA", False: "FALLA", None: "n/a"}[cumple]


@pytest.fixture(scope="module")
def resumen(corridas: list[Corrida], procedencia_inicial: dict[str, Any]) -> dict[str, Proporcion]:
    logrados = [h for *_, informe, h in corridas if informe is not None]

    print("\n" + "=" * 78)
    print("  CALIDAD DEL INFORME CONTRA LOS EVENTOS SEMBRADOS")
    print("=" * 78)
    for evento, clase, informe, hallazgos in corridas:
        estado = "sin informe" if informe is None else ""
        print(f"\n  {evento.tipo:<20} {evento.product_id}  {evento.fecha}  "
              f"[{clase}] {estado}")
        for h in hallazgos:
            print(f"     {_rotulo(h.cumple):<6} {h.nombre:<36} {h.detalle[:70]}")

    proporciones = resumir(logrados)
    print("\n" + "-" * 78)
    print(f"  {'métrica':<36} {'valor':>7}  {'aplicó':>8}   umbral")
    for nombre, umbral in UMBRALES.items():
        p = proporciones.get(nombre)
        if p is None or p.valor is None:
            cobertura = f"0/{p.total}" if p else "—"
            # Sin cobertura no hay porcentaje que mostrar. Un guion dice la
            # verdad; un 100% diría que salió perfecto sin haber medido nada.
            print(f"  {nombre:<36} {'—':>7}  {cobertura:>8}   "
                  f"({umbral:.0%})  NUNCA APLICÓ")
            continue
        print(f"  {nombre:<36} {p.valor:>7.0%}  "
              f"{f'{p.aplicables}/{p.total}':>8}   ({umbral:.0%})  "
              f"{'ok' if p.valor >= umbral else 'POR DEBAJO'}")
    print("-" * 78 + "\n")

    # El registro va acá y no dentro de un test: los tests de umbral pueden
    # fallar, y una corrida que falla es justamente la que hay que conservar.
    archivo = guardar(documento_de_corrida(
        corridas=corridas, proporciones=proporciones, umbrales=UMBRALES,
        generado_en=datetime.now(), procedencia_inicial=procedencia_inicial,
    ))
    print(f"  corrida registrada en {archivo}\n")

    return proporciones


def test_todas_las_consultas_produjeron_un_informe(corridas: list[Corrida]) -> None:
    """Antes de juzgar la calidad hay que tener qué juzgar.

    Si el agente no llega a producir informe, las métricas de calidad no se
    calculan sobre nada y un promedio alto sobre dos casos no dice lo mismo que
    sobre seis.
    """
    sin_informe = [f"{e.product_id} [{clase}]"
                   for e, clase, informe, _ in corridas if informe is None]

    assert not sin_informe, f"el agente no produjo informe para {sin_informe}"


@pytest.mark.parametrize("nombre,umbral", sorted(UMBRALES.items()))
def test_la_metrica_alcanza_su_umbral(
    resumen: dict[str, Proporcion], nombre: str, umbral: float,
) -> None:
    p = resumen.get(nombre)
    assert p is not None, f"la métrica '{nombre}' no se calculó en ninguna corrida"

    if p.valor is None:
        # No se aprueba ni se reprueba: no hubo nada que juzgar. Un `skip` deja
        # el hueco a la vista con su motivo; devolver verde lo escondería, que
        # es el error que esta versión vino a corregir.
        pytest.skip(
            f"'{nombre}' no aplicó en ninguna de las {p.total} corridas: "
            f"el agente nunca produjo la salida que esta métrica juzga"
        )

    assert p.valor >= umbral, (
        f"{nombre}: {p.valor:.0%} sobre {p.aplicables}/{p.total} corridas "
        f"aplicables, contra un umbral de {umbral:.0%}. "
        f"El umbral se fijó antes de medir y no se baja para que pase."
    )
