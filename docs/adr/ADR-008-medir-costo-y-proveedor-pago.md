# ADR-008 — Un tercer adaptador LLM y la medición de costo

- **Estado:** Aceptado
- **Fecha:** 2026-08-19

## Contexto

El eval de la Fase 5 mide **calidad**: cinco métricas determinísticas sobre 15
casos, con los umbrales fijados antes de medir y cada corrida persistida con su
commit. Es un instrumento serio y contesta bien una pregunta: *¿anda bien?*

No contesta la otra: *¿conviene?*

`eval/registro.py` no guardaba tokens, ni costo, ni latencia agregada. Con un
modelo local eso no molestaba —lo que no se factura no se discute—, pero
significa que el proyecto no podía responder la pregunta que decide si un
sistema se pone en producción: **cuánto cuesta una consulta**.

Al mismo tiempo, el puerto `ClienteLLM` tenía dos adaptadores (ADR-007) y los
dos hablaban con **el mismo backend**. `ClienteOllama` y `ClienteLangChain`
llegan a Ollama por dos caminos distintos, así que un puerto moldeado sobre los
detalles de Ollama los pasaría a los dos sin protestar. La verificación del
puerto estaba incompleta y no había forma de saberlo desde adentro.

Las dos carencias se resuelven con la misma pieza.

## Decisión

Se agrega **`ClienteAnthropic`** (`agent/llm_anthropic.py`), tercer adaptador
del mismo puerto, sobre el **SDK oficial `anthropic`** y no sobre
`ChatAnthropic` de LangChain.

```
LLM_BACKEND=httpx      → ClienteOllama      (default, sin cambios)
LLM_BACKEND=langchain  → ClienteLangChain
LLM_BACKEND=anthropic  → ClienteAnthropic   (modelo por ANTHROPIC_MODEL)
```

Y con él, tres piezas de instrumentación:

1. **`Uso`** y el puerto opcional **`ClienteLLMConUso`** (`agent/llm.py`).
   Tokens de entrada, salida, cacheados y cantidad de llamadas. Segregado del
   puerto principal por el mismo criterio que `ClienteLLMConSalud`: los nodos no
   necesitan saber de tokens.
2. **`eval/costo.py`**: tarifas fechadas y la conversión de tokens a dólares.
3. **`eval/registro.py`**: el bloque `uso` por corrida, `duracion_ms` por caso, y
   **`comparar_modelos()`**.

El default sigue siendo `httpx`. Nada de esto cambia el camino que el golden set
tiene medido.

## Justificación

**El SDK nativo y no LangChain.** El adaptador de LangChain ya existe y agregar
un proveedor ahí hubiera sido una línea. Se eligió el SDK igual por una razón
concreta: este adaptador tiene que reportar **tokens consumidos**, y el objeto
`usage` de cada respuesta —entrada, salida, lecturas de cache— el SDK lo expone
directo mientras LangChain lo normaliza y lo tapa. Medir es el objetivo del
ejercicio; no se elige la capa que esconde la medición.

**Tokens en el registro, no dólares.** Los tokens son un hecho que reporta el
proveedor y no cambia nunca. El precio es una tabla que se mueve varias veces
al año. Guardando tokens, una corrida vieja se puede recalcular con la tarifa de
hoy; guardando solo dólares, el número queda congelado contra una tabla que
nadie anotó. Por eso `Uso` vive en `agent/` y las tarifas en `eval/costo.py`,
fechadas.

**`comparar()` no se relaja.** Esa función lanza `ValueError` si los modelos
difieren, y hace bien: sin esa guarda, cambiar el modelo, ver subir el número y
atribuírselo al prompt es el error más tentador del oficio. La guarda queda
intacta. Se agrega **`comparar_modelos()`**, un camino aparte cuyo nombre
declara qué se está haciendo — que era exactamente lo que pedía el comentario
original: *para comparar modelos hay que decir que se están comparando modelos*.

La diferencia entre las dos no es técnica, es de intención. En `comparar()` el
modelo es constante y varía el código: un delta es una mejora o una regresión.
En `comparar_modelos()` varía el modelo: un delta **no es progreso, es un
trade-off**, y por eso se devuelve con el costo y la latencia al lado.

**`count_tokens` como herramienta de primera clase.** El endpoint es gratis, y
eso lo vuelve lo más útil del adaptador: permite calcular el costo exacto de una
corrida **antes** de gastar un centavo. Es la misma disciplina que ya ordena el
eval —fijar umbrales antes de medir— aplicada a la factura.

## Alcance: qué NO hace

- **No cambia el default.** `LLM_BACKEND=httpx` sigue siendo el camino medido.
- **No mete Anthropic en el grafo.** Los nodos no saben qué proveedor corre,
  igual que con los otros dos adaptadores.
- **No agrega OpenAI.** El mínimo de carga de crédito es por proveedor: un
  proveedor pago medido contra el local ya da la tabla completa, y el segundo
  cuesta el doble para agregar casi nada. Se decidió deliberadamente comprar una
  sola entrada.
- **No usa prompt caching.** Se evaluó y **no aplica**: los prefijos fijos son de
  809 tokens (router) y 774 (synthesizer), por debajo del mínimo cacheable de
  ~1024. Aplicarlo sería cargo culto — no cachearía nada, en silencio.

## Alternativas consideradas

