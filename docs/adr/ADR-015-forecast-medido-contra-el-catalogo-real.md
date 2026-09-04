# ADR-015 — El forecast se midió contra el catálogo real: un bug arreglado, dos ideas descartadas

- **Estado:** Aceptado
- **Fecha:** 2026-09-03

## Contexto

`ml/forecast.py::pronosticar` (Ridge + features de lag, versionado
`ridge_lags_v1`) existe desde la Fase 4 y tiene tests unitarios
(`tests/test_ml_backtest.py`) desde entonces — pero corridos siempre contra
arrays sintéticos armados a mano ("una serie con tendencia clara", "puro
ruido"). Nunca se había medido contra las 40 series reales del catálogo
(`dbo.order_items`), y no había ADR: la elección de Ridge sobre algo más
sofisticado vivía solo en un comentario de código.

El usuario preguntó si el sistema tenía forecast, y después si se podía
mejorar. Antes de proponer nada, se midió.

## Qué se midió

Backtest walk-forward (3 ventanas, horizonte 14 días) sobre los 40 productos
reales, con SQL Server levantado:

| | Resultado |
|---|---|
| El modelo le gana al naive (`mape_modelo < mape_baseline`) | 26/40 productos (65%) |
| MAPE promedio — modelo | 78,8% |
| MAPE promedio — naive | 78,6% |
| MAPE promedio — estacional (mismo día, semana anterior) | 83,5% |

**El modelo gana en la mayoría de los productos, pero el promedio queda
prácticamente empatado con el naive.** La razón no es que el modelo sea
mediocre en general: un solo producto (`P001`) arrastra el promedio entero.

## El bug real: recursión autorregresiva inestable

`P001` daba 739,9% de MAPE — un orden de magnitud peor que cualquier otro
producto. Diagnóstico, no suposición: se reprodujo la ventana de backtest a
mano. `P001` tiene 94% de días en cero (máximo histórico: 10 u/día, contra
1.104-1.703 unidades totales de productos normales). En esa ventana, Ridge
ajustó `lag_1=0,863` y `lag_7=1,59` — la suma de los coeficientes de lag
supera 1.

`_predecir_recursivo` realimenta su propia salida como `lag_1`/`lag_7` del
paso siguiente. Con esa suma de coeficientes, cada paso amplifica al
anterior en vez de decaer: la proyección fue `18 → 23,5 → 32,2 → ... →
128,3` sobre una historia que nunca superó 10. Es un proceso autorregresivo
matemáticamente inestable, no un error de datos ni un caso raro — cualquier
producto de bajo volumen puede pisar la misma trampa.

**Por qué el backtest no alcanzaba para protegerlo**: `pronosticar()` decide
si usar el modelo o el baseline mirando si el backtest superó al naive — pero
el backtest entrena modelos por VENTANA (menos datos, en el pasado), y el
pronóstico real entrena un modelo NUEVO sobre TODA la serie. Que el primero
haya sido estable no garantiza que el segundo lo sea: son ajustes distintos,
sobre datos distintos.

### La decisión

`_proyeccion_es_estable()` en `ml/forecast.py`: un guard que mira el
RESULTADO de la proyección contra una cota derivada de la propia historia
(8x el máximo histórico observado — generoso para no cortar un crecimiento
legítimo, finito para atajar la divergencia). No mira los coeficientes de
Ridge: depende de la combinación exacta de features y sería frágil ante
cualquier cambio en `construir_features`. Si la proyección diverge,
`pronosticar()` cae al baseline — mismo mecanismo que ya usaba para "el
modelo no le ganó al naive", ahora con una segunda causa posible.

El backtest de `ml/backtest.py` **no** pasa por este guard, a propósito:
sigue midiendo el modelo crudo, con sus fallas — es la medición honesta que
permitió encontrar el bug. Filtrar el backtest lo hubiera ocultado.

`tests/test_ml_forecast.py` no existía — se creó. Incluye la reproducción
exacta del caso P001 (vía `monkeypatch` sobre `_predecir_recursivo`, para no
depender de que Ridge reproduzca la inestabilidad por azar en CI).

## Dos ideas que se probaron y se descartaron

Ambas con el mismo criterio que ya usan ADR-003 (revisión) y las notas de
`keep_alive`/`num_ctx`: medidas contra el catálogo real, no elegidas por
intuición ni mantenidas por sesgo de esfuerzo invertido.

**`log1p(y)` antes de ajustar Ridge.** Hipótesis razonable: las ventas son un
conteo no-negativo y sesgado (muchos ceros, algún pico), `log1p` es el
tratamiento clásico para eso. Implementado, medido — y descartado por algo
peor que "no ayudó": **crasheó**. Revertir con `expm1` convierte una
tendencia ADITIVA del target en una EXPONENCIAL en unidades reales; bajo la
recursión, una predicción grande desbordó a `inf` (`expm1` de un número
suficientemente grande), ese `inf` se realimentó como `lag_1` del paso
siguiente, y `Ridge.predict()` rechazó la entrada con una excepción de
sklearn no atrapada. Un test unitario limpio (`_serie_con_tendencia`, sin
picos) ya lo delataba antes de tocar el catálogo real: MAPE 98,5% contra
7,6% del naive en una serie donde el modelo lineal ganaba claro.

**`lag_14`** (sumado a `lag_1`/`lag_7`). Medido contra el catálogo real: 1
producto más ganado (27/40 vs 26/40), pero MAPE promedio **peor** (79,2% vs
78,8%). Dentro del ruido de la medición, no una mejora real — se descartó
para no sumar una feature que hay que mantener sin evidencia de que la
pague.

## Alcance: qué no cambió

- El modelo sigue siendo Ridge + lags — la revisión no encontró motivo para
  reemplazarlo por algo más sofisticado. "Difícil de superar y mucho más
  fácil de explicar" (comentario original de `entrenar_modelo`) se sostiene
  con los números en la mano, no solo como argumento a priori.
- Ningún golden set case dispara `pronosticar()` hoy — se verificó antes de
  considerar correr el eval completo de 42 minutos, que hubiera sido tiempo
  gastado sin ninguna señal sobre este cambio. Es una cobertura pendiente,
  no arreglada en este ADR.

## Alternativas consideradas

| Alternativa | Por qué se descartó |
|---|---|
| Modelo de gradient boosting (LightGBM/XGBoost) | El empate Ridge-naive en promedio no viene de que Ridge sea débil — viene de un bug de estabilidad puntual, ya arreglado. Agregar un modelo más complejo antes de confirmar que el simple, arreglado, sigue empatando sería resolver el problema equivocado. |
| Clipear los coeficientes de Ridge en vez de mirar el resultado | Frágil: depende de la combinación exacta de `construir_features`, que puede cambiar. El guard sobre el resultado no. |
| Filtrar el bug también dentro de `backtest()` | Hubiera ocultado la medición honesta que permitió encontrarlo — el punto de `backtest()` es medir el modelo crudo. |

## Consecuencias

**Positivas**

- Un bug real de producción arreglado, con reproducción determinística en
  test.
- Dos ideas descartadas con evidencia, no repetidas la próxima vez que
  alguien piense "esto seguro ayuda".
- `ml/forecast.py::pronosticar` tiene test propio por primera vez.

**Negativas, y hay que decirlas**

- El empate MAPE modelo/naive en promedio **sigue así** — arreglar P001 quita
  el peor outlier, no mejora el resto del catálogo. Ridge sigue ganando en
  65% de los productos, ni más ni menos que antes del fix.
- Ningún caso publicado (golden set ni replay) ejercita `pronosticar()`
  todavía — la capacidad existe, está mejor probada que antes, y sigue sin
  vidriera.
