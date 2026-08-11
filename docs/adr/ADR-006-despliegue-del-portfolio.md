# ADR-006 — Despliegue del portfolio: por qué este proyecto no va a AWS

- **Estado:** Aceptado
- **Fecha:** 2026-08-11

## Contexto

El objetivo es concreto: que alguien que recibe el link pueda **probar el
agente** sin instalar nada. El proyecto tiene, desde el ADR-002 y la definición
inicial, una restricción declarada no negociable:

> Costo USD 0: nada de APIs pagas, vector DB SaaS ni cloud obligatorio.

La pregunta era si esa restricción y un despliegue en AWS podían convivir. La
respuesta resultó ser aritmética, no arquitectónica.

**Lo que el sistema necesita para correr entero:**

| Componente | RAM | Nota |
|---|---|---|
| Ollama + `llama3.2:3b` | ~4 GB | Inferencia sostenida, 100% CPU |
| SQL Server 2025 | ~2 GB | Mínimo del motor; imagen de 1,5 GB |
| torch + sentence-transformers | ~2 GB | ~2 GB solo de dependencias en disco |
| FastAPI + FAISS + MLflow | ~1 GB | Lo barato |
| **Total** | **~8 GB** | y ~20 GB de disco |

**Lo que tarda una corrida real** (medido sobre las capturas del 2026-08-11, no
estimado):

```
cmp-01   93,8 s      hyb-02   88,6 s
perf-01  72,3 s      hold-05  ~75 s
```

Entre setenta segundos y minuto y medio por análisis. Eso ya condiciona todo lo
demás: **nadie mira un spinner noventa segundos.**

## Decisión

**No se despliega el sistema. Se publican ejecuciones reales grabadas.**

1. **Replay estático** (`docs/replay/`) como puerta de entrada, servido por
   GitHub Pages. Carga instantánea, costo cero, sin vencimiento.
2. **El repositorio** con `docker compose up` documentado, para quien quiera
   correrlo de verdad.
3. **No se escribe infraestructura como código para AWS.** Ver *Alternativas*.

## Evidencia

### 1. AWS no puede sostener el costo cero

| Cuenta creada | Qué otorga | Por qué no alcanza |
|---|---|---|
| Antes del 2025-07-15 | 750 h/mes de `t2/t3.micro`, 12 meses | `t3.micro` = **1 GB de RAM**. SQL Server Express pide ~1,4 GB. No entra. Y expira. |
| Desde el 2025-07-15 | **USD 200 en créditos, 6 meses** | El consumo descuenta del crédito. No es una cuota gratuita paralela: es un reloj de arena. |

Ninguna de las dos ramas sostiene un servicio permanente. La primera no entra en
memoria; la segunda no es gratis, es una prueba con fecha de vencimiento.

### 2. Las instancias burstable son lo contrario de lo que hace falta

La familia `t3`/`t4g` entrega CPU por créditos: excelente para tráfico a
ráfagas, pésima para carga sostenida. Este sistema ocupa la CPU al máximo
durante noventa segundos por consulta. Los créditos se agotan y la instancia cae
a su *baseline* — en una `t3.large`, el 30% de 2 vCPU.

Servirlo bien exigiría familia dedicada (`m7i`/`c7i`): del orden de **USD 60-75
por mes** para un demo que se visita de a ratos.

### 3. El bloqueante no es el LLM: es SQL Server

Este fue el hallazgo que reordenó el análisis. Se asumía que el modelo de
lenguaje era lo caro. No lo es:

- SQL Server pide ~1,4 GB en su edición Express y Microsoft **no publica imagen
  para ARM64 Linux**.
- Eso descarta el free tier más generoso que existe —Oracle Cloud Always Free,
  4 vCPU y 24 GB de RAM sin vencimiento— porque es ARM.
- También descarta Render (512 MB) y complica Cloud Run, donde el arranque en
  frío con un modelo de 2 GB es prohibitivo.

Todas las opciones gratuitas mueren en el mismo lugar, y no es donde se esperaba.

### 4. Hugging Face Spaces dejó de ser una salida

Los 16 GB de RAM del hardware *CPU Basic* existen, pero la documentación oficial
es explícita: los Spaces de **Docker y Gradio requieren plan pago** para
crearse (PRO en cuentas personales). Solo los *Static Spaces* siguen siendo
gratuitos para todos. Hay queja formal de la comunidad en el foro oficial por
este cambio.

