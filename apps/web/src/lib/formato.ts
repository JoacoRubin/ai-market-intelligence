/**
 * Formateadores portados literal de `docs/replay/replay.js` — mismo criterio,
 * mismos números, para que un valor se lea igual en el replay grabado y en
 * este dashboard en vivo.
 */

export const nf = new Intl.NumberFormat("es-AR").format;
export const nf1 = new Intl.NumberFormat("es-AR", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
}).format;
export const nf2 = new Intl.NumberFormat("es-AR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
}).format;

export function duracion(ms: number): string {
  if (ms < 1000) return `${nf(ms)} ms`;
  return `${nf1(ms / 1000)} s`;
}

/** Un `null`/`undefined` en el modelo significa "no se sabe", no "cero".
 * Devolver 0 acá convertiría un dato ausente en una afirmación falsa. */
export function oGuion<T>(valor: T | null | undefined, formatear: (v: T) => string): string {
  return valor === null || valor === undefined ? "—" : formatear(valor);
}

export function fechaLegible(iso: string): string {
  return new Date(iso).toLocaleDateString("es-AR", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

/** Segundos transcurridos desde un ISO timestamp hasta ahora. Para el reloj
 * en vivo del panel de espera. */
export function segundosDesde(iso: string): number {
  return Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
}

/** Fecha relativa para el historial ("hace 3 min", "ayer") — más legible que
 * un timestamp ISO en una lista que se escanea rápido. Más allá de una
 * semana cae a `fechaLegible`: "hace 19 días" deja de ser más útil que la
 * fecha concreta. */
export function relativoDesde(iso: string): string {
  const segundos = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (segundos < 60) return "recién";
  const minutos = Math.floor(segundos / 60);
  if (minutos < 60) return `hace ${minutos} min`;
  const horas = Math.floor(minutos / 60);
  if (horas < 24) return `hace ${horas} h`;
  const dias = Math.floor(horas / 24);
  if (dias === 1) return "ayer";
  if (dias < 7) return `hace ${dias} días`;
  return fechaLegible(iso);
}
