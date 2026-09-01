import { duracion, nf, nf1 } from "../lib/formato";
import { esEtapaLlm } from "../lib/etapas";
import type { PasoTrace } from "../api/types";

interface Props {
  trace: PasoTrace[];
}

/** La cinta de traza — puerto directo de `construirTraza`/`construirTablaTraza`
 * en `docs/replay/replay.js`. Sin la animación de "reproducir": ahí tiene
 * sentido porque el replay es un artefacto de portfolio pensado para
 * mostrarse; acá el usuario está viendo la corrida real en vivo, no una
 * grabación — la cinta estática con proporciones reales ya comunica todo. */
export function Traza({ trace }: Props) {
  if (!trace.length) {
    return <p className="nota">Esta ejecución no registró etapas.</p>;
  }

  const total = trace.reduce((a, p) => a + p.duracion_ms, 0);
  const conLlm = trace.filter((p) => esEtapaLlm(p.nodo));
  const msLlm = conLlm.reduce((a, p) => a + p.duracion_ms, 0);

  const etiquetaCinta =
    `Traza de ${trace.length} etapas, ${duracion(total)} en total: ` +
    trace.map((p) => `${p.nodo} ${duracion(p.duracion_ms)}`).join(", ");

  return (
    <div>
      <ul className="leyenda">
        <li>
          <span className="leyenda__marca leyenda__marca--det" /> Determinística
        </li>
        <li>
          <span className="leyenda__marca leyenda__marca--llm" /> Modelo (LLM)
        </li>
      </ul>

      <div className="cinta" aria-label={etiquetaCinta}>
        {trace.map((paso, i) => {
          const esLlm = esEtapaLlm(paso.nodo);
          const proporcion = total ? paso.duracion_ms / total : 0;
          const angosto = proporcion < 0.08;
          return (
            <div
              key={i}
              className={`tramo ${esLlm ? "tramo--llm" : ""} ${angosto ? "tramo--angosto" : ""}`}
              style={{ flexGrow: Math.max(proporcion, 0.004), flexBasis: 0 }}
              title={`${paso.nodo} · ${duracion(paso.duracion_ms)}${paso.tool ? ` · ${paso.tool}` : ""}`}
            >
              <div className="tramo__relleno" />
              <span className="tramo__rotulo">{paso.nodo}</span>
            </div>
          );
        })}
      </div>

      <p className="traza__reloj mono">{nf1(total / 1000)}s</p>

      <p className="tesis">
        <strong>
          {conLlm.length} de {trace.length} etapas usan el modelo
        </strong>
        {total ? (
          <>
            {" "}
            — y se llevan el {nf(Math.round((msLlm / total) * 100))} % del tiempo. Las otras son
            software clásico: ningún número del informe sale del modelo.
          </>
        ) : (
          "."
        )}
      </p>

      <div className="envoltorio-tabla">
        <table className="tabla">
          <thead>
            <tr>
              <th scope="col">Etapa</th>
              <th scope="col">Tipo</th>
              <th scope="col" className="num">
                Duración
              </th>
              <th scope="col" className="num">
                % del total
              </th>
            </tr>
          </thead>
          <tbody>
            {trace.map((paso, i) => {
              const esLlm = esEtapaLlm(paso.nodo);
              return (
                <tr key={i}>
                  <td>
                    <span className={`punto ${esLlm ? "punto--llm" : "punto--det"}`} />
                    <span className="mono">{paso.nodo}</span>
                    {paso.tool && <span className="mono"> ({paso.tool})</span>}
                  </td>
                  <td>{esLlm ? "Modelo" : "Determinística"}</td>
                  <td className="num">{duracion(paso.duracion_ms)}</td>
                  <td className="num">{total ? `${nf1((paso.duracion_ms / total) * 100)} %` : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
