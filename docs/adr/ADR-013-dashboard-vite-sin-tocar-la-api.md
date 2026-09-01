# ADR-013 — El dashboard corre en Vite con proxy de dev, sin tocar la API

- **Estado:** Aceptado
- **Fecha:** 2026-09-01

## Contexto

Hasta acá el único artefacto visual del proyecto era `docs/replay/`: un sitio
estático, sin build ni dependencias, que reproduce **corridas grabadas** del
agente. Es deliberadamente de solo lectura — muestra qué pasó, no permite
lanzar nada nuevo.

La API (`apps/api/`) ya está construida y verificada: 712 tests corriendo
contra SQL Server real, el ciclo completo `POST /analyses` → polling →
`GET /analyses/{id}` probado en vivo, con tiempos medidos de 6 s (rechazo en
router) a ~2m35s (comparación en lenguaje natural). Falta la Fase 7 del
roadmap del README: "UI y visualización — React + TypeScript + Vite", hoy en
`0%`, sin ninguna carpeta `apps/web/` ni rastro previo.

El pedido es puntual: lanzar un análisis contra el agente **en vivo**, ver el
detalle completo del resultado (no un resumen), y descargar el informe en
PDF. Es la versión en vivo de lo que el replay ya hace grabado — y por eso la
decisión central de este ADR es reusar su lenguaje visual, no inventar uno
nuevo.

Verificado en código durante el diseño, no asumido: `estado == "completado"`
con `informe == null` es un camino real y distinto de `"fallido"`
(`application/analisis.py:83-104`). Cuando el router descarta la consulta por
fuera de alcance, el grafo termina sin excepción y el análisis igual
transiciona a `COMPLETADO`, con `informe=None`. `FALLIDO` es solo para
excepciones no controladas. El dashboard tiene que distinguir estas dos
causas del mismo bloque de rechazo, tal como ya lo hace
`docs/replay/replay.js::construirRechazo`.

## Decisión

**Vite + React + TypeScript en `apps/web/`**, sin router, sin librería de
estado (Redux/Zustand) ni de fetching de datos (React Query/SWR). El flujo
completo es una sola pantalla con una máquina de estados chica
(`formulario → polling → detalle`) — nada que bookmarkear, una sola sesión,
un solo consumidor de la API. Agregar esas dependencias traería vocabulario
nuevo (query keys, reducers, acciones) a un proyecto que hoy no lo tiene en
ningún lado, para un caso que no lo necesita.

**Proxy de dev de Vite en vez de `CORSMiddleware`.** `vite.config.ts` reenvía
`/analyses`, `/products` y `/health` a `http://127.0.0.1:8000`; el navegador
solo habla con `localhost:5173`. Cero CORS, cero header nuevo, cero import en
`apps/api/main.py`.

**`theme.css` es una copia literal de `docs/replay/estilos.css`**, no un
import cruzado. Reusa la paleta ya validada contra daltonismo (azul `#2a78d6`
↔ naranja `#eb6834`, ΔE 24.7/26.8 contra objetivo 8) y los componentes ya
resueltos: `.sello--{tipo}`, `.cita`, `.cinta`/`.tramo`, `.barra`,
`.alerta--{aviso|grave}`. `app.css` es estrictamente aditivo: nunca redefine
un token de `theme.css`.

## Por qué proxy y no CORS

`apps/api/main.py` es un archivo con 712 tests pasando y un docstring que
explica el porqué de cada código de estado HTTP. `CORSMiddleware` es en
teoría trivial de agregar, pero abre una pregunta que el proyecto ya
contestó en otro lado: ¿qué orígenes se permiten en producción? — y ADR-006
ya estableció que no hay producción cloud, todo corre local. El proxy de
Vite resuelve el problema exactamente en la capa que lo generó (el dev
server), sin pedirle nada a un backend que no lo necesitaba.

Costo aceptado: el proxy solo existe bajo `npm run dev`. No hay hoy una
historia de "build de producción servido standalone" — ver Alcance, abajo.

## Por qué copiar `theme.css` y no importarlo

`docs/replay/estilos.css` está protegido por su propio contrato
(`tests/test_replay_contrato_sitio.py`). Importarlo directo desde
`apps/web/` (Vite lo permite vía `server.fs.allow`) acoplaría el build del
dashboard a la estructura de un sitio que evoluciona por su cuenta — un
cambio en el replay podría romper el dashboard sin que nadie lo haya tocado,
y viceversa.

El costo es sincronización manual: si los tokens de color cambian en el
original, hay que actualizar `theme.css` a mano, y no hay test hoy que lo
detecte. Se acepta porque los tokens cambian rarísima vez (están validados
contra daltonismo, cambiarlos implica re-medir) y porque el desacople de dos
contratos verificados por separado vale más que ahorrarse una copia de 400
líneas de CSS.