Se verificó antes de escribir una línea de Dockerfile.

### 5. Una licencia que no habilitaba el uso

El `docker-compose.yml` declara `MSSQL_PID: Developer`, cuya licencia cubre
desarrollo y test **no productivo**. Un demo público en internet no encaja en esa
definición. El reemplazo por Express era trivial —los datos pesan 1 MB contra un
tope de 10 GB— pero el punto es que el despliegue habría requerido revisar la
licencia, y eso no estaba en el plan original.

## Justificación

La decisión no se toma por precio. Se toma porque **el replay muestra más que un
demo en vivo.**

En vivo, el visitante espera noventa segundos y ve un PDF. En el replay ve, al
instante:

- La traza etapa por etapa con la duración **real** de cada nodo.
- El campo `razon` de cada herramienta elegida, o sea el criterio del agente.
- Las citas documentales con su identificador, resolubles contra la fuente.
- El forecast contra su baseline, con el veredicto explícito de si le gana.
- Las advertencias que el modelo `Report` genera por sí solo.

Todo eso **ya existía** en el modelo de datos y en una corrida en vivo queda
invisible. El replay no es una versión recortada del sistema: es una vista que el
sistema en vivo no ofrece.

La honestidad se sostiene por construcción, no por promesa: el manifiesto declara
el modelo y la fecha, rechaza capturas de modelos distintos, y publica el comando
de reproducción. La página lo dice **arriba de todo**, no en un pie.

## Consecuencias

**Positivas**

- Costo de hosting **cero, sin vencimiento y sin tarjeta**.
- Sin endpoint público no hay superficie de abuso, ni secretos que rotar, ni
  cuenta que vigilar.
- El sitio no puede "caerse": son archivos estáticos.

**Negativas, y hay que decirlas**

- **Nadie puede escribir su propia consulta.** Es la limitación real de esta
  decisión y no tiene mitigación dentro del costo cero.
- **Las capturas envejecen.** Si cambia el prompt, el modelo o el dataset, el
  replay muestra un sistema que ya no existe. Nada lo fuerza a regenerarse.
- Capturar es un paso manual (`.\tasks.ps1 replay`) que tarda varios minutos y
  exige la base y Ollama arriba. No puede ir en CI.
- Los datos capturados —JSON y PDF, unos 72 KB— se versionan en el repositorio,
  porque GitHub Pages sirve desde `docs/`.

## Alternativas consideradas

| Alternativa | Por qué se descartó |
|---|---|
| EC2 `m7i`/`c7i` con el stack completo | ~USD 60-75/mes y noventa segundos por consulta. Viola la restricción de costo y ofrece peor experiencia que el replay. |
| Créditos de AWS por 6 meses | Sirve si la búsqueda laboral es inmediata. Se descartó por ser una solución con fecha de vencimiento a un problema permanente. |
| Hugging Face Spaces con Docker | Requiere plan PRO. Verificado en la documentación oficial. |
| Oracle Cloud Always Free (24 GB, ARM) | SQL Server no tiene imagen ARM64. |
| Reemplazar SQL Server por SQLite solo en el demo | Entra en free tiers reales, pero diluye la decisión central del ADR-002 y obliga a mantener dos rutas de datos. El demo dejaría de ser este sistema. |
| Reemplazar Ollama por una API paga en el demo | Rompe el costo cero y, peor, **invalida el golden set**: `llama3.2:3b` se eligió midiendo consistencia (ADR-003). Un demo con otro modelo no muestra el sistema que se evaluó. |
| Terraform de AWS como entregable, versionado pero nunca aplicado | Infraestructura que nunca se ejecutó no está verificada. Un revisor que corra `terraform plan` encuentra código que no provisiona nada, y el artefacto resta en vez de sumar. Este ADR ocupa su lugar: documenta criterio, y el criterio sí es verificable. |

## Riesgo abierto

**No hay nada que detecte que las capturas quedaron viejas.** Si se cambia el
prompt del sintetizador y no se recaptura, el sitio sigue publicando la corrida
anterior con su fecha correcta — o sea, seguirá siendo honesto sobre *cuándo* se
grabó, pero mostrará un sistema que ya no es el del repositorio.

Mitigación posible, no implementada: comparar en CI el hash del prompt y del
modelo declarado en el manifiesto contra los del código, y fallar si difieren.
