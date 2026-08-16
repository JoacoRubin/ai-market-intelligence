# ADR-007 — Dos adaptadores para un mismo puerto LLM

- **Estado:** Aceptado
- **Fecha:** 2026-08-15

## Contexto

`agent/llm.py` define `ClienteLLM`: un `Protocol` de dos métodos —`estructurado`
y `redactar`— que es todo lo que los nodos del grafo necesitan de un modelo.

Ese puerto existe por una razón concreta y medida: en esta máquina una llamada
al LLM tarda entre 12 y 41 segundos (ADR-003). Sin la interfaz inyectada, cada
test del grafo invocaría a Ollama y la suite pasaría de segundos a media hora.
Una suite de media hora es una suite que nadie corre, y ahí se termina el TDD.

Hasta ahora el puerto tenía **una sola** implementación real, `ClienteOllama`,
que arma el POST a `/api/chat` con `httpx`. Y un puerto con un solo adaptador no
es un puerto verificado: es la hipótesis de que la abstracción sirve, sin la
prueba.

Además, `langchain` y `langchain-ollama` estaban declaradas en `pyproject.toml`
sin un solo `import` en el repositorio. Eso es peor que no tenerlas: no
demuestra nada y, a quien abre el archivo, le sugiere que se pusieron por el
nombre. Justo lo contrario del criterio que fija ADR-001 —*la arquitectura
justifica el framework, no al revés*.

## Decisión

Se agrega **`ClienteLangChain`** (`agent/llm_langchain.py`), un segundo
adaptador del mismo puerto que delega en `ChatOllama`.

Los dos conviven. La elección es de configuración, no de código:

```
LLM_BACKEND=httpx      → ClienteOllama     (default)
LLM_BACKEND=langchain  → ClienteLangChain
```

`crear_cliente()` es el único lugar que decide. Ni los nodos, ni la API, ni el
harness de replay saben cuál está corriendo.

**El default sigue siendo `httpx`.** Es el camino que el golden set tiene
medido, y cambiarlo movería el sistema evaluado a cambio de nada.

## Justificación

**Verifica el puerto.** Dos adaptadores que pasan los mismos tests de contrato
prueban que `ClienteLLM` abstrae lo que dice abstraer. Con uno solo, la
abstracción podía estar moldeada sobre los detalles de Ollama sin que nadie lo
notara.

**Abre la puerta a otros proveedores.** `with_structured_output` y la interfaz
de mensajes son las mismas para `ChatAnthropic` o `ChatOpenAI`. Cambiar de
proveedor pasa a ser una línea en `_chat_ollama`, no una reescritura.

**Convierte una dependencia declarada en una dependencia usada.** El framework
ahora resuelve un problema real y acotado —traducir dos llamadas— en vez de
figurar en un archivo de configuración.

## Alcance: qué NO hace

Esto es tan importante como lo anterior.

- **LangChain no entra al grafo.** La orquestación es de LangGraph y las
  decisiones son de los nodos (ADR-001). Este adaptador traduce una llamada al
  modelo y nada más.
- **No se usa `create_agent`** ni ningún agente ReAct prearmado. ADR-001 lo
  descartó explícitamente: con inferencia CPU-only, ceder el control sobre
  cuántas veces se llama al modelo es inaceptable.
- **No se usan los loaders, splitters ni retrievers de LangChain.** El corpus de
  RAG es generado por `rag/corpus.py`, no parseado: ya se sabe dónde empieza y
  termina cada chunk. Esas piezas existen para domar documentos del mundo real,
  que acá no hay.

## Alternativas consideradas

| Alternativa | Por qué se descartó |
|---|---|
| Sacar `langchain` y `langchain-ollama` del `pyproject.toml` | Honesto y defendible: era la otra opción real. Se prefirió el adaptador porque además de limpiar la dependencia, verifica el puerto y deja preparada la salida multi-proveedor. |
| Reemplazar `ClienteOllama` por el adaptador | Perdería el camino sin framework, que son ~40 líneas y es el que mide el golden set. Tener los dos es justamente lo que prueba que el puerto sirve. |
| Dejar las dependencias declaradas sin usar | El peor de los dos mundos: no demuestra competencia y sugiere lo contrario a quien lee el `pyproject.toml`. |
| Usar `format=esquema` vía `bind()` en vez de `with_structured_output` | Más fácil de testear con los dobles de `langchain-core`, pero `format` es específico de Ollama: el adaptador quedaría atado al mismo proveedor que ya tenemos, y se pierde el principal beneficio. |

## Consecuencias

**Positivas**
- El puerto `ClienteLLM` queda verificado por dos implementaciones.
- Cambiar de proveedor es configuración más un constructor, no una refactorización.
- Las dependencias de LangChain pasan a estar ejercitadas por tests.

**Negativas**
- Dos caminos que mantener. Si divergen —por ejemplo, en temperatura— el golden
  set deja de medir lo que cree medir. Las constantes están compartidas y
  comentadas justamente por eso.
- `FakeListChatModel` de `langchain-core` no implementa `with_structured_output`
  (verificado en 1.5.3): el doble del chat model hay que escribirlo a mano.
- Superficie de API en evolución rápida. Las versiones se fijan en `uv.lock`.

## Notas de implementación

Verificado contra `langchain-ollama 1.1.0` y `langchain-core 1.5.3`:

- `with_structured_output(esquema, method="json_schema")` acepta un JSON Schema
  crudo — el mismo dict que ya se pasaba en `format`. Se usa `json_schema` y no
  `json_mode` porque el primero obliga al modelo por gramática; el segundo solo
  pide "un JSON", y el esquema deja de ser garantía para volverse sugerencia.
- Se lee `AIMessage.text`, no `.content`: en `langchain-core` 1.x el contenido
  puede venir como lista de bloques y `.content` la devolvería tal cual.
- `ChatOllama` se construye con `validate_model_on_init=False`. Con `True`, el
  constructor hace I/O contra la red, y un constructor que hace I/O no se puede
  instanciar en un test.
- Hay dos instancias de `ChatOllama` y no una porque clasificar y redactar
  corren a temperaturas distintas, y `with_structured_output` se pide sobre el
  modelo: `bind()` devuelve un `RunnableBinding`, que ya no lo expone.
- El techo de tokens va como `bind(options={...})`. `_chat_params` solo
  intercepta la clave `options`; cualquier otro kwarg cae al cliente de Ollama y
  revienta con `TypeError: Client.chat() got an unexpected keyword argument`.
  Y `options` **reemplaza** al dict que `ChatOllama` arma desde sus campos, así
  que la temperatura hay que repasarla en la misma llamada o se pierde sin aviso.

## Postdata: por qué los tests contra el modelo real no son opcionales

`bind(num_predict=...)` —la forma intuitiva, y la primera que se escribió— pasa
los tests con dobles y **falla contra Ollama**. El doble acepta cualquier kwarg;
el cliente real, no.

Es la misma lección que ya está escrita en `docs/` sobre la medición: el
instrumento miente antes que el sistema. Un doble prueba que *nuestro* código
traduce bien, y es ciego a si la API del framework acepta esa traducción. Por eso
`tests/test_agent_llm_langchain.py` tiene las dos capas, y por eso los dos tests
marcados `llm` tienen que correrse cada vez que se toque este adaptador o se suba
la versión de `langchain-ollama`:

```
pytest tests/test_agent_llm_langchain.py -m llm
```
