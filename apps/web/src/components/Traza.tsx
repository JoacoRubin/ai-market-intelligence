import { duracion, nf, nf1 } from "../lib/formato";
import { origenEtapa, type OrigenEtapa } from "../lib/etapas";
import { useReproducirTraza } from "../hooks/useReproducirTraza";
import type { PasoTrace } from "../api/types";

interface Props {
  trace: PasoTrace[];
}

/** Sufijo de clase y texto por origen — un solo lugar para no repetir el
 * mapeo en la cinta, la leyenda y la tabla. "externo" sigue siendo software
 * determinístico (SEC EDGAR, ADR-014): el matiz de color separa la FUENTE,
 * no reabre si el LLM inventó el número — esa pregunta la sigue
 * respondiendo "Modelo" vs "Determinística", no el tono de aqua vs azul. */
const SUFIJO: Record<OrigenEtapa, string> = { llm: "llm", interno: "det", externo: "ext" };
const ETIQUETA_TIPO: Record<OrigenEtapa, string> = {
  llm: "Modelo",
  interno: "Determinística",
  externo: "Determinística (SEC EDGAR)",
};

/** La cinta de traza — puerto de `construirTraza`/`construirTablaTraza` en
 * `docs/replay/replay.js`, esta vez CON la animación de "reproducir": es el
 * elemento de firma de esta iteración — duraciones reales aceleradas, factor
 * declarado, opt-in por botón (nunca autoplay: abrir varios análisis del
 * historial seguidos con autoplay sería ruido). Ver
 * `hooks/useReproducirTraza.ts` para por qué corre por refs y no por
 * `setState` a 60fps. */
export function Traza({ trace }: Props) {
  const total = trace.reduce((a, p) => a + p.duracion_ms, 0);
  const { animando, etiquetaBoton, reproducir, rellenosRef, relojRef } = useReproducirTraza(
    trace,
    total,
  );

  if (!trace.length) {
    return <p className="nota">Esta ejecución no registró etapas.</p>;
  }

  const conLlm = trace.filter((p) => origenEtapa(p.nodo) === "llm");
  const msLlm = conLlm.reduce((a, p) => a + p.duracion_ms, 0);
  const factorVelocidad = Math.max(1, Math.round(total / 3200));

  const etiquetaCinta =
    `Traza de ${trace.length} etapas, ${duracion(total)} en total: ` +
    trace.map((p) => `${p.nodo} ${duracion(p.duracion_ms)}`).join(", ");

  return (
    <div>
      <div className="traza__controles">
        <button type="button" className="boton" onClick={reproducir}>
          {etiquetaBoton}
        </button>
        <p className="traza__reloj mono">
          <span ref={relojRef}>{nf1(total / 1000)}</span> s
        </p>
        <p className="traza__aviso">
          Reproducción acelerada ×{nf(factorVelocidad)}. Los tiempos mostrados son los reales.
        </p>
      </div>

      {/* Pipeline de nodos: la vista "de un vistazo" que pide un sistema
          agentic — cada nodo es el que REALMENTE corrió (sale de `trace`,
          no de una lista fija de "Router/Planner/.../Report" inventada:
          esta corrida puede no haber tocado RAG, o haber ido a SEC EDGAR en
          vez de SQL). `aria-hidden`: es un resumen visual de lo mismo que
          la cinta de abajo ya anuncia por `aria-label`, listarlo dos veces
          sería ruido para un lector de pantalla. */}
      <div className="pipeline" aria-hidden="true">
        {trace.map((paso, i) => {
          const origen = origenEtapa(paso.nodo);
          return (
            <div className="pipeline__paso" key={i}>
              <div className="pipeline__nodo">
                <span className={`pipeline__punto pipeline__punto--${SUFIJO[origen]}`}>
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                    <path d="m5 13 4 4L19 7" />
                  </svg>
                </span>
                <span className="pipeline__nombre mono">{paso.nodo}</span>
                <span className="pipeline__duracion mono">{duracion(paso.duracion_ms)}</span>
              </div>
              {i < trace.length - 1 && <span className="pipeline__conector" />}
            </div>
          );
        })}
      </div>

      {/* role="img": la cinta es un solo objeto visual para quien usa lector
          de pantalla, no una lista de divs decorativos para recorrer uno por
          uno — el resumen completo va en aria-label, y la tabla de abajo es
          la fuente de verdad accesible, no un duplicado decorativo. */}
      <div className="cinta" role="img" aria-label={etiquetaCinta} data-animando={animando}>
        {trace.map((paso, i) => {
          const origen = origenEtapa(paso.nodo);
          const proporcion = total ? paso.duracion_ms / total : 0;
          const angosto = proporcion < 0.08;
          const detalleTramo = `${paso.nodo} · ${duracion(paso.duracion_ms)}${paso.tool ? ` · ${paso.tool}` : ""}`;
          // "interno" no lleva modificador: la regla base .tramo YA es la
          // apariencia determinística (--det-tenue), mismo criterio que el
          // código anterior con "" para el caso default.
          const claseOrigen = origen === "interno" ? "" : `tramo--${SUFIJO[origen]}`;
          return (
            <div
              key={i}
              className={`tramo ${claseOrigen} ${angosto ? "tramo--angosto" : ""}`}
              style={{ flexGrow: Math.max(proporcion, 0.004), flexBasis: 0 }}
              title={detalleTramo}
              aria-label={detalleTramo}
            >
              <div
                className="tramo__relleno"
                ref={(el) => {
                  rellenosRef.current[i] = el;
                }}
              />
              <span className="tramo__rotulo">{paso.nodo}</span>
            </div>
          );
        })}
      </div>

      <ul className="leyenda">
        <li>
          <span className="leyenda__marca leyenda__marca--det" /> Determinística
        </li>
        <li>
          <span className="leyenda__marca leyenda__marca--ext" /> Determinística (SEC EDGAR)
        </li>
        <li>
          <span className="leyenda__marca leyenda__marca--llm" /> Modelo (LLM)
        </li>
      </ul>

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
          <caption>Duración exacta de cada etapa</caption>
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
              const origen = origenEtapa(paso.nodo);
              return (
                <tr key={i}>
                  <td>
                    <span className={`punto punto--${SUFIJO[origen]}`} />
                    <span className="mono">{paso.nodo}</span>
                    {paso.tool && <span className="mono"> ({paso.tool})</span>}
                  </td>
                  <td>{ETIQUETA_TIPO[origen]}</td>
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
