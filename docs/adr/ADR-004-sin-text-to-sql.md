# ADR-004 — El modelo no escribe SQL

- **Estado:** Aceptado
- **Fecha:** 2026-08-16 (decisión tomada y aplicada desde la Fase 1; este
  documento la registra tarde: estaba citado desde ADR-002 y ADR-005 sin existir)

## Contexto

El sistema responde preguntas de análisis comercial sobre datos que viven en SQL
Server. La forma de moda de resolver eso es **text-to-SQL**: se le da al modelo
el esquema de la base, el modelo escribe la consulta, el sistema la ejecuta.

Es tentador porque parece resolver el caso general de una sola vez. Y hay tres
razones concretas —no estéticas— por las que acá no se hace.

**Primera: las reglas de negocio dejarían de tener dueño.** ADR-005 fija que las
reglas de los KPIs viven en SQL, escritas una vez. El margen excluye
devoluciones; el revenue NO las netea, porque netearlas escondería la señal de
calidad que el sistema justamente tiene que detectar. Un modelo que escribe la
consulta resuelve esas decisiones de nuevo en cada llamada, y no
necesariamente igual dos veces. El informe pasaría a ser correcto *en promedio*.

**Segunda: los argumentos del modelo son entrada no confiable.** No por malicia
del modelo, sino por su naturaleza: son texto generado, igual que el de un
formulario público. Ejecutar texto generado contra un motor de base de datos es
la definición del problema, no una mitigación de él.

**Tercera: cuesta una llamada más al modelo.** Con inferencia CPU-only, cada
llamada son entre 12 y 41 segundos (ADR-003). Gastarlos en producir algo que ya
está escrito y probado no compra nada.

## Decisión

**El SQL lo escriben las personas y vive en `core/kpis.py`.** El modelo decide
*qué* herramienta usar y *con qué parámetros*, nunca *cómo* consultar.

La frontera es exactamente esa:

```
El modelo elige     →  tool = "product_metrics", product_ids, desde, hasta
El software ejecuta →  la consulta parametrizada, escrita a mano y testeada
```

## Las tres capas de defensa

Están implementadas y son independientes: cada una atrapa lo que la anterior
dejó pasar. Ninguna se apoya en las otras.

**1. Lista blanca en el esquema de la tool** (`agent/tools/product_metrics.py`)

```python
PATRON_PRODUCTO = re.compile(r"^P\d{1,6}$")
```

Es lista **blanca** y no negra a propósito. No se intenta detectar ataques: esa
carrera se pierde siempre, siempre aparece una codificación nueva. Se describe
con precisión qué es un identificador válido —la letra P y hasta seis dígitos— y
todo lo demás se rechaza sin analizarlo.

**2. Consultas parametrizadas** (`core/kpis.py`)

```python
cur.execute(sql, (product_id, *_rango(desde, hasta)))
```

Los valores viajan como parámetros del driver. Nunca se concatenan al texto de
la consulta, así que no hay punto donde un valor pueda convertirse en sintaxis.

**3. Usuario de base de datos read-only** (`infra/sql/02_readonly_user.sql`)

```sql
ALTER ROLE db_datareader ADD MEMBER ami_reader;
DENY INSERT, UPDATE, DELETE, ALTER, CREATE TABLE, EXECUTE TO ami_reader;
DENY SELECT ON dbo.ground_truth TO ami_reader;
```

`ami_reader` es el **único** usuario que ven las tools del agente. El `DENY`
explícito además de `db_datareader` no es redundancia decorativa: un `DENY` gana
sobre cualquier `GRANT` posterior, así que si alguien agrega este usuario a un
rol con más permisos, la denegación sigue en pie.

## El caso especial: `ground_truth`

La última línea merece su propio párrafo, porque no es una medida de seguridad
sino de **integridad de la medición**.

`dbo.ground_truth` contiene los eventos sembrados en el dataset: qué producto
tuvo un pico de devoluciones, en qué fecha, por qué. Es la hoja de respuestas
del golden set. El usuario del agente tiene `DENY SELECT` sobre esa tabla.

Sin eso, un cambio inocente en una consulta podría empezar a leer las respuestas
sin que nadie lo note, y el eval mediría el acceso a la tabla en vez de la
capacidad del sistema. La restricción hace que ese error sea **imposible**, no
improbable.

## Verificación

La decisión no está sostenida por comentarios. `tests/test_db_guardrails.py`
tiene nueve tests que la ejercitan contra la base real:

| Test | Qué prueba |
|---|---|
| `test_lector_puede_consultar_productos` | El camino feliz funciona |
| `test_lector_puede_hacer_joins_analiticos` | La restricción no rompe el análisis |
| `test_lector_no_puede_insertar` / `actualizar` / `borrar` | Sin DML |
| `test_lector_no_puede_dropear_tablas` / `crear_tablas` | Sin DDL |
| `test_lector_no_puede_leer_el_ground_truth` | La hoja de respuestas está cerrada |
| `test_el_ground_truth_si_es_accesible_para_evaluacion` | Pero el eval sí la lee |

Los dos últimos juntos son el punto: la tabla existe y se usa, y el que no puede
verla es específicamente el agente.

## Alternativas consideradas

| Alternativa | Por qué se descartó |
|---|---|
| **Text-to-SQL** con el esquema en el prompt | Pone las reglas de negocio en manos del modelo (ADR-005), ejecuta texto generado y cuesta una llamada extra de 12-41s (ADR-003). |
| Text-to-SQL **con validación del SQL generado** antes de ejecutar | Parsear SQL para decidir si es seguro es una lista negra con otro nombre. Y si al final solo se aceptan las formas previstas, esas formas son las consultas que ya están escritas. |
| Vistas SQL en vez de consultas parametrizadas | Más limpio para consultas fijas, pero los rangos de fecha son dinámicos. Revisable si aparecen consultas repetidas (ya está anotado en ADR-005). |
| Un ORM que arme las consultas | Resuelve la inyección pero no el problema real: las reglas de negocio seguirían dispersas en código de aplicación en vez de estar en un solo lugar. |

## Cuándo esta decisión dejaría de valer

Se escribe para que el límite quede marcado, igual que en ADR-001.

Text-to-SQL **sí** sería el camino correcto si el sistema tuviera que responder
preguntas arbitrarias sobre un esquema que cambia, o sobre bases que el equipo
no conoce de antemano. Ahí el costo de escribir cada consulta a mano supera el
costo del riesgo, y la conversación pasa a ser cómo acotar el riesgo.

No es este caso. Acá el dominio es conocido, el esquema es propio y las
preguntas caen en un puñado de formas previstas.

## Consecuencias

**Positivas**
- Las reglas de negocio tienen un único dueño y se testean como código.
- La superficie de ataque es una lista blanca de identificadores, no un parser.
- El eval no puede contaminarse leyendo la tabla de respuestas.
- Una llamada menos al modelo por consulta.

**Negativas**
- Cada pregunta nueva que el sistema deba responder requiere escribir una
  consulta. Es trabajo real y es el precio de todo lo anterior.
- El sistema no puede responder preguntas que nadie previó. Responde bien las que
  sí, y para el resto dice que no puede — que es lo que hace `FUERA_DE_ALCANCE`.
