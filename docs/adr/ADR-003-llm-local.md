# ADR-003 — LLM local: elección de modelo basada en mediciones propias

- **Estado:** Aceptado
- **Fecha:** 2026-08-09

## Contexto

Todo el proyecto descansa sobre un supuesto no validado: que un LLM local corra
de forma usable en el hardware disponible. Si ese supuesto falla, se caen las
fases 2 a 7 completas.

**Hardware de referencia:**

```
CPU   : Intel i7-1255U (10C/12T) — ultrabook, 15W
RAM   : 31.7 GB
GPU   : Intel Iris Xe integrada — SIN NVIDIA, SIN CUDA
```

Inferencia 100% CPU. La RAM abundante no ayuda: el cuello de botella es el ancho
de banda de memoria y los cores, no la capacidad. Cargar un modelo grande es
posible y a la vez inútil.

Se ejecutó un spike con criterios de aceptación fijados **antes** de medir:

| Criterio | Umbral |
|---|---|
| Velocidad de generación | ≥ 8 tok/s |
| Tool calling correcto | 3/3 |
| JSON schema válido | 3/3 |
| Síntesis de informe | ≤ 60 s |

## Decisión

**Modelo por defecto: `llama3.2:3b`** (Q4_K_M, vía Ollama).

Se descartan `qwen3:1.7b` y `qwen3:4b`.

## Evidencia

### Ronda 1 — comparación de modelos

| modelo | tok/s | tools | json | síntesis | citas RAG |
|---|---|---|---|---|---|
| qwen3:1.7b | 16.78 | 3/3 | 3/3 | 75.4 s | **0/3** |
| llama3.2:3b | 8.17 | 3/3 | 3/3 | 113.7 s | **3/3** |
| qwen3:4b | 6.24 | 3/3 (207s / 85s / 313s) | — | — | — |

`qwen3:4b` quedó descartado por velocidad: 2.7x más lento que `qwen3:1.7b` con
solo 2.3x más parámetros. **Escalar parámetros en CPU sale carísimo.**

### Ronda 2 — invalidada por su propio grupo de control

Se midió el efecto de desactivar el modo *thinking* de qwen3 corriendo primero
todas las mediciones de una condición y después las de la otra. El grupo de
control (`llama3.2:3b`, que no tiene modo thinking y por lo tanto no puede verse
afectado) se degradó de 8.17 a 4.67 tok/s: **-43%**.

Esa caída prueba que las condiciones de la máquina derivaron durante el
experimento. El efecto "thinking" quedó confundido con el efecto "momento en que
se midió", y la comparación entre rondas resultó inutilizable.

**Sin el grupo de control, se habría reportado ruido como hallazgo.**

### Ronda 3 — diseño intercalado

Se repitió el experimento alternando condiciones e intercalando el control:
`CTRL → ON → OFF → CTRL → OFF → ON → CTRL`, con mediciones de ~30 s para que
fueran repetibles.

```
CONTROL (llama3.2:3b) : [6.15, 5.74, 5.44]  → deriva 11.5% → ESTABLE
qwen3 think=ON        : [8.81, 8.80]        → media 8.80
qwen3 think=OFF       : [9.33, 9.23]        → media 9.28

Efecto de apagar thinking en velocidad : +5.4%
Latencia de tool call    ON: 41.55 s  |  OFF: 19.52 s
Tool calls correctos     ON: 2/2      |  OFF: 0/2
```

**La ronda 2 estaba equivocada por completo.** Sugería que apagar el thinking
degradaba la velocidad un 50%; el diseño intercalado muestra que la mejora un
5.4%. Toda esa "degradación" era deriva de la máquina.

Hallazgo real: apagar el thinking hace el router 2.1x más rápido y 0% correcto.
`qwen3:1.7b` **necesita** razonar para elegir bien la herramienta, y eso le
cuesta ~41 s por decisión de routing. `llama3.2:3b` decide en ~12 s.

## Justificación

`llama3.2:3b` **no gana por velocidad — pierde en velocidad.** Gana por ser el
único que se comportó igual bajo condiciones distintas:

- Tool calling 3/3 en todas las rondas y condiciones.
- Citas RAG 3/3 en todas las rondas.
- Groundedness numérica 100%: cero números inventados.

`qwen3:1.7b` es más rápido y no sirve: no cita fuentes (viola la Definition of
Done) y su tool calling depende del thinking, que le cuesta 41 s por llamada.

