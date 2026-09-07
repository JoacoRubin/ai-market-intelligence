/**
 * Color de identidad por empresa — los 8 slots completos de la paleta
 * categórica que ya usan --det/--llm/--ext (slots 1-3), extendida a los 8
 * reales que tiene el catálogo (`SolicitudForm.tsx` ya lo documentaba: "8
 * empresas reales para 40 productos"). Asignación DETERMINÍSTICA: orden
 * alfabético de las empresas que trae `/products`, nunca a mano — si el
 * catálogo cambia, el mapeo se recalcula solo.
 *
 * Validado con scripts/validate_palette.js de la skill dataviz contra los
 * fondos nuevos (#111827 card, oscuro): los 8 pasan el chequeo ADYACENTE
 * completo (peor par ΔE 8.4 daltonismo / 19.3 visión normal, sobre objetivo
 * 8/15). Bajo `--pairs all` (cualquier par puede quedar lado a lado, que es
 * justo el caso de "Tu comparación") el piso NO se sostiene más allá de 3
 * slots — documentado en palette.md, no es un bug de esta app. Por eso acá
 * el nombre de la empresa es SIEMPRE texto visible en cada card/chip, nunca
 * el color solo: es la excepción legal del validador ("secondary encoding"),
 * no una vista gorda de la regla.
 */

const SLOTS = 8;

export function colorPorEmpresa(empresas: string[]): Map<string, number> {
  const ordenadas = [...empresas].sort((a, b) => a.localeCompare(b, "es"));
  const mapa = new Map<string, number>();
  ordenadas.forEach((empresa, i) => mapa.set(empresa, (i % SLOTS) + 1));
  return mapa;
}
