/* Replay del agente — carga las capturas y las reproduce.
 *
 * Sin dependencias externas y sin build. No es minimalismo por deporte: el
 * proyecto entero corre sin servicios de terceros, y un sitio que se cae porque
 * una CDN cambió una URL contradiría lo único que este demo intenta demostrar.
 *
 * Todo el texto que viene de los datos se escribe con textContent, nunca con
 * innerHTML. Los informes los redacta un modelo de lenguaje: tratar esa salida
 * como HTML confiable sería exactamente el error que el sistema evita en todas
 * las demás capas.
 */

'use strict';

const DATOS = 'data';

/* Qué etapas invocan al modelo. Sale del grafo (agent/graph.py): de las seis,
 * el router clasifica la intención y el synthesizer redacta. Las otras cuatro
 * son software clásico. Si el grafo cambia, esta lista cambia con él. */
const ETAPAS_LLM = new Set(['router', 'synthesizer']);

/* Qué etapas pegan contra una fuente PÚBLICA en vez de los datos propios.
 * Mismo criterio que `apps/web/src/lib/etapas.ts` (corregido 2026-09-03,
 * ver ADR-014): hasta acá esta lista no existía y `edgar_tool` se mostraba
 * igual que `sql_tool`/`rag_tool` — indistinguible de una consulta a la base
 * propia, cuando en realidad es una llamada a SEC EDGAR. */
const ETAPAS_EXTERNAS = new Set(['edgar_tool']);

function origenEtapa(nodo) {
  if (ETAPAS_LLM.has(nodo)) return 'llm';
  if (ETAPAS_EXTERNAS.has(nodo)) return 'externo';
  return 'interno';
}
const SUFIJO_ORIGEN = { llm: 'llm', interno: 'det', externo: 'ext' };
const ETIQUETA_TIPO_ORIGEN = {
  llm: 'Modelo',
  interno: 'Determinística',
  externo: 'Determinística (SEC EDGAR)',
};

/* Cuánto dura la reproducción en pantalla. Una corrida real ronda el minuto;
 * hacer esperar eso al visitante sería reproducir el problema que este sitio
 * existe para resolver. Los tiempos que se MUESTRAN son los reales. */
const DURACION_PANTALLA_MS = 3200;

/* Se toma `.format` y no el objeto: `Intl.NumberFormat` devuelve un formateador,
 * no una función, y llamarlo directo tira `TypeError`. `format` es un getter que
 * entrega la función YA vinculada — está en la especificación para poder pasarla
 * suelta, y es lo que la vuelve invocable acá. */