**En un sistema de producción la consistencia le gana a la velocidad pico.** Un
modelo que a veces no llama la tool es peor que uno lento que siempre la llama:
la lentitud se resuelve con arquitectura, la inconsistencia rompe el agente de
forma aleatoria.

## Consecuencia mayor: el informe no puede ser síncrono

**Ningún modelo cumplió el criterio de síntesis ≤ 60 s.** Y no lo va a cumplir
ninguno: es física de la CPU, no elección de modelo.

Esto no se arregla cambiando de modelo. Se arregla por diseño:

- Los informes se generan como **trabajo asíncrono** (job + worker), no en el
  ciclo de request HTTP.
- El frontend muestra **progreso por etapas**, alimentado por el tracing del grafo.
- Las tools cachean resultados: el caching deja de ser "extensión opcional" y
  pasa a ser parte del núcleo.

**Consecuencia de roadmap:** la Fase 6 (jobs, caching) sube de prioridad y deja
de ser un extra de infraestructura.

## Riesgos abiertos y detectados

### 1. El router clasifica mal la intención

En las dos rondas y en los dos modelos, el campo `intent` salió mal:
`company_research` para consultas que eran claramente `product_performance`.

Que se repita bajo condiciones distintas lo confirma como problema **real y
robusto**, no ruido. El JSON era 3/3 válido contra el schema: Pydantic lo habría
aprobado sin quejarse. **La validez de schema no implica correctitud semántica.**

Mitigación: prompt con few-shots + `tool selection accuracy` como métrica propia
en el golden set.

### 2. Groundedness 100% con el informe igualmente incorrecto

La auditoría numérica dio 22/22 números grounded y 0 inventados. Aun así, el
informe generado contiene errores graves:

- **Invierte el significado de una métrica**: describe un MAPE de 8,3% como
  "precisión del 8,3%". MAPE es *error*, no precisión. El número es correcto y
  la afirmación es falsa.
- **Confunde baseline con métrica del modelo**: llama al *baseline naïve*
  "precisión basada en la tendencia".
- **Recomendación dirigida al producto equivocado**: sugiere reducir devoluciones
  para el Producto A (2,1%) en vez del Producto B (5,7%).
- **No conecta la evidencia con la anomalía**: tiene en el contexto dos
  documentos que explican el pico de devoluciones (campaña sin control de stock,
  cambio de lote del proveedor) y concluye "sugiere una posible causa externa".
  Las citas quedan pegadas al final como decoración en vez de integrarse al
  análisis.
- **Omite todas las magnitudes absolutas**: unidades (1.243, 981), revenue
  (87.010, 92.340) y proyecciones (1.470, 910) no aparecen. El informe habla solo
  en porcentajes.

**Conclusión:** un validador numérico es necesario y no es suficiente. Detecta
alucinación de cifras; no detecta interpretaciones invertidas, atribuciones
equivocadas ni evidencia sin usar.

Mitigación: el `ReportValidator` necesita chequeos semánticos además de
numéricos, y el golden set debe incluir aserciones sobre *afirmaciones*, no solo
sobre *números*.

### 3. El instrumento de auditoría sobreestima

El auditor contó como claims numéricos los IDs de documentos (`doc_112` → 112) y
los números de sección (`§3.2` → 3.2). De los 22 "grounded", aproximadamente la
mitad son artefactos de parsing.

La métrica de groundedness está inflada cerca del doble. **El instrumento de
medición también necesita ser validado.**

Mitigación: excluir IDs y referencias de sección antes de extraer los números, y
distinguir magnitudes de negocio de metadatos.

## Alternativas consideradas

| Alternativa | Por qué se descartó |
|---|---|
| API paga (OpenAI / Anthropic / Gemini) | Viola la restricción de costo cero. Resolvería latencia y calidad, y queda documentada como la extensión natural si el proyecto dejara de ser costo cero. |
| Modelo local más grande (7B–14B) | Entra en 31 GB de RAM, pero `qwen3:4b` ya bajó a 6.24 tok/s. Un 7B sería inutilizable. |
| `qwen3:1.7b` por velocidad | No cita fuentes RAG (0/3 y 1/3), lo que viola un ítem de la Definition of Done. |
| Modelos distintos por nodo del grafo | Interesante: uno chico para routing y otro para síntesis. **Se difiere**, no se descarta — requiere primero resolver el problema de clasificación de intención. |

## Metodología establecida

A partir de este spike, toda medición de performance del proyecto se corre:

