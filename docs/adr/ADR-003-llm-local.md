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

> **Se implementó, y no alcanzó.** Ver la *Revisión (2026-08-28)*: dos de las
> cinco métricas que salieron de esta mitigación estuvieron mal —una midiendo
> una sección que el sistema tiene prohibido escribir, otra aprobando una
> afirmación inventada— sin que ninguna fallara. La conclusión de arriba tiene
> hermana: una métrica de citación también es necesaria y no suficiente.

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

---

## Revisión (2026-08-28) — dos instrumentos en verde sobre informes incorrectos

**Estado del riesgo 2: la mitigación se implementó, y falló de dos maneras que
el riesgo no anticipaba.**

El riesgo 2 pedía que "el golden set incluya aserciones sobre *afirmaciones*, no
solo sobre *números*". Se hizo: `eval/metricas.py` mide cinco defectos concretos
del informe. Un año de disciplina después, dos de esas cinco métricas estuvieron
mal —una midiendo nada, otra aprobando una mentira— y **ninguna falló**. Es el
riesgo 3 otra vez, un nivel más arriba: el instrumento también necesita ser
validado, y validarlo no se hace una sola vez.

### Hallazgo 1 — una métrica midió 0 de 15 y nada se había roto

`atribuye_al_producto_correcto` leía `informe.recomendaciones`. Esa sección **el
sistema tiene prohibido escribirla**: la regla 3 de `synthesizer.SISTEMA` veta
las recomendaciones y las lista entre sus ejemplos incorrectos. La única vía a
esa sección es `validator.validar_informe`, que reubica lo que el modelo escribió
*desobedeciendo* esa regla.

| modelo | obediencia a la regla 3 | cobertura de la métrica |
|---|---|---|
| `llama3.2:3b` | desobedecía 2 de 15 veces | 2/15 |
| `qwen3:4b` | obedece 15 de 15 | **0/15** |

La cobertura no medía atribución: medía **desobediencia**. El modelo mejoró y el
instrumento lo registró como una regresión.

Abajo había un segundo defecto. La métrica buscaba identificadores (`\bP\d{3,}\b`)
donde el informe escribe **marcas**: en las cinco capturas de `docs/replay/` de
esa fecha había un solo `P010` en toda la prosa, contra "Ribera", "Vertex" y
"Lumen" por todas partes. Aunque hubiera habido recomendaciones, la métrica
habría fallado igual — por formato, no por atribución.

**Por qué ningún test lo detectó en meses:** el fixture usaba
`nombre="Producto 010"` y escribía `"P010"` en el texto. `seeds/generate.py`
genera `"<marca> <categoría>"`. El fixture no se parecía a los datos de
producción, así que la regla se validaba a sí misma.

> **Lección.** Una regla léxica se valida contra **salidas reales del sistema**,
> no contra el fixture que la acompaña. Corregida la métrica, se verificó sobre
> las cuatro capturas con informe: pasaron de cobertura nula a resolver su
> producto en las cuatro.

### Hallazgo 2 — una afirmación inventada, con 100% de la métrica que debía verla

En el sitio publicado, primer caso del replay:

```
HECHO  "El proveedor de Vertex calzado (P001) reportó defectos de costura
        en el lote"                              fuente: doc_ficha_P001
```

`doc_ficha_P001` es una ficha técnica: no menciona defectos, ni costura, ni un
lote. El texto salía del **ejemplo del propio prompt** del sintetizador. El
modelo copió el ejemplo y le pegó un identificador real, que es la peor forma de
alucinación porque no parece una: parece rigor.

Vivía en el hueco entre dos guardrails, cada uno mirando la mitad:

| guardrail | qué verifica | por qué no la vio |
|---|---|---|
| `ReportValidator` (numérico) | que cada cifra salga de una herramienta | la frase no tiene ninguna cifra: caía en `if not numeros: aceptadas.append(a)` |
| `usa_la_evidencia_documental` | que el id citado esté entre los recuperados | `doc_ficha_P001` **sí** estaba entre los recuperados. Le dio **100%** |

> **Conclusión, que extiende la del riesgo 2.** Una **métrica de citación** es
> necesaria y no es suficiente. Detecta la cita ausente; no detecta la cita que
> no sostiene lo que dice sostener.