## Cómo quedó repartido el código

| Carpeta | Responsabilidad |
|---|---|
| `apps/web/src/api/` | `types.ts` (espejo manual del contrato Pydantic) y `client.ts` (fetch tipado, normaliza el `detail` de FastAPI a `string[]`) |
| `apps/web/src/hooks/` | `useAnalysisPoll.ts` — el polling, con su cleanup |
| `apps/web/src/lib/` | `formato.ts` y `etapas.ts` — formateadores y la lista de etapas LLM, portados literal de `docs/replay/replay.js` |
| `apps/web/src/components/` | Un componente por sección del informe, cada uno mapeado 1:1 a un campo de `Report` |
| `apps/web/src/styles/` | `theme.css` (copia) + `app.css` (aditivo) |

`SolicitudForm` valida client-side la misma regla XOR que
`apps/api/schemas.py::SolicitudAnalisis._forma_coherente` — pero **espeja**
esa regla, no la reemplaza: el 422 real del backend sigue siendo la
autoridad si algo se cuela.

## La trampa que costó entenderla: completado no siempre trae informe

Leer solo el schema de `Analisis` (`informe: Report | None`) no dice *por
qué* puede ser `None` con `estado == "completado"` — parece que "completado"
implica éxito con resultado. Hay que leer `application/analisis.py` para ver
que el router puede descartar la consulta sin que eso sea una falla: el
grafo corrió bien, simplemente no había nada que analizar. `Rechazo.tsx`
cubre las dos causas (`error` presente, o `informe === null` sin error) en
el mismo bloque visual, con el mismo texto que ya usa el replay: "un agente
que siempre intenta responder siempre responde algo, aunque no tenga con
qué."

## Alcance: qué NO hace

- **No hay historial de análisis pasados.** `GET /analyses` ya existe y no
  cuesta nada agregarlo después; es una pantalla distinta (lista +
  navegación) que el pedido no incluyó.
- **No hay autenticación.** Herramienta local, un solo operador, contra
  `localhost:8000` en la misma máquina.
- **No hay tests de frontend.** El repo no tiene tooling de testing JS hoy
  (Vitest/RTL); elegirlo es una decisión de arquitectura propia que no entra
  de paso en este ADR.
- **No se diseña activamente para mobile/responsive.** El `@media
  (max-width: 900px)` de `.tablero` viene gratis al copiar `estilos.css`,
  pero no se prueba para eso.
- **Sin animación de "reproducir" la traza.** Tiene sentido en el replay,
  que es un artefacto de portfolio pensado para mostrarse; acá el usuario ve
  la corrida real en vivo — la cinta estática con proporciones reales ya
  comunica todo.
- **Sin build de producción ni despliegue del sitio.** v1 es `npm run dev`
  local, consistente con ADR-006.
- **Sin botón "cancelar" (`DELETE /analyses/{id}`).** La ruta ya existe;
  queda como candidato barato si una corrida larga en lenguaje natural se
  siente incómoda en la práctica.

## Alternativas consideradas

| Alternativa | Por qué se descartó |
|---|---|
| Next.js | SSR y routing de archivos no aportan nada a una app de una pantalla contra un backend local, y trae su propio servidor que competiría con el de FastAPI. |
| Ampliar el sitio de replay estático en vez de una app nueva | El replay es deliberadamente de solo lectura sobre corridas grabadas. Mezclar "reproducir historia" con "lanzar y pollear en vivo" le rompe el propósito a los dos. |
| `CORSMiddleware` en `apps/api/main.py` | Ver arriba: reabre un archivo verificado por una necesidad puramente de dev server. |
| React Query / SWR | Un solo endpoint, un solo consumidor a la vez, sin caché entre pantallas ni invalidación cruzada — exactamente el caso que resuelven de más. |
| Redux / Zustand | El grafo de estado son cuatro variables; cabe en `useState` sin traer un vocabulario nuevo al proyecto. |

## Consecuencias

**Positivas**
- Dashboard interactivo real sobre el agente en vivo, con costo cero y sin
  tocar un backend ya verificado.
- El lenguaje visual queda consistente entre el replay (historia grabada) y
  el dashboard (corrida en vivo): un usuario que vio uno reconoce el otro.
- Cero dependencias nuevas más allá de React/Vite/TypeScript.

**Negativas**
- `theme.css` puede driftear del original si alguien edita
  `docs/replay/estilos.css` y se olvida de sincronizar — sin test que lo
  detecte hoy.
- Sin tests de frontend: la verificación de esta fase es manual (ver el plan
  de verificación end-to-end del PR), no automatizada como el resto del
  repo.
- Sin historial de análisis pasados en v1 — declarado, no escondido.
