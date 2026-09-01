import { relativoDesde } from "../lib/formato";
import type { AnalisisResumen, EstadoAnalisis } from "../api/types";

interface Props {
  items: AnalisisResumen[];
  activoId: string | null;
  onSeleccionar: (id: string | null) => void;
  cargando: boolean;
  error: string | null;
}

/** Rótulo + clase de color por estado. `completado` cubre tanto un informe
 * real como un rechazo del router (`AnalisisResumen` no trae `informe`, así
 * que la lista no puede ni debe distinguirlos — esa distinción solo importa
 * al abrir el detalle. "Completado" es honesto acá: el grafo terminó sin
 * excepción, que es lo único que este nivel promete). `cancelado` nunca
 * llega: `apps/api/store.py::listar()` ya lo excluye. */
const ESTADO: Record<EstadoAnalisis, { etiqueta: string; clase: string }> = {
  pendiente: { etiqueta: "En cola", clase: "caso__estado--en-curso" },
  procesando: { etiqueta: "Procesando", clase: "caso__estado--en-curso" },
  completado: { etiqueta: "Completado", clase: "caso__estado--completado" },
  fallido: { etiqueta: "Error", clase: "caso__estado--fallido" },
  cancelado: { etiqueta: "Cancelado", clase: "caso__estado--en-curso" },
};

const LARGO_MAX_CONSULTA = 70;

/** Rail del historial — activa `.indice`/`.caso`, copiados de
 * `docs/replay/estilos.css` desde v1 y sin usar hasta ahora. Cada ítem es un
 * `<button>` real (no `<div onClick>`): foco y activación por teclado vienen
 * gratis de usar el elemento correcto, no hace falta reimplementarlos. */
export function HistorialAnalisis({ items, activoId, onSeleccionar, cargando, error }: Props) {
  return (
    <nav className="indice" aria-label="Historial de análisis">
      <h2 className="indice__titulo">Historial</h2>

      <button
        type="button"
        className="caso"
        aria-current={activoId === null}
        onClick={() => onSeleccionar(null)}
      >
        <span className="caso__consulta">+ Nuevo análisis</span>
      </button>

      {cargando && items.length === 0 && <p className="nota">Cargando…</p>}
      {error && <p className="nota">{error}</p>}

      <ul className="indice__lista">
        {items.map((item) => {
          const info = ESTADO[item.estado];
          const consulta =
            item.consulta.length > LARGO_MAX_CONSULTA
              ? `${item.consulta.slice(0, LARGO_MAX_CONSULTA)}…`
              : item.consulta;
          return (
            <li key={item.id}>
              <button
                type="button"
                className="caso"
                aria-current={item.id === activoId}
                onClick={() => onSeleccionar(item.id)}
              >
                <span className="caso__consulta">{consulta}</span>
                <span className="caso__pie">
                  <span>{relativoDesde(item.creado_en)}</span>
                  <span className={`caso__estado ${info.clase}`}>{info.etiqueta}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
