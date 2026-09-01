import type { Analisis, ListaProductos, SolicitudAnalisis } from "./types";

/** Cuánto se espera cada request antes de darla por caída. Un GET de estado o
 * el POST inicial (que responde en milisegundos: el análisis se despacha en
 * background, no corre dentro del request — ver ADR-013) sobran de sobra con
 * 10s; si tarda más que eso, algo está roto, no ocupado. */
const TIMEOUT_MS = 10_000;

export class ErrorApi extends Error {
  status: number;
  detalles: string[];

  constructor(status: number, detalles: string[]) {
    super(detalles.join(" — ") || `HTTP ${status}`);
    this.status = status;
    this.detalles = detalles;
  }
}

/**
 * FastAPI devuelve `detail` como string (una excepción manual, ej.
 * "producto no encontrado") o como array de objetos `{loc, msg, type}`
 * (un ValidationError de Pydantic, ej. la regla XOR de `SolicitudAnalisis`).
 * Se normalizan las dos formas a una lista de strings legibles.
 */
async function extraerDetalles(r: Response): Promise<string[]> {
  try {
    const cuerpo = await r.json();
    const detail = cuerpo?.detail;
    if (typeof detail === "string") return [detail];
    if (Array.isArray(detail)) {
      return detail.map((d) => (typeof d?.msg === "string" ? d.msg : JSON.stringify(d)));
    }
    return [`HTTP ${r.status}`];
  } catch {
    return [`HTTP ${r.status}`];
  }
}

async function pedir<T>(input: string, init?: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch(input, { ...init, signal: AbortSignal.timeout(TIMEOUT_MS) });
  } catch (e) {
    const motivo = e instanceof Error && e.name === "TimeoutError" ? "sin respuesta" : "sin conexión";
    throw new ErrorApi(0, [`${motivo} con la API (${input})`]);
  }
  if (!r.ok) throw new ErrorApi(r.status, await extraerDetalles(r));
  return r.json() as Promise<T>;
}

export function crearAnalisis(solicitud: SolicitudAnalisis): Promise<Analisis> {
  return pedir<Analisis>("/analyses", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(solicitud),
  });
}

export function obtenerAnalisis(id: string): Promise<Analisis> {
  return pedir<Analisis>(`/analyses/${encodeURIComponent(id)}`, {
    headers: { Accept: "application/json" },
  });
}

export function listarProductos(): Promise<ListaProductos> {
  // limite=200: cubre el catálogo completo (40 productos sembrados hoy) sin
  // paginar — el tope real lo impone la API (`main.py`, máx. 200).
  return pedir<ListaProductos>("/products?limite=200");
}
