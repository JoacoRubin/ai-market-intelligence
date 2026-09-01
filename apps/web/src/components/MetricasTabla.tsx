import { nf, nf1, nf2, oGuion } from "../lib/formato";
import type { MetricaProducto } from "../api/types";

interface Props {
  metricas: MetricaProducto[];
}

export function MetricasTabla({ metricas }: Props) {
  if (!metricas.length) return null;

  const maxUnidades = Math.max(...metricas.map((m) => m.unidades), 1);

  return (
    <div className="subbloque">
      <p className="etiqueta">KPIs calculados por SQL</p>
      <div className="envoltorio-tabla">
        <table className="tabla">
          <thead>
            <tr>
              <th scope="col">Producto</th>
              <th scope="col" className="num">
                Unidades
              </th>
              <th scope="col"></th>
              <th scope="col" className="num">
                Revenue
              </th>
              <th scope="col" className="num">
                Margen
              </th>
              <th scope="col" className="num">
                Crecimiento
              </th>
              <th scope="col" className="num">
                Devoluciones
              </th>
            </tr>
          </thead>
          <tbody>
            {metricas.map((m) => (
              <tr key={m.product_id}>
                <td>
                  <span className="mono">{m.product_id}</span> {m.nombre}
                </td>
                <td className="num">{nf(m.unidades)}</td>
                <td>
                  {/* Barra de una sola serie: la identidad la lleva el rótulo
                      de la fila, no hace falta leyenda ni un segundo color. */}
                  <div className="barra">
                    <div className="barra__relleno" style={{ width: `${(m.unidades / maxUnidades) * 100}%` }} />
                  </div>
                </td>
                <td className="num">USD {nf2(m.revenue)}</td>
                <td className="num">{oGuion(m.margen_pct, (v) => `${nf1(v)} %`)}</td>
                <td className="num">{oGuion(m.crecimiento_pct, (v) => `${nf1(v)} %`)}</td>
                <td className="num">{oGuion(m.tasa_devolucion_pct, (v) => `${nf1(v)} %`)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