### Mitigación implementada

`_anclada_en_su_cita` en `agent/nodes/validator.py`: una afirmación que cita un
documento tiene que estar hecha de las palabras de ese documento. Es el criterio
que ya se le aplicaba a las cifras —un número vale si figura en el pasaje
citado— extendido a la prosa.

Va en el **validador y no en el prompt**, porque un prompt es una sugerencia.

**El alcance es la parte que decidió el diseño.** Solo se juzgan las afirmaciones
**sin cifras**: las que traen cifras ya están ancladas por ellas, y exigirles
además parecido léxico las castigaría por parafrasear, que es su trabajo.

| afirmación | sustancia en el documento citado | veredicto correcto |
|---|---|---|
| "El proveedor... defectos de costura" (`doc_ficha_P001`) | **20%** | fabricada |
| "El reporte de quiebre de stock explica..." (`doc_stock_006`) | 75% | correcta |
| "El lote L4990 del proveedor..." (`doc_prov_010`) | 100% | correcta |
| "La campaña de descuento del 30%..." (`doc_promo_001`) | 25% | **correcta** — la ancla su cifra |

La última fila es la que fija el alcance: el modelo parafraseó "acción" como
"campaña" y "salto" por "la demanda se multiplicó". Sin acotar la regla a las
afirmaciones sin cifras, el arreglo habría borrado una afirmación válida.

Dos detalles que la regla necesita y no son cosméticos:

- **La identidad del producto no cuenta.** `vertex`, `calzado` y `p001` aparecen
  en *todo* documento sobre ese producto. Era lo único que la frase inventada
  compartía con su ficha.
- **Se compara por raíz de cinco letras.** El español conjuga y pluraliza: la
  primera medición de este arreglo dio un falso negativo comparando `costura`
  con `costuras`. El instrumento del arreglo también estaba mal.

Umbral `ANCLAJE_MINIMO = 0.5`, puesto donde el hueco es ancho (20% contra 75% y
100%) y no ajustado al decimal.

Se descarta la **afirmación** y no solo la cita: quitarle la fuente la dejaría
atribuida a los datos, y un hecho inventado con el sello de SQL es peor que uno
con una cita que no lo sostiene.

### La verificación, y por qué salió mejor de lo esperado

Al recapturar, **el modelo volvió a inventar la misma frase** y el validador la
frenó. Queda escrita en las advertencias del informe publicado.

Que reincida es el resultado útil. Si la corrida hubiera salido limpia no se
sabría si el guardrail funciona o si tocó un sorteo favorable; así quedó medido
contra el modelo vivo y no contra un fixture propio. Y sin falsos positivos:
`hyb-02` y `perf-01` conservaron sus citas legítimas.

### Defecto menor del mismo informe

Las limitaciones declaraban "no incluyen evidencia documental" **siempre**,
incluso tres bloques debajo de una cita documental. Ahora es condicional a la
evidencia recuperada. Un informe que se contradice en la misma página no tiene un
problema de redacción: tiene uno de credibilidad, y justo en el proyecto cuyo
argumento entero es la trazabilidad.

### Lo que esta revisión NO afirma

- **Que el anclaje léxico detecte alucinaciones en general.** Detecta la que ya
  ocurrió: una afirmación construida con palabras que el documento no tiene. Una
  paráfrasis lo bastante creativa lo pasa, y una alucinación que reuse el
  vocabulario del documento también. Es un piso verificable, no un techo.
- **Que el umbral esté calibrado.** Está fijado sobre dos ejemplos correctos y
  uno inventado. Se eligió el medio del hueco más ancho justamente porque tres
  muestras no alcanzan para calibrar un número; hace falta más corridas antes de
  moverlo, y moverlo con el resultado a la vista no sería calibrar.
- **Que las cinco métricas en 100% signifiquen que el informe sea correcto.**
  Es, literalmente, el error que este ADR documenta en el riesgo 2: la auditoría
  daba 22/22 y el informe estaba mal. El 100% de hoy dice que los cinco defectos
  *conocidos* no aparecen en quince casos. No dice nada sobre el sexto.

