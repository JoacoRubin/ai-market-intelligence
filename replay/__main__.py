"""Captura las ejecuciones del replay corriendo el agente de verdad.

    .\\tasks.ps1 replay

Necesita SQL Server levantado y Ollama respondiendo, y tarda **minutos**: cada
caso invoca al modelo dos veces y la inferencia en CPU cuesta entre 12 y 41
segundos por llamada. Es exactamente el costo que el replay existe para que el
visitante no pague.

Se corre a mano, cuando cambia el modelo, el prompt o el dataset. No va en CI:
un pipeline que espera minutos por una inferencia local es un pipeline que
termina desactivado.
"""

from __future__ import annotations

import io
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.graph import analizar
from agent.llm import ClienteLLM, crear_cliente
from replay.captura import Captura
from replay.casos import CasoGolden, casos_para_replay
from replay.entorno import problemas_de_entorno
from replay.escritura import escribir
from replay.procedencia import capturar_procedencia

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "docs" / "replay" / "data"

if os.name == "nt":
    # Habilita las secuencias ANSI en la consola de Windows.
    os.system("")

    # Y fuerza UTF-8 en la salida. Sin esto `print` usa la codificación de la
    # consola —`cp1252` en cuanto la salida se redirige a un archivo o a un
    # pipe— y la flecha de `etapas` (U+2192) revienta con UnicodeEncodeError.
    #
    # El costo no es el error: es CUÁNDO ocurre. La excepción salta DESPUÉS de
    # correr el caso, o sea que se pagan los minutos de inferencia y no se
    # escribe una sola captura. Pasó el 2026-08-28: la corrida murió en el
    # `print` del caso 1 de 5, con el análisis ya hecho y tirado a la basura.
    #
    # `errors="replace"` para que un carácter raro degrade el mensaje en vez de
    # matar la corrida: este harness cuesta minutos y su salida es informativa.
    # Ningún adorno de consola vale una captura perdida.
    for flujo in (sys.stdout, sys.stderr):
        if isinstance(flujo, io.TextIOWrapper):
            flujo.reconfigure(encoding="utf-8", errors="replace")

VERDE, AZUL, GRIS, ROJO, FIN = "\033[92m", "\033[94m", "\033[90m", "\033[91m", "\033[0m"


def capturar(
    caso: CasoGolden, cliente: ClienteLLM, *, indice: Any = None
) -> Captura:
    """Corre el grafo completo sobre un caso y congela el resultado.

    `indice` va como `Any` porque el grafo lo recibe sin tipar: el índice vive en
    el grupo opcional `rag`, y tiparlo acá obligaría a importar torch para correr
    el harness sin RAG.
    """
    estado = analizar(
        caso.consulta, cliente, request_id=f"replay-{caso.id}", indice=indice
    )
    return Captura.desde_estado(
        caso.id, estado, capturada_en=datetime.now(), modelo_llm=cliente.nombre
    )


def main() -> int:
    cliente = crear_cliente()

    # Se verifica TODO antes de gastar el primer minuto de CPU. Un entorno
    # incompleto no falla: produce capturas plausibles y vacías, que tardan lo
    # mismo en generarse y que se ven bien hasta que alguien las lee.
    problemas = problemas_de_entorno(cliente)
    if problemas:
        print()
        print(f"{ROJO}  No se puede capturar todavía:{FIN}")
        for p in problemas:
            print(f"{ROJO}   · {p}{FIN}")
        print()
        return 1

    # El índice es opcional a propósito: sin él el planner no agenda RAG y los
    # casos híbridos salen sin evidencia documental. Se avisa en vez de fallar,
    # porque una captura sin RAG sigue siendo válida — solo muestra menos.
    try:
        from rag.build import cargar_indice
        indice = cargar_indice()
    except Exception as e:
        print(f"{ROJO}  Sin índice RAG ({type(e).__name__}): "
              f"los casos híbridos saldrán sin citas documentales.{FIN}")
        indice = None

    casos = casos_para_replay()
    inicio = datetime.now()
    procedencia = capturar_procedencia(RAIZ)

    print()
    print(f"  {'=' * 76}")
    print(f"  CAPTURA DE EJECUCIONES PARA EL REPLAY  {GRIS}modelo: "
          f"{cliente.nombre}{FIN}")
    print(f"  {'=' * 76}\n")

    capturas: list[Captura] = []
    for i, caso in enumerate(casos, 1):
        print(f"  {AZUL}[{i}/{len(casos)}]{FIN} {caso.id:<10}{GRIS}"
              f"{caso.consulta[:52]}{FIN}", flush=True)
        captura = capturar(caso, cliente, indice=indice)
        capturas.append(captura)

        etapas = " → ".join(p.nodo for p in captura.trace)
        print(f"           {VERDE}{captura.duracion_total_ms / 1000:>6.1f}s{FIN}  "
              f"{GRIS}{etapas or 'sin trace'}{FIN}")
        if captura.error:
            print(f"           {ROJO}{captura.error}{FIN}")
        print()

    manifiesto = escribir(
        capturas,
        destino=DESTINO,
        capturado_en=inicio,
        procedencia=procedencia,
    )

    total_s = sum(c.duracion_total_ms for c in capturas) / 1000
    con_informe = sum(1 for c in capturas if c.informe is not None)
    print(f"  {'-' * 76}")
    print(f"  {manifiesto.total} capturas · {con_informe} con informe · "
          f"{total_s:.0f}s de cómputo congelados")
    print(f"  {GRIS}{DESTINO}{FIN}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
