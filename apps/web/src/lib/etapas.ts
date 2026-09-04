/**
 * Qué etapas del grafo invocan al modelo. Sale de `agent/graph.py`: de las
 * seis etapas, el router clasifica la intención y el synthesizer redacta —
 * las otras cuatro son software clásico. Si el grafo cambia, esta lista
 * cambia con él (mismo comentario que ya tiene `docs/replay/replay.js`).
 */
export const ETAPAS_LLM = new Set(["router", "synthesizer"]);

/**
 * Qué etapas pegan contra una fuente PÚBLICA en vez de los datos propios.
 * Hoy es una sola: `edgar_tool` (SEC EDGAR, ADR-014) — sql_tool y rag_tool
 * consultan la base y el índice propios, así que siguen del lado "interno".
 * El replay estático (`docs/replay/replay.js`) todavía no hace esta
 * distinción de tres vías; es una divergencia deliberada del dashboard, ver
 * el comentario sobre `--ext` en app.css.
 */
export const ETAPAS_EXTERNAS = new Set(["edgar_tool"]);

export type OrigenEtapa = "llm" | "interno" | "externo";

/**
 * De dónde sale lo que hizo esta etapa — la clasificación de tres vías que
 * usa la cinta de traza para pintar identidad, no solo "modelo sí/no".
 */
export function origenEtapa(nodo: string): OrigenEtapa {
  if (ETAPAS_LLM.has(nodo)) return "llm";
  if (ETAPAS_EXTERNAS.has(nodo)) return "externo";
  return "interno";
}

export function esEtapaLlm(nodo: string): boolean {
  return origenEtapa(nodo) === "llm";
}
