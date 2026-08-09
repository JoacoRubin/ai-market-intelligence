# ADR-001 — Por qué LangGraph, y cuándo NO usarlo

- **Estado:** Aceptado
- **Fecha:** 2026-08-09

## Contexto

El sistema debe responder consultas de análisis comercial combinando cuatro
fuentes heterogéneas: métricas en SQL Server, evidencia documental vía RAG,
datos públicos (SEC EDGAR, World Bank) y predicciones de modelos de ML.

El flujo no es lineal. Requiere:

- **Estado tipado compartido** entre etapas (`AnalysisState`: intent, entities,
  plan, tool_results, evidence, ml_results, warnings, retry_count, trace_id).
- **Ramificación condicional**: qué tools ejecutar depende de la intención
  detectada y del período solicitado.
- **Loops acotados**: si la evidencia recolectada es insuficiente, replanificar
  — con un máximo estricto de 2 reintentos.
- **Trazabilidad por etapa**: qué tool se llamó, con qué argumentos, cuánto
  tardó y qué devolvió.

Además, la restricción de hardware (inferencia CPU-only, ver ADR-003) hace que
cada llamada al LLM cueste segundos reales. Un flujo que llama al modelo de más,
o que entra en un loop no acotado, no es un problema de elegancia: es un
producto inutilizable.

## Decisión

Se usa **LangGraph** para orquestar el agente.

El grafo es explícito y sus nodos son: `IntentRouter` → `PlanBuilder` → tools
(`SQL`, `RAG`, `Research`, `ML`) → `EvidenceGate` → `Synthesizer` →
`ReportValidator`.

El estado se define con un tipo explícito y validado, no con diccionarios sueltos.

## Justificación

La complejidad real del flujo (estado, ramas, loops acotados, reintentos,
trazabilidad) es exactamente el problema que un grafo de estados resuelve bien.
Implementarlo a mano significaría reescribir peor lo mismo.

**La arquitectura justifica el framework, no al revés.** Si la V1 fuera una sola
llamada a una tool seguida de una respuesta, LangGraph sería sobreingeniería y
correspondería una función con dos `if`.

## Cuándo NO usarlo

Este ADR existe tanto para justificar la elección como para marcar sus límites.
**No** corresponde LangGraph si:

- El flujo es una sola tool call seguida de una respuesta.
- No hay estado que sobreviva entre pasos.
- No hay ramificación ni reintentos.
- El pipeline es puramente lineal y determinístico (ahí va un script, no un agente).

## Alternativas consideradas

| Alternativa | Por qué se descartó |
|---|---|
| Orquestación a mano (funciones + `if`) | Viable para la V1, pero habría que reimplementar estado tipado, reintentos acotados y tracing. Se termina escribiendo un LangGraph peor. |
| CrewAI / AutoGen | Orientados a colaboración multi-agente. Acá no hay múltiples agentes conversando: hay **un** flujo con herramientas. Sumarlos sería agregar keywords sin resolver un problema real. |
| Agente ReAct autónomo sin grafo | Menos control sobre el número de llamadas al LLM. Con inferencia CPU-only eso es inaceptable, y además un agente sin límites es más difícil de defender que uno controlado. |

## Consecuencias

**Positivas**
- El estado del análisis es inspeccionable en cualquier punto del flujo.
- Los límites (`max_iterations`, `max_tool_calls`) son explícitos y testeables.
- El tracing por nodo sale casi gratis y alimenta directamente los evals.

**Negativas**
- Dependencia de un framework en evolución rápida; las versiones se fijan en
  `uv.lock`.
- Curva de aprendizaje del modelo de grafos y del manejo de estado.
- Riesgo de sobreingeniería si el proyecto se simplificara: por eso está escrita
  arriba la sección "Cuándo NO usarlo".