| Alternativa | Por qué se descartó |
|---|---|
| `ChatAnthropic` sobre el adaptador LangChain existente | Casi gratis en código, pero tapa el objeto `usage` que es todo el punto del ejercicio. |
| Guardar dólares en el registro en vez de tokens | Irreproducible: en seis meses nadie sabe con qué tarifa se calculó. |
| Relajar el guard de `comparar()` para aceptar modelos distintos | Tiraría abajo la única defensa contra atribuirle al prompt una mejora del modelo. El nombre de la función es la declaración. |
| Poner `uso()` dentro de `ClienteLLM` | Obligaría a cada doble de test a implementarlo sin que ningún test lo use, y `ClienteOllama` no tiene nada que reportar. |
| Cargar el precio promocional de Sonnet 5 ($2/$10, vence 2026-08-31) | Una tabla que baquea una promoción empieza a subestimar el costo el día que vence, en silencio. Errar hacia caro es recuperable. |
| Cerrar los `ESQUEMA` de los nodos con `additionalProperties` | Cambiaría también el prompt que manda `ClienteOllama`, y el golden set dejaría de medir el mismo sistema. Se cierran en el borde, sobre una copia. |

## Consecuencias

**Positivas**

- El puerto `ClienteLLM` queda verificado contra **otro proveedor**, que es lo
  único que podía demostrar que la abstracción no estaba moldeada sobre Ollama.
- El registro pasa a poder contestar *¿cuánto cuesta una consulta?* — la
  pregunta de negocio, no la técnica.
- El costo exacto se puede predecir gratis con `count_tokens` antes de gastar.

**Negativas**

- **El régimen de sampling no se puede sostener constante entre proveedores.**
  Los otros dos adaptadores fijan temperatura 0 para clasificar y 0.3 para
  redactar. Los modelos Claude actuales **rechazan `temperature` con un 400**: el
  parámetro no existe más. Es una divergencia real, no una omisión, y significa
  que la comparación entre modelos tiene un **confound declarado**: no se varía
  solo el modelo, también el sampling. Decirlo es lo que separa una medición de
  una decoración.
- Tres caminos que mantener en vez de dos.
- Una dependencia más (`anthropic`, grupo opcional). No entra en el core.

## Notas de implementación

Verificado contra `anthropic 0.123.0`, por introspección del SDK y no por
memoria:

- La salida estructurada va en `output_config={"format": {"type":
  "json_schema", "schema": ...}}`. `JSONOutputFormatParam` exige exactamente
  esas dos claves.
- `output_config` acepta además `effort` (`low`…`max`). Se usa **`low` por
  defecto**: en Opus 5 el thinking viene encendido y sus tokens se facturan como
  salida, y nuestras dos llamadas son mecánicas —clasificar una consulta,
  redactar cinco oraciones sobre números ya calculados—. Es una hipótesis sobre
  costo/calidad, no un dogma: por eso es parámetro y un eje que el eval puede
  barrer.
- El prompt de sistema va en el parámetro `system`, **no** como un mensaje del
  array. En Ollama es un mensaje; acá es un campo aparte. Mandarlo adentro de
  `messages` no rompe nada visible: el modelo lo lee igual y clasifica un poco
  peor.
- `usage` trae `input_tokens`, `output_tokens`, `cache_read_input_tokens` y
  `cache_creation_input_tokens`. Se contabiliza en un `finally`: una respuesta
  que no cumple el esquema **igual se cobra**, y un contador que solo suma los
  éxitos subestima la factura justo en las corridas que se repiten.
- `_texto()` concatena **todos** los bloques `type == "text"` y descarta el
  resto. Con thinking activo la respuesta trae bloques `thinking` antes del
  texto; leer solo el primer bloque devolvería un informe truncado, y
  concatenar a ciegas metería el razonamiento del modelo adentro del informe.
- El doble de los tests devuelve un `anthropic.types.Message` **real**,
  construido con los modelos Pydantic del SDK. Un doble a mano acepta cualquier
  forma y no vería que el SDK renombró un campo.

## Postdata: lo medido y lo que todavía es hipótesis

**Medido**, reconstruyendo los prompts reales del agente con las métricas
capturadas en `docs/replay/data/casos/`: **~2.346 tokens de entrada y ~133 de
salida por consulta**. Una estimación previa a ojo daba 10.000 de entrada —
erró por 4x. Por eso se mide.

De ahí sale el número que vuelve al proyecto defendible en una conversación de
negocio: **USD 0,0151 por consulta en Opus 5**, USD 0,0030 en Haiku 4.5. El
golden set completo cuesta USD 0,23 en el modelo más caro.

Y el 94% de esos tokens son de **entrada**, no de salida. Eso no es casualidad:
es el principio rector del sistema —al LLM no se le delega lo que resuelve el
software— visible en la factura. La arquitectura es barata porque es correcta.

**Todavía hipótesis**, hasta que haya una `ANTHROPIC_API_KEY`:

- Que la API acepte los esquemas cerrados por `_cerrar()`.
- Que `effort: "low"` no degrade ninguna métrica del golden set.
- El conteo exacto de tokens (lo de arriba supone 3,5 chars/token).

Los tres los contestan los tests marcados `llm` de
`tests/test_agent_llm_anthropic.py`, y **el primero de ellos no consume tokens**:
`count_tokens` es gratis. Es el que hay que correr primero.

Los dobles no validan la API del framework. Ya nos costó una sesión aprenderlo
con `bind(num_predict=...)` —21 unitarios en verde y un `TypeError` contra
Ollama real— y no hay razón para creer que un SDK distinto sea más indulgente.