1. Con la máquina en reposo.
2. Con **grupo de control** incluido.
3. Con criterios de aceptación fijados **antes** de medir.
4. Con condiciones **alternadas**, nunca en bloques, cuando las condiciones
   puedan derivar en el tiempo.
5. Con mediciones lo bastante baratas como para poder repetirse. Un test que
   tarda 200 s no se puede correr siete veces, y por lo tanto no sirve para
   comparar.
6. Con guardado incremental de resultados.

---

## Revisión (2026-08-27) — se adopta `qwen3:4b` con el thinking apagado

**Estado de la decisión original: superada.** No estaba equivocada: medía una
condición que dejó de ser la que corre.

### Qué la reabrió

Un análisis en el stack containerizado tardó 287 s y el informe salió del
respaldo determinístico. El trace mostró dónde:

```
router                0 ms      (cortocircuitó: la solicitud vino estructurada)
planner               0 ms
sql_tool            354 ms
synthesizer      300 328 ms     ← TIMEOUT_SEGUNDOS clavado
```

300 328 ms no es un modelo pensando: es el timeout. `qwen3:4b` tiene la
capacidad `thinking` y `estructurado` no acotaba nada, así que el modelo
razonaba hasta que lo cortaban.

**Y ahí está la conexión con la ronda 1 de este mismo ADR.** `qwen3:4b` se
descartó por lento, con tool calls de 207 s, 85 s y 313 s. Esos números tienen
la misma forma que el bug: se midió el modelo **con el razonamiento
prendido**, que era la única forma conocida de correrlo en ese momento.

### Ablación del sintetizador

Prompt real del sintetizador, una variable por vez, con grupo de control:

| condición | tiempo | tokens | resultado |
|---|---|---|---|
| `qwen3:4b` como estaba | 312,2 s | — | **FALLO ReadTimeout** |
| `qwen3:4b` `think=False` | **60,9 s** | 112 | 3 conclusiones, OK |
| `qwen3:4b` `num_predict=400` | 194,9 s | 400 | **JSON truncado** |
| `llama3.2:3b` (control) | 132,0 s | 174 | 4 conclusiones, OK |

El tiempo nunca estuvo en escribir el JSON —son 112 tokens— sino en razonar
antes. `num_predict` lo empeora: gasta 1.516 caracteres pensando, se queda sin
presupuesto y decapita la respuesta. Es la confirmación empírica de lo que ya
advertía `tests/test_agent_synthesizer.py`.

### Ablación del router, con diseño intercalado

La ronda 3 de este ADR midió `think=OFF` sobre **`qwen3:1.7b`** y encontró
tool calling 0/2. Ese hallazgo **no se traslada al 4b**, y suponerlo habría
sido el error: se midió.

Cinco casos con intención conocida, condiciones rotadas por caso:

| condición | aciertos | media |
|---|---|---|
| `llama3.2:3b` (control) | 5/5 | 48,7 s |
| `qwen3:4b` `think=ON` | **0/5** | 152,1 s (todos ReadTimeout) |
| `qwen3:4b` `think=OFF` | **5/5** | 12,8 s |

Con el razonamiento prendido el router es inservible: no clasifica mal,
directamente no termina. Apagado, acierta los cinco.

**Advertencia sobre esa columna de latencia:** intercalar modelos obliga a
Ollama a recargar pesos, y el control salió bimodal (74,8 / 74,0 / 8,7 / 9,0 /
76,9 s). El confound de la deriva se cambió por el del swap. La **precisión**
—5/5 contra 0/5— no se ve afectada, pero la comparación de latencia entre
modelos no es limpia y no se usa como evidencia. Medirla bien requiere un
modelo por corrida, en caliente.

### Decisión revisada

**`qwen3:4b` con `"think": False` en `estructurado`.**

El flag va siempre y no condicionado al modelo: `llama3.2:3b` no tiene la
capacidad, lo recibe y lo ignora (HTTP 200 en 5,9 s). Un flag que hay que
acordarse de poner según el modelo es uno que se olvida el día que se cambia
el modelo — que es exactamente cómo apareció este bug.

### Lo que esta revisión NO afirma

- **Que `qwen3:4b` sea mejor en calidad de informe.** Se midió velocidad y
  corrección del router. Las cinco métricas del golden set hay que
  re-correrlas: las corridas de `eval/corridas/` dicen `llama3.2:3b` y por lo
  tanto describen un sistema que ya no es el que está configurado.
- **Que `qwen3:1.7b` deba reconsiderarse.** Sigue descartado por no citar
  fuentes RAG, que es un ítem de la Definition of Done y no una cuestión de
  velocidad.