const nf = new Intl.NumberFormat('es-AR').format;
const nf1 = new Intl.NumberFormat('es-AR', { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format;
const nf2 = new Intl.NumberFormat('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format;

const menosMovimiento = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

let manifiesto = null;
let animacionEnCurso = null;

/* --- utilidades ------------------------------------------------------------ */

function el(tag, clase, texto) {
  const n = document.createElement(tag);
  if (clase) n.className = clase;
  if (texto !== undefined && texto !== null) n.textContent = texto;
  return n;
}

function duracion(ms) {
  if (ms < 1000) return `${nf(ms)} ms`;
  return `${nf1(ms / 1000)} s`;
}

/* Un `null` en el modelo significa "no se sabe", no "cero". Devolver 0 acá
 * convertiría un dato ausente en una afirmación falsa, y el informe la mostraría
 * como un hecho. El guion largo es la representación honesta. */
function oGuion(valor, formatear) {
  return valor === null || valor === undefined ? '—' : formatear(valor);
}

function fecha(iso) {
  const d = new Date(iso);
  return d.toLocaleDateString('es-AR', { day: 'numeric', month: 'long', year: 'numeric' });
}

/* --- arranque -------------------------------------------------------------- */

async function iniciar() {
  try {
    const r = await fetch(`${DATOS}/manifiesto.json`, { cache: 'no-cache' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    manifiesto = await r.json();
  } catch (e) {
    mostrarSinDatos(e);
    return;
  }

  document.getElementById('meta-fecha').textContent = fecha(manifiesto.capturado_en);
  document.getElementById('meta-modelo').textContent = manifiesto.modelo_llm;
  document.getElementById('meta-comando').textContent = manifiesto.reproducible_con;

  construirIndice(manifiesto.casos);
  if (manifiesto.casos.length) seleccionar(manifiesto.casos[0].id);
}

/* Una pantalla vacía es una invitación a actuar: dice qué falta y qué comando
 * lo arregla, en vez de un "error al cargar" que no ayuda a nadie. */
function mostrarSinDatos(error) {
  const destino = document.getElementById('detalle');
  destino.textContent = '';

  const caja = el('div', 'vacio');

  if (location.protocol === 'file:') {
    caja.appendChild(el('h2', null, 'Abrilo con un servidor, no como archivo'));
    caja.appendChild(el('p', null,
      'El navegador bloquea la lectura de los JSON cuando la página se abre ' +
      'directamente desde el disco. Levantá un servidor local:'));
    caja.appendChild(el('pre', null, '.\\tasks.ps1 replay-servir'));
  } else {
    caja.appendChild(el('h2', null, 'Todavía no hay capturas'));
    caja.appendChild(el('p', null,
      'El sitio lee ejecuciones grabadas del agente y acá no hay ninguna. ' +
      'Generalas con SQL Server levantado y Ollama respondiendo:'));
    caja.appendChild(el('pre', null, '.\\tasks.ps1 db-up\n.\\tasks.ps1 replay'));
    caja.appendChild(el('p', null, `Detalle técnico: ${error.message}`));
  }

  destino.appendChild(caja);
}

/* --- índice ---------------------------------------------------------------- */

function construirIndice(casos) {
  const lista = document.getElementById('indice-lista');
  lista.textContent = '';

  for (const caso of casos) {
    const li = document.createElement('li');
    const boton = el('button', 'caso');
    boton.type = 'button';
    boton.dataset.id = caso.id;
    boton.setAttribute('aria-current', 'false');

    boton.appendChild(el('span', 'caso__consulta', caso.consulta));

    const pie = el('span', 'caso__pie');
    pie.appendChild(el('span', null, caso.intencion || 'sin intención'));
    pie.appendChild(el('span', null, duracion(caso.duracion_total_ms)));
    boton.appendChild(pie);

    boton.addEventListener('click', () => seleccionar(caso.id));
    li.appendChild(boton);
    lista.appendChild(li);
  }
}

async function seleccionar(id) {
  for (const b of document.querySelectorAll('.caso')) {
    b.setAttribute('aria-current', String(b.dataset.id === id));
  }

  if (animacionEnCurso) { cancelAnimationFrame(animacionEnCurso); animacionEnCurso = null; }

  const destino = document.getElementById('detalle');
  destino.textContent = '';
  destino.appendChild(el('div', 'cargando', 'Cargando ejecución…'));

  let captura;
  try {
    const r = await fetch(`${DATOS}/casos/${encodeURIComponent(id)}.json`, { cache: 'no-cache' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    captura = await r.json();
  } catch (e) {
    destino.textContent = '';
    const caja = el('div', 'vacio');
    caja.appendChild(el('h2', null, 'No se pudo leer esta ejecución'));
    caja.appendChild(el('p', null, e.message));
    destino.appendChild(caja);
    return;
  }

  destino.textContent = '';
  destino.appendChild(construirDetalle(captura));
}

/* --- detalle --------------------------------------------------------------- */

function construirDetalle(captura) {
  const molde = document.getElementById('molde-detalle');
  const nodo = molde.content.cloneNode(true);

  nodo.querySelector('[data-consulta]').textContent = captura.consulta;

  construirInterpretacion(nodo.querySelector('[data-interpretacion]'), captura);
  construirTraza(nodo, captura);

  if (captura.plan && captura.plan.length) {
    nodo.querySelector('[data-seccion="plan"]').hidden = false;
    construirPlan(nodo.querySelector('[data-plan]'), captura.plan);
  }

  if (captura.informe) {
    nodo.querySelector('[data-seccion="informe"]').hidden = false;
    construirInforme(nodo.querySelector('[data-informe]'), captura);
  } else {
    nodo.querySelector('[data-seccion="rechazo"]').hidden = false;
    construirRechazo(nodo.querySelector('[data-rechazo]'), captura);
  }

  return nodo;
}

function construirInterpretacion(destino, captura) {
  const campos = [
    ['Intención', captura.intencion || '—'],
    ['Entidades', captura.entidades.length ? captura.entidades.join(', ') : '—'],
    ['Período', captura.periodo ? `${captura.periodo.desde} → ${captura.periodo.hasta}` : '—'],
    ['Herramientas', `${captura.llamadas_tools} llamada(s)`],
    ['Replanificaciones', String(captura.reintentos)],
  ];

  for (const [clave, valor] of campos) {
    const ficha = el('span', 'ficha');
    ficha.appendChild(el('span', 'ficha__clave', clave));
    ficha.appendChild(el('span', 'ficha__valor', valor));
    destino.appendChild(ficha);
  }
}

/* --- la cinta de traza ----------------------------------------------------- */

function construirTraza(raiz, captura) {
  const cinta = raiz.querySelector('[data-cinta]');
  const trace = captura.trace || [];
  const total = trace.reduce((a, p) => a + p.duracion_ms, 0);

  const boton = raiz.querySelector('[data-reproducir]');
  const botonTexto = raiz.querySelector('[data-reproducir-texto]');
  const reloj = raiz.querySelector('[data-reloj]');
  const aviso = raiz.querySelector('[data-velocidad]');

  if (!trace.length) {
    cinta.appendChild(el('p', null, 'Esta ejecución no registró etapas.'));
    boton.disabled = true;
    return;
  }

  const factor = Math.max(1, Math.round(total / DURACION_PANTALLA_MS));
  aviso.textContent = menosMovimiento
    ? 'Los tiempos mostrados son los reales de la corrida.'
    : `Reproducción acelerada ×${nf(factor)}. Los tiempos son los reales.`;

  cinta.setAttribute('aria-label',
    `Traza de ${trace.length} etapas, ${duracion(total)} en total: ` +
    trace.map((p) => `${p.nodo} ${duracion(p.duracion_ms)}`).join(', '));

  /* Pipeline de nodos — mismo criterio que Traza.tsx del dashboard: sale de
   * la traza REAL, no de una lista fija inventada. `aria-hidden` porque es
   * un resumen visual de lo que la cinta de abajo ya anuncia por
   * `aria-label`. */
  const pipeline = el('div', 'pipeline');
  pipeline.setAttribute('aria-hidden', 'true');
  trace.forEach((paso, i) => {
    const origen = origenEtapa(paso.nodo);
    const envoltorioPaso = el('div', 'pipeline__paso');

    const nodoEl = el('div', 'pipeline__nodo');
    const punto = el('span', `pipeline__punto pipeline__punto--${SUFIJO_ORIGEN[origen]}`);
    punto.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" ' +
      'stroke="currentColor" stroke-width="3"><path d="m5 13 4 4L19 7"/></svg>';
    nodoEl.appendChild(punto);
    nodoEl.appendChild(el('span', 'pipeline__nombre mono', paso.nodo));
    nodoEl.appendChild(el('span', 'pipeline__duracion mono', duracion(paso.duracion_ms)));
    envoltorioPaso.appendChild(nodoEl);

    if (i < trace.length - 1) envoltorioPaso.appendChild(el('span', 'pipeline__conector'));
    pipeline.appendChild(envoltorioPaso);
  });
  // `raiz.querySelector(...)` y no `cinta.parentNode`: el segundo asume que
  // `cinta` ya está insertada en un árbol con padre — cierto en un navegador
  // real, falso en `harness_dom.js` (DOM mínimo, sin parentNode) mientras el
  // molde todavía es un fragmento suelto. Pedirle el contenedor a `raiz`
  // directamente no depende de esa asunción.
  raiz.querySelector('[data-seccion="traza"]').insertBefore(pipeline, cinta);

  const rellenos = [];
  for (const paso of trace) {
    const origen = origenEtapa(paso.nodo);
    const proporcion = total ? paso.duracion_ms / total : 0;

    // "interno" no lleva modificador — la regla base .tramo ya es la
    // apariencia determinística, mismo criterio que Traza.tsx.
    const claseOrigen = origen === 'interno' ? '' : ` tramo--${SUFIJO_ORIGEN[origen]}`;
    const tramo = el('div', `tramo${claseOrigen}`);
    tramo.style.flexGrow = String(Math.max(proporcion, 0.004));
    tramo.style.flexBasis = '0';
    if (proporcion < 0.08) tramo.classList.add('tramo--angosto');
    tramo.title = `${paso.nodo} · ${duracion(paso.duracion_ms)}${paso.tool ? ` · ${paso.tool}` : ''}`;

    const relleno = el('div', 'tramo__relleno');
    tramo.appendChild(relleno);
    tramo.appendChild(el('span', 'tramo__rotulo', paso.nodo));

    cinta.appendChild(tramo);
    rellenos.push(relleno);
  }

  /* La leyenda del molde HTML solo trae dos ítems (det/llm, de antes de
   * ADR-014) — se suma el tercero acá en vez de tocar `index.html`, mismo
   * lugar donde ya se arma el resto del DOM dinámico. */
  const leyenda = raiz.querySelector('.leyenda');
  if (leyenda && !leyenda.querySelector('.leyenda__marca--ext')) {
    const item = document.createElement('li');
    item.appendChild(el('span', 'leyenda__marca leyenda__marca--ext'));
    item.appendChild(document.createTextNode(' Determinística (SEC EDGAR)'));
    leyenda.insertBefore(item, leyenda.lastElementChild);
  }

  reloj.textContent = nf1(total / 1000);

  const conLlm = trace.filter((p) => ETAPAS_LLM.has(p.nodo));
  const msLlm = conLlm.reduce((a, p) => a + p.duracion_ms, 0);
  const tesis = raiz.querySelector('[data-tesis]');
  tesis.appendChild(el('strong', null,
    `${conLlm.length} de ${trace.length} etapas usan el modelo`));
  tesis.appendChild(document.createTextNode(
    total
      ? ` — y se llevan el ${nf(Math.round((msLlm / total) * 100))} % del tiempo. ` +
        'Las otras son software clásico: ningún número del informe sale del modelo.'
      : '.'));

  construirTablaTraza(raiz.querySelector('[data-tabla-traza]'), trace, total);

  boton.addEventListener('click', () => {
    if (animacionEnCurso) { cancelAnimationFrame(animacionEnCurso); animacionEnCurso = null; }
    animar(cinta, rellenos, trace, total, reloj, botonTexto);
  });
}

function animar(cinta, rellenos, trace, total, reloj, botonTexto) {
  if (menosMovimiento) {
    reloj.textContent = nf1(total / 1000);
    return;
  }

  cinta.dataset.animando = 'true';
  botonTexto.textContent = 'Reproduciendo…';
  rellenos.forEach((r) => { r.style.transform = 'scaleX(0)'; });

  const inicios = [];
  let acumulado = 0;
  for (const paso of trace) { inicios.push(acumulado); acumulado += paso.duracion_ms; }

  const arranque = performance.now();

  function marco(ahora) {
    const avance = Math.min((ahora - arranque) / DURACION_PANTALLA_MS, 1);
    const msReales = avance * total;

    trace.forEach((paso, i) => {
      const local = paso.duracion_ms
        ? (msReales - inicios[i]) / paso.duracion_ms
        : 1;
      rellenos[i].style.transform = `scaleX(${Math.min(Math.max(local, 0), 1)})`;
    });

    reloj.textContent = nf1(msReales / 1000);

    if (avance < 1) {
      animacionEnCurso = requestAnimationFrame(marco);
    } else {
      animacionEnCurso = null;
      cinta.dataset.animando = 'false';
      botonTexto.textContent = 'Reproducir de nuevo';
    }
  }

  animacionEnCurso = requestAnimationFrame(marco);
}

function construirTablaTraza(cuerpo, trace, total) {
  for (const paso of trace) {
    const origen = origenEtapa(paso.nodo);
    const fila = document.createElement('tr');

    const celdaNodo = document.createElement('td');
    celdaNodo.appendChild(el('span', `punto punto--${SUFIJO_ORIGEN[origen]}`));
    celdaNodo.appendChild(el('span', 'mono', paso.nodo));
    if (paso.tool) {
      celdaNodo.appendChild(el('span', null, ' '));
      celdaNodo.appendChild(el('span', 'mono', `(${paso.tool})`));
    }
    fila.appendChild(celdaNodo);

    fila.appendChild(el('td', null, ETIQUETA_TIPO_ORIGEN[origen]));

    const celdaMs = el('td', 'num', duracion(paso.duracion_ms));
    fila.appendChild(celdaMs);

    fila.appendChild(el('td', 'num',
      total ? `${nf1((paso.duracion_ms / total) * 100)} %` : '—'));

    cuerpo.appendChild(fila);
  }
}

/* --- plan ------------------------------------------------------------------ */

function construirPlan(destino, plan) {
  for (const paso of plan) {
    const li = el('li', 'plan__paso');
    li.appendChild(el('p', 'plan__tool mono', paso.tool));
    if (paso.razon) li.appendChild(el('p', 'plan__razon', paso.razon));
    destino.appendChild(li);
  }
}

/* --- informe --------------------------------------------------------------- */

function construirInforme(destino, captura) {
  const informe = captura.informe;
  const porFuente = new Map((informe.fuentes || []).map((f) => [f.id, f]));

  seccionAfirmaciones(destino, 'Resumen ejecutivo', informe.resumen_ejecutivo, porFuente);
  seccionMetricas(destino, informe.metricas);
  seccionPredicciones(destino, informe.predicciones);
  seccionAfirmaciones(destino, 'Contexto de mercado', informe.contexto_mercado, porFuente);
  seccionAfirmaciones(destino, 'Recomendaciones', informe.recomendaciones, porFuente);
  seccionFuentes(destino, informe.fuentes);
  seccionAlertas(destino, informe.advertencias || captura.advertencias);
  seccionLimitaciones(destino, informe.limitaciones);

  const enlace = el('a', 'descarga', 'Descargar el informe en PDF');
  enlace.href = `${DATOS}/pdf/${encodeURIComponent(captura.id)}.pdf`;
  enlace.setAttribute('download', `informe-${captura.id}.pdf`);
  destino.appendChild(enlace);
}

function seccionAfirmaciones(destino, titulo, afirmaciones, porFuente) {
  if (!afirmaciones || !afirmaciones.length) return;

  const bloque = el('div', 'subbloque');
  bloque.appendChild(el('p', 'etiqueta', titulo));

  const lista = el('ul', 'afirmaciones');
  for (const a of afirmaciones) {
    const li = el('li', 'afirmacion');
    li.appendChild(el('span', `sello sello--${a.tipo}`, a.tipo));

    const texto = el('p', 'afirmacion__texto', a.texto);
    if (a.fuentes && a.fuentes.length) {
      const citas = el('span', 'citas');
      for (const id of a.fuentes) {
        const f = porFuente.get(id);
        const chip = el('span', 'cita', id);
        chip.title = f ? `${f.tipo} · ${f.referencia}` : 'fuente no declarada';
        citas.appendChild(chip);
      }
      texto.appendChild(citas);
    }
    li.appendChild(texto);
    lista.appendChild(li);
  }

  bloque.appendChild(lista);
  destino.appendChild(bloque);
}

/* Signo de una variación: no es "el número es positivo", es "la flecha va en
 * el sentido bueno" — por eso `invertido` existe (una tasa de devolución que
 * BAJA es una mejora, aunque el número tenga signo negativo). Mismo criterio
 * que `MetricasTabla.tsx` del dashboard. */
function signoDelta(valor, invertido) {
  if (valor === null || valor === undefined || valor === 0) return 'neutro';
  const sube = valor > 0;
  return sube !== invertido ? 'positivo' : 'negativo';
}

function filaDelta(etiqueta, valor, invertido) {
  const fila = el('div', 'kpi-card__fila');
  fila.appendChild(el('span', 'kpi-card__fila-etiqueta', etiqueta));
  const s = signoDelta(valor, invertido);
  const span = el('span', `kpi-card__fila-valor delta delta--${s}`);
  if (valor === null || valor === undefined) {
    span.textContent = '—';
  } else {
    const flecha = valor > 0 ? '↑' : valor < 0 ? '↓' : '→';
    span.textContent = `${flecha} ${nf1(Math.abs(valor))} %`;
  }
  fila.appendChild(span);
  return fila;
}

function seccionMetricas(destino, metricas) {
  if (!metricas || !metricas.length) return;

  const bloque = el('div', 'subbloque');
  bloque.appendChild(el('p', 'etiqueta', 'KPIs calculados por SQL'));

  const grilla = el('div', 'kpi-grid');
  for (const m of metricas) {
    const card = el('article', 'kpi-card');
    card.setAttribute('aria-label', `KPIs de ${m.nombre}`);

    const cabecera = el('header', 'kpi-card__encabezado');
    cabecera.appendChild(el('span', 'mono kpi-card__id', m.product_id));
    cabecera.appendChild(el('span', 'kpi-card__nombre', m.nombre));
    card.appendChild(cabecera);

    const hero = el('p', 'kpi-card__hero');
    hero.appendChild(el('span', 'kpi-card__hero-cifra', `USD ${nf2(m.revenue)}`));
    hero.appendChild(el('span', 'kpi-card__hero-etiqueta', 'Revenue'));
    card.appendChild(hero);

    card.appendChild(el('p', 'kpi-card__unidades', `${nf(m.unidades)} unidades vendidas`));

    const filas = el('div', 'kpi-card__filas');
    const margen = el('div', 'kpi-card__fila');
    margen.appendChild(el('span', 'kpi-card__fila-etiqueta', 'Margen'));
    margen.appendChild(el('span', 'kpi-card__fila-valor mono',
      oGuion(m.margen_pct, (v) => `${nf1(v)} %`)));
    filas.appendChild(margen);
    filas.appendChild(filaDelta('Crecimiento', m.crecimiento_pct, false));
    filas.appendChild(filaDelta('Devoluciones', m.tasa_devolucion_pct, true));
    card.appendChild(filas);

    grilla.appendChild(card);
  }
  bloque.appendChild(grilla);
  destino.appendChild(bloque);
}

function seccionPredicciones(destino, predicciones) {
  if (!predicciones || !predicciones.length) return;

  const bloque = el('div', 'subbloque');
  bloque.appendChild(el('p', 'etiqueta', 'Predicciones, con su error medido'));

  const envoltorio = el('div', 'envoltorio-tabla');
  const tabla = el('table', 'tabla');

  const thead = document.createElement('thead');
  const filaCab = document.createElement('tr');
  for (const [texto, clase] of [['Producto', ''], ['Horizonte', 'num'], ['Valor', 'num'],
                                ['MAPE modelo', 'num'], ['MAPE baseline', 'num'],
                                ['¿Le gana?', '']]) {
    const th = el('th', clase, texto);
    th.scope = 'col';
    filaCab.appendChild(th);
  }
  thead.appendChild(filaCab);
  tabla.appendChild(thead);

  const tbody = document.createElement('tbody');
  for (const p of predicciones) {
    const fila = document.createElement('tr');
    fila.appendChild(el('td', 'mono', p.product_id));
    fila.appendChild(el('td', 'num', `${p.horizonte_dias} d`));
    fila.appendChild(el('td', 'num', nf2(p.valor)));
    fila.appendChild(el('td', 'num', oGuion(p.mape_backtest, (v) => `${nf1(v)} %`)));
    fila.appendChild(el('td', 'num', oGuion(p.mape_baseline, (v) => `${nf1(v)} %`)));

    /* Nunca solo color: va la palabra. Sin baseline no se afirma nada. */
    let veredicto = 'Sin baseline';
    if (p.mape_backtest !== null && p.mape_baseline !== null) {
      veredicto = p.mape_backtest < p.mape_baseline ? 'Sí' : 'No — peor que el baseline';
    }
    fila.appendChild(el('td', null, veredicto));

    tbody.appendChild(fila);
  }
  tabla.appendChild(tbody);
  envoltorio.appendChild(tabla);
  bloque.appendChild(envoltorio);
  destino.appendChild(bloque);
}

/* Rótulo legible por tipo — mismo vocabulario que `FuentesTabla.tsx`. */
const ETIQUETA_TIPO_FUENTE = {
  sql: 'Base de datos',
  documento: 'Documento interno',
  api_publica: 'API pública',
  modelo_ml: 'Modelo predictivo',
};

let contadorFuente = 0;

/* Una fuente, expandible. Colapsada muestra lo que alguien necesita para
 * decidir si confía en la afirmación (tipo, cuál, cuándo); expandida suma el
 * identificador técnico y la sección — detalle real, no un "fragmento
 * relevante" ni un score inventados: `Fuente` no trae esos campos. Mismo
 * componente que `FuentesTabla.tsx`, portado a DOM plano. */
function tarjetaFuente(f) {
  const id = `fuente-detalle-${contadorFuente++}`;
  const li = el('li', 'fuente-card');

  const cabecera = el('button', 'fuente-card__cabecera');
  cabecera.type = 'button';
  cabecera.setAttribute('aria-expanded', 'false');
  cabecera.setAttribute('aria-controls', id);

  cabecera.appendChild(el('span',
    `fuente-card__tipo fuente-card__tipo--${f.tipo}`,
    ETIQUETA_TIPO_FUENTE[f.tipo] || f.tipo));
  cabecera.appendChild(el('span', 'fuente-card__referencia', f.referencia));
  cabecera.appendChild(el('span', 'fuente-card__fecha mono', fecha(f.consultada_en)));

  const flecha = el('span', 'fuente-card__flecha');
  flecha.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2.5"><path d="m6 9 6 6 6-6"/></svg>';
  cabecera.appendChild(flecha);

  li.appendChild(cabecera);

  const detalle = el('div', 'fuente-card__detalle');
  detalle.id = id;
  detalle.hidden = true;
  const campos = el('dl', 'fuente-card__campos');

  const campoId = document.createElement('div');
  campoId.appendChild(el('dt', null, 'Identificador'));
  campoId.appendChild(el('dd', 'mono', f.id));
  campos.appendChild(campoId);

  if (f.seccion) {
    const campoSeccion = document.createElement('div');
    campoSeccion.appendChild(el('dt', null, 'Sección'));
    campoSeccion.appendChild(el('dd', null, f.seccion));
    campos.appendChild(campoSeccion);
  }

  if (f.url) {
    const campoUrl = document.createElement('div');
    campoUrl.appendChild(el('dt', null, 'Enlace'));
    const dd = document.createElement('dd');
    const a = el('a', null, f.url);
    a.href = f.url;
    a.rel = 'noopener noreferrer';
    dd.appendChild(a);
    campoUrl.appendChild(dd);
    campos.appendChild(campoUrl);
  }

  detalle.appendChild(campos);
  li.appendChild(detalle);

  cabecera.addEventListener('click', () => {
    const abierta = cabecera.getAttribute('aria-expanded') === 'true';
    cabecera.setAttribute('aria-expanded', String(!abierta));
    flecha.classList.toggle('fuente-card__flecha--abierta', !abierta);
    detalle.hidden = abierta;
  });

  return li;
}

function seccionFuentes(destino, fuentes) {
  if (!fuentes || !fuentes.length) return;

  const bloque = el('div', 'subbloque');
  bloque.appendChild(el('p', 'etiqueta', 'Fuentes declaradas'));

  const lista = el('ul', 'fuentes-lista');
  for (const f of fuentes) lista.appendChild(tarjetaFuente(f));
  bloque.appendChild(lista);
  destino.appendChild(bloque);
}

function seccionAlertas(destino, advertencias) {
  if (!advertencias || !advertencias.length) return;

  const bloque = el('div', 'subbloque');
  bloque.appendChild(el('p', 'etiqueta', 'Advertencias que el informe lleva consigo'));

  const lista = el('ul', 'alertas');
  for (const texto of advertencias) {
    /* Una predicción peor que su baseline es más grave que una nota de alcance:
     * el ícono y la palabra lo dicen, el borde solo acompaña. */
    const grave = /peor que el baseline/i.test(texto);
    const li = el('li', `alerta ${grave ? 'alerta--grave' : 'alerta--aviso'}`);
    li.appendChild(el('span', 'alerta__icono', grave ? '!!' : '!'));
    li.appendChild(el('span', null, texto));
    lista.appendChild(li);
  }

  bloque.appendChild(lista);
  destino.appendChild(bloque);
}

function seccionLimitaciones(destino, limitaciones) {
  if (!limitaciones || !limitaciones.length) return;

  const bloque = el('div', 'subbloque');
  bloque.appendChild(el('p', 'etiqueta', 'Limitaciones declaradas'));
  const lista = el('ul', 'limitaciones');
  for (const texto of limitaciones) lista.appendChild(el('li', null, texto));
  bloque.appendChild(lista);
  destino.appendChild(bloque);
}

/* --- rechazo --------------------------------------------------------------- */

/* Que el agente no produzca informe no es una falla que haya que disimular.
 * Poder decir "esto no me corresponde" es la capacidad más difícil de construir
 * y la que casi ningún demo muestra. */
function construirRechazo(destino, captura) {
  const caja = el('div', 'rechazo');

  if (captura.error) {
    caja.appendChild(el('h4', 'rechazo__titulo', 'La ejecución terminó con error'));
    caja.appendChild(el('p', 'rechazo__texto mono', captura.error));
  } else {
    caja.appendChild(el('h4', 'rechazo__titulo',
      'El agente decidió que la consulta está fuera de su alcance'));
    caja.appendChild(el('p', 'rechazo__texto',
      'Cortó en el router, antes de planificar o tocar la base. Un agente que ' +
      'siempre intenta responder siempre responde algo, aunque no tenga con qué.'));
  }

  destino.appendChild(caja);

  if (captura.advertencias && captura.advertencias.length) {
    seccionAlertas(destino, captura.advertencias);
  }
}

document.addEventListener('DOMContentLoaded', iniciar);
