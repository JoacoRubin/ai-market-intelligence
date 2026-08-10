# ADR-005 — Reglas de negocio de los KPIs

- **Estado:** Aceptado
- **Fecha:** 2026-08-09

## Contexto

Los KPIs son la materia prima del informe. Todo lo que el agente afirma sobre
performance sale de estas cinco métricas: unidades, revenue, margen, crecimiento
y tasa de devolución.

El problema es que ninguna de las cinco tiene una definición única. "Revenue" y
"margen" suenan obvias hasta que hay que escribir el `WHERE`, y ahí aparecen
preguntas que la matemática no responde:

- ¿Una orden cancelada es una venta?
- ¿El revenue se informa antes o después de devoluciones?
- Con un producto en campaña al 25% off, ¿el margen se calcula sobre el precio
  de lista o sobre el que el cliente pagó?

**Estas decisiones no son técnicas, son de negocio.** Y tienen una propiedad
peligrosa: resueltas de forma distinta en dos lugares del sistema, producen un
informe que se contradice a sí mismo sin que ningún test de tipo lo note.

Además, un KPI mal definido no genera un error visible. Genera una frase
perfectamente redactada que dice algo falso. El LLM no tiene forma de detectarlo
—recibe el número ya calculado— y el lector tampoco.

## Decisión

### 1. Las órdenes canceladas NO cuentan como venta

Se excluye `status = 'cancelada'` de toda métrica de venta.

Una cancelación no generó ingreso. En el dataset son el 3,5% de las órdenes:
incluirlas desviaría sistemáticamente todas las métricas hacia arriba.

### 2. Las devoluciones NO se restan del revenue

El revenue se informa **bruto**, y la tasa de devolución se reporta como métrica
separada.

Netear una contra otra esconde el problema: un producto con mucho volumen y
muchas devoluciones se vería igual que uno con poco volumen y ninguna. Separadas,
el informe puede decir "vendió mucho **y** tiene un problema de calidad", que es
la afirmación útil.

### 3. El margen se calcula sobre el precio PAGADO

Se usa `order_items.unit_price` —el precio efectivamente cobrado, ya con
descuento aplicado— y no `products.price`.

Es la decisión de mayor impacto de las tres. Con una campaña al 25% off, calcular
el margen sobre el precio de lista lo infla en decenas de puntos porcentuales.
El informe diría que la campaña fue rentable justo cuando destruyó el margen.

### 4. Ausencia de dato devuelve `None`, nunca cero

- Sin ventas en el período → margen `None`, no `0%`.
- Sin revenue en el período previo → crecimiento `None`, no `0%`.
- Sin líneas vendidas → tasa de devolución `None`, no `0%`.

Un `0%` se lee como "se mantuvo estable", que es una afirmación sobre el negocio.
`None` se renderiza como `—` y significa "no hay dato", que es la verdad. La
diferencia importa: el informe no debe afirmar lo que no sabe.

## Cómo se verifica

Cada KPI se calcula **dos veces por caminos independientes**:

1. Una consulta T-SQL contra SQL Server.
2. Un cálculo en pandas sobre el mismo dataset generado en memoria.

Si los dos resultados no coinciden, una de las dos implementaciones está mal.
Es el principio de la partida doble aplicado a métricas: no se confía en un
único cálculo, se lo confronta con otro hecho por un camino distinto.

Además, cada regla tiene su **contraprueba**: un test que verifica que el
resultado NO coincide con el que daría la regla equivocada. Sin la contraprueba,
una query sin el filtro de canceladas pasaría igual si el cálculo en pandas
tuviera el mismo error, y la doble contabilidad dejaría de servir para nada.

Los tests que no encuentran su caso de prueba **fallan, no se saltean**: un test
que se saltea porque no encontró el escenario no está probando la regla, está
fingiendo que la probó.

## Consecuencias

**Positivas**
- Las tres reglas viven en un solo lugar del código y están documentadas acá.
- El agente no puede reinterpretarlas: recibe los números ya calculados.
- Cualquier cambio futuro rompe tests, que es exactamente lo que debe pasar.

**Negativas**
- Los tests requieren SQL Server levantado (marcados con `@pytest.mark.db`).
- Mantener dos implementaciones de cada KPI cuesta más que una. Se acepta: el
  costo de un KPI silenciosamente mal calculado es mucho mayor.

## Alternativas consideradas

| Alternativa | Por qué se descartó |
|---|---|
| Calcular los KPIs en pandas y no en SQL | Desperdicia el motor y no escala. Además, el proyecto busca demostrar T-SQL analítico. |
| Vistas SQL en lugar de consultas parametrizadas | Más limpio para consultas fijas, pero los rangos de fecha son dinámicos. Se puede revisar si aparecen consultas repetidas. |
| Permitir que el agente genere el SQL (text-to-SQL) | Deja las reglas de negocio en manos del modelo. Cada consulta podría resolverlas distinto. Ver ADR-004. |
| Netear devoluciones del revenue | Es defendible contablemente, pero esconde la señal de calidad que el sistema justamente debe detectar. |
