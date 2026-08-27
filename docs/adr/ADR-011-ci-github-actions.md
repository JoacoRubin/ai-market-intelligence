# ADR-011 — CI con GitHub Actions, y lo que todavía no cubre

- **Estado:** Aceptado
- **Fecha:** 2026-08-26

## Contexto

`ruff`, `mypy` y `pytest` corrían solo a mano vía `tasks.ps1`. Nada impedía
que un commit con la suite rota llegara al repo — el README podía decir
"532 tests en verde" mientras la rama activa no lo estuviera, y nadie se
enteraba hasta la próxima corrida manual.

## Decisión

Un solo workflow (`.github/workflows/ci.yml`), un solo job: `ruff check`,
`mypy` strict, y `pytest -m "not db"`. Corre en cada push a cualquier rama
y en cada PR contra `master`.

**Deliberadamente NO corre:**
- **Tests marcados `db`** (requieren SQL Server). Levantarlo como
  `services:` del job es una extensión real y queda pendiente — no se
  armó acá porque agrega un healthcheck, un `db-init` completo y minutos
  de más al pipeline, y no era el alcance de esta pieza.

  > **Redis sí entró** como `services:` al sumarse la cola de trabajos
  > (ADR-012), y la diferencia con SQL Server es la que justifica el
  > tratamiento distinto: la imagen de Redis levanta en segundos y no
  > necesita esquema ni datos sembrados para que un test sea significativo.
- **Tests marcados `llm`** (invocan el modelo real). Ya vienen excluidos
  por default en `pyproject.toml` (`addopts = "... -m 'not llm'"`) — el
  propio proyecto ya decidió que esos no corren "en cada commit, sino
  cuando se toca el prompt o el modelo" (ver ese mismo comentario en
  `pyproject.toml`). CI hereda esa decisión, no la repite.

**Sí corre los tests marcados `rag`**, que sí estaban en el filo: cargan el
modelo real de embeddings (`intfloat/multilingual-e5-small`). Sin cachearlo,
cada run lo bajaría de nuevo — minutos de red por commit, contra un modelo
que no cambia. Se cachea por nombre de modelo con `actions/cache`, con el
mismo criterio de `HF_HUB_OFFLINE` que ya usa `tasks.ps1`: offline por
default, un step puntual lo apaga para la descarga inicial.

## Justificación

**Por qué no todo en un solo `uv sync`.** `--all-extras` en vez de listar
`rag`, `ml`, `seed`, `report`, `anthropic` a mano: la suite completa toca
los cinco (los tests de `rag`/`ml` importan esos módulos, y `seed` es lo que
usa el generador de datos sintéticos que esos mismos tests consumen).
Listarlos a mano es una lista que hay que acordarse de actualizar cada vez
que se agregue un grupo opcional nuevo; `--all-extras` no se desactualiza.

**Por qué `--frozen`.** Mismo criterio que ya fija ADR-010 para el
Dockerfile: falla si el lock no coincide con `pyproject.toml`, en vez de
resolver versiones distintas de las que el golden set tiene medidas.

## Alternativas consideradas

| Alternativa | Por qué se descartó |
|---|---|
| Levantar SQL Server como `services:` del job, correr la suite entera | Es la cobertura real que falta. Se descartó PARA ESTA pieza porque agrega un healthcheck, `db-init` completo y minutos de pipeline — alcance de una mejora futura, no de "armar CI" desde cero. |
| No cachear el modelo de embeddings, dejar que cada run lo baje | Simplifica el workflow en un paso, pero cada push a cualquier rama —el trigger elegido— dispara una descarga de red que no cambia entre commits. El costo de mantenimiento del cache (una key, un `actions/cache`) es menor que ese desperdicio repetido. |
| Trigger solo en PR contra `master` | Se prefirió sumar `push` a cualquier rama: en un repo de portfolio de una sola persona, enterarse de un test roto AL COMMITEAR (no recién al abrir el PR) es la señal que sirve. |

## Consecuencias

**Positivas**
- Un commit con la suite rota no llega en silencio: el badge del repo lo
  muestra.
- El cache de `uv` y del modelo de embeddings hace que los runs sucesivos
  sean rápidos — no se repaga el costo pesado (torch, sentence-transformers,
  descarga del modelo) en cada push.

**Negativas**
- **Cobertura incompleta a propósito**: los tests `db` no corren en CI
  todavía. Un PR puede estar verde en Actions y romper algo que solo un
  test contra SQL Server real detecta. Es un hueco conocido, no uno oculto.
- Si `pyproject.toml` suma un extra nuevo con una dependencia pesada,
  `--all-extras` lo instala en CI sin que nadie lo decida explícitamente ahí
  — la decisión de qué entra al lock ya se tomó en `pyproject.toml`, pero
  vale la pena recordarlo si el pipeline empieza a tardar de más.
