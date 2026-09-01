/**
 * Qué etapas del grafo invocan al modelo. Sale de `agent/graph.py`: de las
 * seis etapas, el router clasifica la intención y el synthesizer redacta —
 * las otras cuatro son software clásico. Si el grafo cambia, esta lista
 * cambia con él (mismo comentario que ya tiene `docs/replay/replay.js`).
 */
export const ETAPAS_LLM = new Set(["router", "synthesizer"]);

export function esEtapaLlm(nodo: string): boolean {
  return ETAPAS_LLM.has(nodo);
}
