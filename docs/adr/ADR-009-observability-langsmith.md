# ADR-009 — Observability con LangSmith, y la excepción al costo cero

- **Estado:** Aceptado
- **Fecha:** 2026-08-26

## Contexto

El sistema ya tiene tracing casero: ADR-001 dice *"el tracing por nodo sale
casi gratis y alimenta directamente los evals"*, y eso es lo que hoy alimenta
`docs/replay/` — la traza etapa por etapa, con duración real de cada nodo y
el criterio de selección de herramientas, sin ningún servicio externo.

Lo que ese sistema casero NO da es lo que da una plataforma de observability
dedicada: comparar corridas entre sí en una UI, ver el árbol completo de un
análisis sin abrir un JSON, o inspeccionar el prompt exacto que recibió el
modelo en una llamada puntual sin instrumentar nada a mano para el caso.

`langsmith` además ya viajaba **transitiva** de `langchain-core`, sin un solo
`import` en el repo — el mismo problema que ADR-007 corrigió con `langchain`
antes de este ADR: una dependencia de polizón no demuestra nada y sugiere que
se puso por el nombre.

## Decisión

Se instrumenta el agente con **LangSmith** (SaaS de LangChain), en dos capas:

1. **Estructura del grafo — gratis, sin tocar código.** Con
   `LANGSMITH_TRACING=true` y `LANGSMITH_API_KEY` seteadas, cada
   `grafo.invoke(...)` ya emite un run raíz con un span por nodo. LangGraph
   corre sobre el runtime de `langchain-core`, así que esto sale solo,
   **sin importar qué backend de LLM esté activo**.
2. **Detalle de la llamada al modelo — `@traceable`, tres líneas por
   adaptador.** Solo en los adaptadores que NO pasan por LangChain
   (`ClienteOllama`, `ClienteAnthropic`): sin esto, LangSmith vería el nodo
   como una caja negra sin el prompt real ni la respuesta cruda.
   `ClienteLangChain` queda sin decorar a propósito — ya lo traza
   `ChatOllama` por sus propios callbacks, y decorar encima duplicaría el
   span en vez de mejorarlo.

Apagado por default (`LANGSMITH_TRACING=false` en `env.example`): sin esa
variable en `true`, `@traceable` es no-op — no pega a la red, no cambia el
camino que mide el golden set, costo cero real.

## Justificación: por qué se rompe el "costo cero"

El README dice *"Costo cero. No se usan APIs pagas, bases vectoriales SaaS ni
cloud obligatorio"*. LangSmith lo contradice en la letra: es un SaaS, y su
free tier (5.000 trazas/mes, retención de 14 días) **exige tarjeta de
crédito en archivo** para seguir ingiriendo una vez agotado — el mismo tipo
de riesgo que ADR-008 usa para no depender de un proveedor pago como
default.

La diferencia con lo que ADR-008 ya aceptó (el adaptador Anthropic) es la
misma: **medir es acotado, servir no lo es.** LangSmith no está en el camino
que responde a un usuario — es una lente que se prende para depurar o
demostrar, y se apaga. Si el free tier cambia mañana, el sistema sigue
funcionando exactamente igual: `LANGSMITH_TRACING=false` y el agente no
sabe que la plataforma existe.

## Alcance: qué NO hace

- **No reemplaza el replay.** `docs/replay/` sigue siendo la forma de
  mostrar el sistema sin instalar nada y sin depender de una cuenta externa
  — es la que ve un recruiter. LangSmith es la herramienta de quien
  desarrolla, no la vidriera del portfolio.
- **No cambia qué mide el golden set.** El eval sigue corriendo con
  `LANGSMITH_TRACING=false`; instrumentar la medición con la misma
  herramienta que se está midiendo sería mezclar el instrumento con el
  objeto (la lección que ya está escrita en ADR-003).
- **No reporta costo/tokens todavía.** `Uso` (en `agent/llm.py`) sigue
  siendo la única fuente de verdad para lo que consume una corrida — es lo
  que usa `eval/costo.py`. Mapear `Uso` al `usage_metadata` de LangSmith
  queda abierto como mejora futura, no como parte de este ADR: son dos
  fuentes de verdad para el mismo número si se hace a medias.

## Alternativas consideradas

| Alternativa | Por qué se descartó |
|---|---|
| Langfuse self-hosted | Es la opción que no rompe el costo cero: Docker Compose local, cero SaaS, mismo enganche con LangGraph vía callbacks de LangChain. Se descartó para ESTA fase porque la decisión fue explícitamente usar LangSmith real, con el trade-off asumido — Langfuse queda como la alternativa a la que volver si el free tier deja de alcanzar. |
| No instrumentar nada, quedarse con el replay | Honesto y ya funciona. Se descartó porque el replay muestra corridas grabadas, no compara corridas entre sí ni deja inspeccionar un prompt puntual sin haber previsto ese caso al grabar. |
| Decorar también `ClienteLangChain` | Duplicaría el span: `ChatOllama` ya emite su propio tracing vía callbacks de `langchain-core` en cuanto `LANGSMITH_TRACING=true` está activo. Envolverlo en `@traceable` además anidaría dos runs por la misma llamada. |

## Consecuencias

**Positivas**
- Visibilidad completa del grafo (estructura) sin tocar código, en
  cualquier backend.
- Visibilidad del prompt/respuesta real en los dos adaptadores que no pasan
  por LangChain, con tres líneas de cambio cada uno.
- La dependencia `langsmith` pasa de transitiva-sin-uso a declarada y
  ejercitada.

**Negativas**
- Primera dependencia real del proyecto en un servicio SaaS de pago. El
  README necesita la nota de excepción (hecha en este ADR) para que
  "costo cero" siga siendo una afirmación verificable y no un eslogan.
- Un tercer lugar que puede desincronizarse: si se agrega un cuarto
  adaptador del puerto `ClienteLLM`, hay que decidir de nuevo si necesita
  `@traceable` o lo saca gratis de LangChain — no es automático.
- Reportado en la comunidad (langsmith-sdk#1306): en algunas versiones un
  error interno de instrumentación puede propagarse y afectar código de
  aplicación. No verificado contra la versión fijada en este repo; si
  aparece, se revisa a la mano, no se apaga tracing en producción sin mirar
  antes qué pasó.