---

## Revisión (2026-08-28 bis) — `num_ctx` se mide y se descarta

**Resultado negativo. No se aplica ningún cambio.** Queda escrito para que la
optimización no se vuelva a proponer sin saber que ya se midió.

### La premisa era falsa

La propuesta decía: `qwen3:4b` declara un contexto de 262.144 tokens para
prompts de ~2.000, así que conviene acotarlo. **Ollama ya lo acota solo.**

```
qwen3:4b declara (qwen3.context_length) : 262 144
Ollama le da (api/ps, context_length)   :   4 096   ← su default; el Modelfile no fija num_ctx
Prompt real más caro del sintetizador   :   1 888   ← medido con prompt_eval_count
```

El prompt usa el 46% de la ventana. No había un contexto gigante que recortar:
había una suposición sobre lo que el modelo declara, que no es lo que el
servidor asigna.

### Criterios de aceptación, fijados antes de medir

Adoptar un `num_ctx` explícito solo si **(a)** no trunca el prompt real, y además
**(b)** reduce la latencia mediana ≥15% contra el control, **o (c)** elimina un
riesgo de truncado silencioso.

### Ablación

Prompt real del sintetizador, condiciones **alternadas dentro de cada ronda**
—nunca en bloques—, control sin `num_ctx` en las tres, guardado incremental.

| condición | mediana | mín | máx | tokens de prompt | conclusiones |
|---|---|---|---|---|---|
| control (sin `num_ctx`) | 172,8 s | 49,1 s | 190,7 s | 1 888 | 5 |
| `num_ctx=2048` | 174,7 s | 143,5 s | 175,0 s | 1 888 | 5 |
| `num_ctx=4096` | 176,6 s | 173,2 s | 186,0 s | 1 888 | 5 |
| `num_ctx=8192` | 168,8 s | 161,1 s | 176,7 s | 1 888 | 5 |

### Decisión: no se adopta

**(b) no se cumple.** Las cuatro medianas caen entre 168,8 s y 176,6 s: una
dispersión de 7,8 s sobre ~172 s, el **4,5%**. Y el número que cierra la
discusión es el otro: la variación **dentro** del control va de 49,1 s a 190,7 s
—141 segundos—, o sea que **el ruido es dieciocho veces más grande que la
diferencia entre condiciones**. Cuando el ruido se traga al efecto, el ganador de
la tabla es azar con nombre propio.

**(c) tampoco.** `prompt_eval_count`, tokens de salida y cantidad de conclusiones
dieron **idénticos en las doce corridas**, incluida `num_ctx=2048`, donde
1 888 + 265 = 2 153 debería desbordar la ventana. No hubo truncado que prevenir.

Las dos razones para tocar `num_ctx` se cayeron con la misma medición.

### Lo que la alternancia salvó

La primera corrida —control, ronda 1— dio **49,1 s**, y todas las siguientes se
estacionaron entre 160 y 190 s. Sea lo que sea eso, **no es `num_ctx`**: esa
corrida es justamente la que no lo llevaba puesto.

Medido en bloques, el control habría salido campeón por haber corrido primero y
la conclusión habría sido "el default es 3× más rápido": falsa, y convincente. Es
la razón por la que el punto 4 de la metodología existe, y la segunda vez que
este ADR lo registra funcionando.

### Lo que esta revisión NO afirma

- **Que `num_ctx` no importe en general.** Se midió un prompt de 1 888 tokens
  contra una ventana de 4 096. Un prompt que se acerque al techo es otro
  experimento, y esta medición no lo cubre.
- **Que la latencia de ~172 s sea el costo real del sintetizador.** Es el costo
  *en esta máquina, en este momento*, con una dispersión enorme dentro de cada
  condición. Como línea base de cualquier optimización futura hace falta más de
  una corrida: `cmp-01` dio 64,9 / 127,6 / 121,9 s en tres capturas del mismo
  modelo y el mismo código.
- **Que no haya nada que optimizar.** El outlier de 49,1 s y la bimodalidad ya
  anotada en la revisión anterior apuntan a `keep_alive` —la recarga del modelo
  entre llamadas—, que es un experimento distinto y sigue pendiente.
