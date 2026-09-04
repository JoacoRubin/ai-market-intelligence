import { useMemo, useState, type CSSProperties } from "react";
import { colorPorEmpresa } from "../lib/empresas";
import type { Producto } from "../api/types";

interface Props {
  productos: Producto[];
  seleccionados: string[];
  onAlternar: (id: string) => void;
  max: number;
}

function estiloEmpresa(slot: number | undefined): CSSProperties {
  // Cast necesario: las custom properties no están en el tipo CSSProperties
  // de React — es el mecanismo estándar para pasarle un token dinámico a
  // una regla CSS que lo lee con var(--color-empresa).
  return { "--color-empresa": `var(--empresa-${slot ?? 1})` } as CSSProperties;
}

/**
 * El selector de productos de la forma "Estructurada" — antes una lista
 * plana de checkboxes (`P021 deportes`) donde armar una comparación de
 * cabeza significaba recordar qué empresa era cada ID. Esto construye la
 * comparación EN PANTALLA a medida que se selecciona: qué empresa, qué
 * categoría, cuántas de cada una — nada que el usuario tenga que retener.
 *
 * Filtros, "Tu comparación" y el grid de productos son estado puramente de
 * presentación (no tocan `seleccionados` salvo alternar/quitar, que ya
 * existían en `SolicitudForm.tsx`) — el payload que sale de acá sigue
 * siendo la misma lista de IDs de siempre.
 *
 * `Producto.brand` — el campo de la API sigue llamándose así
 * (`apps/api/schemas.py`, `seeds/generate.py::MARCAS`); quien mire la
 * respuesta cruda de `/products` lo va a ver así. Acá se lo llama "empresa"
 * en todos lados porque es como el negocio lo piensa: son las compañías que
 * venden en la plataforma, no sub-marcas de un solo dueño. Decisión de
 * producto, no un error de nomenclatura del backend.
 */
