/* DOM mínimo para ejecutar replay.js fuera de un navegador.
 *
 * Existe por un bug real: `Intl.NumberFormat` devuelve un objeto y yo lo llamé
 * como función. Sintaxis válida, contrato de datos intacto, y la página se
 * quedaba en blanco. `node --check` no lo ve, y un test que compara nombres de
 * campos en Python tampoco: hay que EJECUTAR el JavaScript.
 *
 * No pretende ser un navegador. Alcanza con que cada función de render corra de
 * verdad sobre datos reales, que es donde aparecen los TypeError.
 *
 * Uso:  node harness_dom.js <directorio-del-sitio>
 * Sale con código 1 y un JSON con el error si algo revienta.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SITIO = process.argv[2];
if (!SITIO) { console.error('falta el directorio del sitio'); process.exit(2); }

const leer = (...p) => fs.readFileSync(path.join(SITIO, ...p), 'utf8');

function crearNodo(tag) {
  const n = {
    tagName: tag,
    className: '',
    textContent: '',
    title: '',
    href: '',
    rel: '',
    scope: '',
    type: '',
    disabled: false,
    hidden: false,
    style: {},
    dataset: {},
    attrs: {},
    hijos: [],
    classList: { add() {}, remove() {} },
    appendChild(h) { this.hijos.push(h); return h; },
    // Agregado junto con la pipeline de nodos del rediseño (2026-09-03): el
    // molde HTML necesitaba insertar un elemento ANTES de otro, no solo al
    // final — el primer código real que lo necesita en este proyecto.
    insertBefore(nuevo, referencia) {
      const i = this.hijos.indexOf(referencia);
      if (i === -1) this.hijos.push(nuevo);
      else this.hijos.splice(i, 0, nuevo);
      return nuevo;
    },
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k]; },
    addEventListener(evento, fn) { (this.oyentes[evento] ||= []).push(fn); },
    oyentes: {},
    querySelector() { return crearNodo('div'); },
    querySelectorAll() { return []; },
  };
  // El molde del detalle se clona; devolver un nodo nuevo alcanza porque las
  // funciones de render lo recorren con querySelector.
  n.content = { cloneNode: () => crearNodo('fragmento') };
  return n;
}

const porId = {};
let alCargar = null;

const caja = {
  console,
  Intl,
  performance: { now: () => 0 },
  requestAnimationFrame: () => 0,
  cancelAnimationFrame: () => {},
  location: { protocol: 'http:' },
  window: { matchMedia: () => ({ matches: false }) },
  document: {
    getElementById(id) { return (porId[id] ||= crearNodo('div')); },
    createElement: crearNodo,
    createTextNode: (t) => ({ textContent: t, hijos: [] }),
    querySelectorAll() { return []; },
    addEventListener(evento, fn) { if (evento === 'DOMContentLoaded') alCargar = fn; },
  },
  /* Sirve los archivos del disco, así el JS ve exactamente los mismos bytes que
   * vería por HTTP. Un stub con datos inventados probaría el stub. */
  async fetch(url) {
    const relativa = url.replace(/^\/+/, '');
    try {
      return { ok: true, json: async () => JSON.parse(leer(...relativa.split('/'))) };
    } catch {
      return { ok: false, status: 404, json: async () => ({}) };
    }
  },
};

vm.createContext(caja);
vm.runInContext(leer('replay.js'), caja);

(async () => {
  const resultado = { casos_en_indice: 0, casos_renderizados: [], error: null };
  try {
    await alCargar();

    const lista = porId['indice-lista'];
    resultado.casos_en_indice = lista.hijos.length;

    // Cada botón del índice dispara su propio render: es la única forma de
    // ejercitar construirDetalle sobre TODOS los casos, incluido el que no tiene
    // informe (el camino que casi ningún demo recorre).
    const manifiesto = JSON.parse(leer('data', 'manifiesto.json'));
    for (const caso of manifiesto.casos) {
      const boton = lista.hijos
        .flatMap((li) => li.hijos)
        .find((b) => b.dataset && b.dataset.id === caso.id);
      if (!boton) throw new Error(`no se construyó el botón de ${caso.id}`);
      for (const fn of boton.oyentes.click || []) await fn();
      resultado.casos_renderizados.push(caso.id);
    }
  } catch (e) {
    resultado.error = `${e.constructor.name}: ${e.message}`;
    resultado.pila = (e.stack || '').split('\n').slice(0, 5).join(' | ');
  }

  console.log(JSON.stringify(resultado));
  process.exit(resultado.error ? 1 : 0);
})();
