import { nf1, nf2, oGuion } from "../lib/formato";
import type { Prediccion } from "../api/types";

interface Props {
  predicciones: Prediccion[];
}

export function PrediccionesTabla({ predicciones }: Props) {
  if (!predicciones.length) return null;

  return (
    <div className="subbloque">
      <p className="etiqueta">Predicciones, con su error medido</p>
      <div className="envoltorio-tabla">
        <table className="tabla">
          <thead>
            <tr>
              <th scope="col">Producto</th>
              <th scope="col" className="num">
                Horizonte
              </th>
              <th scope="col" className="num">
                Valor
              </th>
              <th scope="col" className="num">
                MAPE modelo
              </th>
              <th scope="col" className="num">
                MAPE baseline
              </th>
              <th scope="col">¿Le gana?</th>
            </tr>
          </thead>
          <tbody>
            {predicciones.map((p, i) => {
              // Nunca solo color: va la palabra. Sin baseline no se afirma nada.
              let veredicto = "Sin baseline";
              if (p.mape_backtest !== null && p.mape_baseline !== null) {
                veredicto = p.mape_backtest < p.mape_baseline ? "Sí" : "No — peor que el baseline";
              }
              return (
                <tr key={i}>
                  <td className="mono">{p.product_id}</td>
                  <td className="num">{p.horizonte_dias} d</td>
                  <td className="num">{nf2(p.valor)}</td>
                  <td className="num">{oGuion(p.mape_backtest, (v) => `${nf1(v)} %`)}</td>
                  <td className="num">{oGuion(p.mape_baseline, (v) => `${nf1(v)} %`)}</td>
                  <td>{veredicto}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