export function SelectorProductos({ productos, seleccionados, onAlternar, max }: Props) {
  const [filtroEmpresa, setFiltroEmpresa] = useState("");
  const [filtroCategoria, setFiltroCategoria] = useState("");
  const [busqueda, setBusqueda] = useState("");
  const [soloSeleccionados, setSoloSeleccionados] = useState(false);

  const empresas = useMemo(
    () => [...new Set(productos.map((p) => p.brand))].sort((a, b) => a.localeCompare(b, "es")),
    [productos],
  );
  const categorias = useMemo(
    () => [...new Set(productos.map((p) => p.category))].sort((a, b) => a.localeCompare(b, "es")),
    [productos],
  );
  const colorEmpresa = useMemo(() => colorPorEmpresa(empresas), [empresas]);
  const porId = useMemo(() => new Map(productos.map((p) => [p.id, p])), [productos]);

  const seleccionadosInfo = seleccionados
    .map((id) => porId.get(id))
    .filter((p): p is Producto => p !== undefined);
  const empresasSeleccionadas = new Set(seleccionadosInfo.map((p) => p.brand));
  const categoriasSeleccionadas = new Set(seleccionadosInfo.map((p) => p.category));
  const enElLimite = seleccionados.length >= max;

  const productosFiltrados = productos.filter((p) => {
    if (filtroEmpresa && p.brand !== filtroEmpresa) return false;
    if (filtroCategoria && p.category !== filtroCategoria) return false;
    if (busqueda && !p.id.toLowerCase().includes(busqueda.trim().toLowerCase())) return false;
    if (soloSeleccionados && !seleccionados.includes(p.id)) return false;
    return true;
  });

  // Sin useMemo: el catálogo entero son ~40 productos, agruparlos de nuevo
  // en cada render es más barato que la memoización misma — y evitarla acá
  // evita el error real de la primera versión de este archivo, donde las
  // dependencias del useMemo eran arrays que cambian de referencia en cada
  // render (nunca memoizaba nada, solo aparentaba hacerlo).
  const gruposFiltrados = (() => {
    const mapa = new Map<string, Producto[]>();
    for (const p of productosFiltrados) {
      const lista = mapa.get(p.brand);
      if (lista) lista.push(p);
      else mapa.set(p.brand, [p]);
    }
    return [...mapa.entries()].sort(([a], [b]) => a.localeCompare(b, "es"));
  })();

  return (
    <div className="selector-productos">
      {/* --- Tu comparación --- */}
      <div className="comparacion" aria-live="polite">
        <div className="comparacion__cabecera">
          <p className="etiqueta">Tu comparación</p>
          <span
            key={seleccionados.length}
            className={`comparacion__contador ${enElLimite ? "comparacion__contador--limite" : ""}`}
          >
            {seleccionados.length} / {max}
          </span>
        </div>

        {seleccionadosInfo.length === 0 ? (
          <p className="nota">Elegí productos de la lista para armar la comparación.</p>
        ) : (
          <>
            <div className="comparacion__chips">
              {seleccionadosInfo.map((p, i) => (
                <div className="comparacion__item" key={p.id}>
                  <span className="comparacion__chip" style={estiloEmpresa(colorEmpresa.get(p.brand))}>
                    <span className="comparacion__chip-empresa">{p.brand}</span>
                    <span className="comparacion__chip-detalle">
                      <span className="mono">{p.id}</span> · {p.category}
                    </span>
                    <button
                      type="button"
                      className="comparacion__quitar"
                      onClick={() => onAlternar(p.id)}
                      aria-label={`Quitar ${p.id} de la comparación`}
                    >
                      ×
                    </button>
                  </span>
                  {i < seleccionadosInfo.length - 1 && (
                    <span className="comparacion__vs" aria-hidden="true">
                      VS
                    </span>
                  )}
                </div>
              ))}
            </div>

            <p className="comparacion__resumen">
              {seleccionadosInfo.length} producto{seleccionadosInfo.length !== 1 && "s"} seleccionado
              {seleccionadosInfo.length !== 1 && "s"} · {empresasSeleccionadas.size} empresa
              {empresasSeleccionadas.size !== 1 && "s"}
              {seleccionadosInfo.length >= 2 &&
                (categoriasSeleccionadas.size === 1
                  ? ` · misma categoría (${[...categoriasSeleccionadas][0]})`
                  : ` · categorías diferentes`)}
            </p>
          </>
        )}

        {enElLimite && (
          <p className="nota comparacion__limite-aviso">
            Alcanzaste el máximo de {max} productos para una comparación.
          </p>
        )}
      </div>

      {/* --- filtros --- */}
      <div className="filtros-productos">
        <select
          className="filtros-productos__select"
          value={filtroEmpresa}
          onChange={(e) => setFiltroEmpresa(e.target.value)}
          aria-label="Filtrar por empresa"
        >
          <option value="">Todas las empresas</option>
          {empresas.map((e) => (
            <option key={e} value={e}>
              {e}
            </option>
          ))}
        </select>
        <select
          className="filtros-productos__select"
          value={filtroCategoria}
          onChange={(e) => setFiltroCategoria(e.target.value)}
          aria-label="Filtrar por categoría"
        >
          <option value="">Todas las categorías</option>
          {categorias.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <input
          type="text"
          className="filtros-productos__buscar"
          placeholder="Buscar producto…"
          value={busqueda}
          onChange={(e) => setBusqueda(e.target.value)}
          aria-label="Buscar producto por ID"
        />
        <label className="filtros-productos__toggle">
          <input
            type="checkbox"
            checked={soloSeleccionados}
            onChange={(e) => setSoloSeleccionados(e.target.checked)}
          />
          Mostrar solo seleccionados
        </label>
      </div>

      {/* --- grid de productos --- */}
      <div className="solicitud__productos">
        {productos.length === 0 && <p className="nota">Cargando catálogo…</p>}
        {productos.length > 0 && gruposFiltrados.length === 0 && (
          <p className="nota">Ningún producto coincide con el filtro.</p>
        )}
        {gruposFiltrados.map(([empresa, items]) => (
          <fieldset key={empresa} className="solicitud__empresa">
            <legend className="etiqueta">{empresa}</legend>
            <div className="producto-grid">
              {items.map((p) => {
                const seleccionado = seleccionados.includes(p.id);
                const deshabilitado = !seleccionado && enElLimite;
                return (
                  <label
                    key={p.id}
                    className={`producto-card ${seleccionado ? "producto-card--seleccionado" : ""} ${
                      deshabilitado ? "producto-card--deshabilitado" : ""
                    }`}
                    style={estiloEmpresa(colorEmpresa.get(p.brand))}
                  >
                    <input
                      type="checkbox"
                      checked={seleccionado}
                      disabled={deshabilitado}
                      onChange={() => onAlternar(p.id)}
                    />
                    <span className="producto-card__id mono">{p.id}</span>
                    <span className="producto-card__categoria">{p.category}</span>
                  </label>
                );
              })}
            </div>
          </fieldset>
        ))}
      </div>
    </div>
  );
}
