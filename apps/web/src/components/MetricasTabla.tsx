import { nf, nf1, nf2, oGuion } from "../lib/formato";
import type { MetricaProducto } from "../api/types";

interface Props {
  metricas: MetricaProducto[];
}

/** Signo de una variación: `positivo` no significa "número positivo", significa
 * "la flecha va en el sentido bueno" — por eso `invertido` existe. Una tasa de
 * devolución que BAJA es una mejora aunque el número tenga signo negativo. */
function signo(valor: number | null, invertido = false): "positivo" | "negativo" | "neutro" {
  if (valor === null || valor === 0) return "neutro";
  const sube = valor > 0;
  return sube !== invertido ? "positivo" : "negativo";
}

interface FilaDeltaProps {
  etiqueta: string;
  valor: number | null;
  invertido?: boolean;
}

/** Fila "nombre + valor con flecha ↑/↓ coloreada" — el pedido explícito del
 * brief ("cada KPI debe mostrar variación con indicador ↑ ↓"). `crecimiento_pct`
 * y `tasa_devolucion_pct` YA SON la comparación contra el período anterior
 * (`application/analisis.py`/las tools de SQL las calculan así) — no hace
 * falta pedir un segundo número al backend para "vs período anterior". */
function FilaDelta({ etiqueta, valor, invertido = false }: FilaDeltaProps) {
  const s = signo(valor, invertido);
  return (
    <div className="kpi-card__fila">
      <span className="kpi-card__fila-etiqueta">{etiqueta}</span>
      <span className={`kpi-card__fila-valor delta delta--${s}`}>
        {valor === null ? (
          "—"
        ) : (
          <>
            {valor > 0 ? "↑" : valor < 0 ? "↓" : "→"} {nf1(Math.abs(valor))} %
          </>
        )}
      </span>
    </div>
  );
}

export function MetricasTabla({ metricas }: Props) {
  if (!metricas.length) return null;

  return (
    <div className="subbloque">
      <p className="etiqueta">KPIs calculados por SQL</p>
      <div className="kpi-grid">
        {metricas.map((m) => (
          <article key={m.product_id} className="kpi-card" aria-label={`KPIs de ${m.nombre}`}>
            <header className="kpi-card__encabezado">
              <span className="mono kpi-card__id">{m.product_id}</span>
              <span className="kpi-card__nombre">{m.nombre}</span>
            </header>

            {/* El "hero" de la card: revenue, el número que más importa,
                grande — el resto son filas secundarias más chicas. */}
            <p className="kpi-card__hero">
              <span className="kpi-card__hero-cifra">USD {nf2(m.revenue)}</span>
              <span className="kpi-card__hero-etiqueta">Revenue</span>
            </p>

            <p className="kpi-card__unidades">{nf(m.unidades)} unidades vendidas</p>

            <div className="kpi-card__filas">
              <div className="kpi-card__fila">
                <span className="kpi-card__fila-etiqueta">Margen</span>
                <span className="kpi-card__fila-valor mono">
                  {oGuion(m.margen_pct, (v) => `${nf1(v)} %`)}
                </span>
              </div>
              <FilaDelta etiqueta="Crecimiento" valor={m.crecimiento_pct} />
              <FilaDelta etiqueta="Devoluciones" valor={m.tasa_devolucion_pct} invertido />
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
