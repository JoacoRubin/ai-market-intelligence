"""Recorrido del agente completo, con el modelo real.

De una consulta en lenguaje natural a un PDF descargable, pasando por las seis
etapas del grafo. Es lento —usa `llama3.2:3b` en CPU— y por eso vive acá y no
en la suite de tests.

Se ejecuta con:  .\\tasks.ps1 agente
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

from agent.graph import analizar
from agent.llm import ClienteOllama
from core.report_pdf import render_pdf
from rag.build import cargar_indice

if os.name == "nt":
    os.system("")

VERDE, AZUL, GRIS, AMARILLO, FIN = (
    "\033[92m", "\033[94m", "\033[90m", "\033[93m", "\033[0m")

HOY = date(2026, 3, 31)
DESTINO = Path(__file__).resolve().parent.parent / "docs" / "ejemplos"

CONSULTAS = [
    "Compará P002 y P003 en los últimos 90 días",
    "Contame un chiste",
]


def main() -> int:
    cliente = ClienteOllama()
    if not cliente.disponible():
        print(f"{AMARILLO}Ollama no responde. Levantalo con `ollama serve`.{FIN}")
        return 1

    consulta = " ".join(sys.argv[1:]) or CONSULTAS[0]

    print()
    print(f"  {'=' * 74}")
    print("  AGENTE DE ANÁLISIS COMERCIAL")
    print(f"  {'=' * 74}")
    print(f"  {GRIS}modelo: {cliente.nombre} · inferencia en CPU{FIN}")
    print(f"\n  {AZUL}Consulta:{FIN} {consulta}\n")
    print(f"  {GRIS}Ejecutando el grafo... (puede tardar en CPU){FIN}\n")

    indice = cargar_indice()
    if indice is None:
        print(f"  {AMARILLO}Sin indice documental: corre .\tasks.ps1 rag-build{FIN}")
    estado = analizar(consulta, cliente, request_id="demo-001", hoy=HOY,
                      indice=indice)

    print(f"  {AZUL}Interpretación{FIN}")
    print(f"    intención  : {estado.intencion.value if estado.intencion else '—'}")
    print(f"    productos  : {estado.entidades or '—'}")
    if estado.periodo:
        print(f"    período    : {estado.periodo.desde} a {estado.periodo.hasta}")

    print(f"\n  {AZUL}Plan{FIN}")
    for paso in estado.plan or []:
        print(f"    · {paso.tool}: {paso.razon}")
    if not estado.plan:
        print(f"    {GRIS}(sin plan: la consulta no requiere herramientas){FIN}")

    print(f"\n  {AZUL}Trace{FIN}")
    for p in estado.trace:
        etiqueta = f"{p.nodo}" + (f" ({p.tool})" if p.tool else "")
        print(f"    {etiqueta:<28}{p.duracion_ms:>7} ms")
    print(f"    {'TOTAL':<28}{estado.duracion_total_ms:>7} ms")
    print(f"    {GRIS}llamadas a herramientas: {estado.llamadas_tools}"
          f"/{estado.max_llamadas_tools} · replanificaciones: "
          f"{estado.reintentos}/{estado.max_reintentos}{FIN}")

    if estado.informe is None:
        print(f"\n  {AMARILLO}Sin informe.{FIN}")
        for w in estado.advertencias:
            print(f"    ! {w}")
        print()
        return 0

    informe = estado.informe
    print(f"\n  {AZUL}Métricas (calculadas por SQL, no por el modelo){FIN}")
    for m in informe.metricas:
        print(f"    {m.nombre} ({m.product_id}): {m.unidades:,} unidades · "
              f"USD {m.revenue:,.2f}"
              + (f" · margen {m.margen_pct:.1f}%" if m.margen_pct else ""))

    print(f"\n  {AZUL}Conclusiones (redactadas por el modelo, validadas por software){FIN}")
    for a in informe.resumen_ejecutivo:
        print(f"    · {a.texto}")
        print(f"      {GRIS}[{', '.join(a.fuentes)}]{FIN}")

    if informe.advertencias:
        print(f"\n  {AMARILLO}Advertencias{FIN}")
        for w in informe.advertencias:
            print(f"    ! {w}")

    ruta = render_pdf(informe, DESTINO / "informe_agente.pdf")
    print(f"\n  {VERDE}PDF generado:{FIN} {ruta}")
    print(f"  {GRIS}modelo declarado en el informe: {informe.modelo_llm}{FIN}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
