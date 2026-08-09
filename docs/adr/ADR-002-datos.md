# ADR-002 — SQL Server como base relacional + FAISS local para vectores

- **Estado:** Aceptado
- **Fecha:** 2026-08-09

## Contexto

El sistema necesita dos capacidades de datos con naturalezas distintas:

1. **Datos transaccionales/analíticos**: productos, órdenes, ítems, inventario,
   devoluciones, campañas y clientes. Se consultan con agregaciones, ventanas
   temporales y joins. Las métricas que salen de acá son **hechos**: alimentan
   los KPIs del informe y no admiten aproximación.

2. **Evidencia documental**: reportes internos, fichas de producto y documentos
   públicos. Se consultan por similitud semántica, no por igualdad exacta.

SQL Server 2025 incorpora un tipo `VECTOR` nativo y `VECTOR_SEARCH`, lo que
abriría la puerta a resolver ambas necesidades en un solo motor.

## Decisión

- **Base relacional:** Microsoft SQL Server 2025 Developer, consultado con T-SQL.
- **Índice vectorial:** **FAISS local**, en un índice separado.
- El soporte vectorial nativo de SQL Server 2025 se evalúa como **experimento
  opcional**, nunca como dependencia del núcleo.

## Justificación

**Por qué SQL Server.** Es gratuito para desarrollo y test no productivo, es
estándar de facto en entornos corporativos, y T-SQL analítico (CTEs, funciones
de ventana, índices) es una competencia demostrable y demandada. Además obliga
a diseñar un esquema relacional de verdad en lugar de esconder todo en un
dataframe.

**Por qué FAISS separado y no vector search nativo.** Una feature reciente de un
motor no debe convertirse en dependencia crítica de un proyecto que tiene que
funcionar de punta a punta hoy. Si el soporte vectorial cambia de comportamiento,
de API o de disponibilidad, se cae el RAG y con él la mitad del sistema.

Mantener el índice vectorial separado también permite **evaluar el retrieval de
forma independiente** de la base transaccional, que es un requisito de los evals:
hay que poder medir si el retrieval empeoró aunque la respuesta suene bien.

**El costo de la separación es conocido y aceptado**: dos almacenes que mantener
sincronizados, y la imposibilidad de hacer un join directo entre métricas y
embeddings. Se acepta porque la alternativa concentra riesgo en el componente
menos maduro del stack.

## Regla de asignación

Cuál de los dos responde una pregunta no se decide caso por caso, se decide por
naturaleza del dato:

| Tipo de pregunta | Fuente | Ejemplo |
|---|---|---|
| Números, agregaciones, rankings, series temporales | **SQL** | "¿Cuántas unidades vendió el Producto A en enero?" |
| Contexto, causas, políticas, descripciones | **RAG** | "¿Qué dice la política de devoluciones?" |

**Si la respuesta es un número, es SQL. Siempre.** El RAG nunca provee valores
numéricos al informe: provee contexto que los explica.

## Alternativas consideradas

| Alternativa | Por qué se descartó |
|---|---|
| PostgreSQL + pgvector | Técnicamente sólido y unificaría ambos mundos. Se descarta porque T-SQL y SQL Server tienen más demanda en el mercado objetivo, y el proyecto busca demostrar esa competencia específica. |
| Vector DB SaaS (Pinecone, Weaviate Cloud) | Viola la restricción de costo cero y agrega dependencia de red y de free tiers que pueden cambiar. |
| Solo SQL Server con `VECTOR` nativo | Menos piezas, pero concentra todo el riesgo en la feature más nueva del stack. Queda como experimento documentado, no como base. |
| Solo FAISS, sin base relacional | Imposible: no hay agregaciones ni joins. Las métricas dejarían de ser confiables. |

## Consecuencias

**Positivas**
- Cada tipo de dato vive donde se consulta mejor.
- El retrieval se puede evaluar de forma aislada.
- Ninguna feature en evolución es dependencia crítica.
- El experimento con vector search nativo queda disponible como extensión y como
  material de comparación documentada.

**Negativas**
- Dos almacenes que mantener sincronizados en el ETL.
- El índice FAISS hay que reconstruirlo cuando cambian los documentos.
- Sin joins directos entre métricas y embeddings; la correlación se hace en la
  capa de aplicación.

## Seguridad

El acceso desde las tools del agente usa un **usuario de base de datos read-only**
y **queries parametrizadas**. Sin DDL, sin DML, sin SQL arbitrario generado por
el modelo. Ver ADR-004.
