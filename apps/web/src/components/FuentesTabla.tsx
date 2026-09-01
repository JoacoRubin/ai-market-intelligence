import { fechaLegible } from "../lib/formato";
import type { Fuente } from "../api/types";

interface Props {
  fuentes: Fuente[];
}

export function FuentesTabla({ fuentes }: Props) {
  if (!fuentes.length) return null;

  return (
    <div className="subbloque">
      <p className="etiqueta">Fuentes declaradas</p>
      <div className="envoltorio-tabla">
        <table className="tabla">
          <thead>
            <tr>
              <th scope="col">Identificador</th>
              <th scope="col">Tipo</th>
              <th scope="col">Referencia</th>
              <th scope="col">Consultada</th>
            </tr>
          </thead>
          <tbody>
            {fuentes.map((f) => (
              <tr key={f.id}>
                <td className="mono">{f.id}</td>
                <td>{f.tipo}</td>
                <td>
                  {f.url ? (
                    <a href={f.url} rel="noopener noreferrer" target="_blank">
                      {f.referencia}
                    </a>
                  ) : (
                    <span className="mono">{f.referencia}</span>
                  )}
                </td>
                <td>{fechaLegible(f.consultada_en)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
