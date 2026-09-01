import { Afirmaciones } from "./Afirmaciones";
import { Alertas } from "./Alertas";
import { DescargarInformeEnlace } from "./DescargarInformeEnlace";
import { FuentesTabla } from "./FuentesTabla";
import { Limitaciones } from "./Limitaciones";
import { MetricasTabla } from "./MetricasTabla";
import { PrediccionesTabla } from "./PrediccionesTabla";
import { Rechazo } from "./Rechazo";
import { Traza } from "./Traza";
import type { Analisis } from "../api/types";

interface Props {
  analisis: Analisis;
}

/** Decide qué rama terminal mostrar y arma las fichas de interpretación —
 * equivalente en vivo de `construirDetalle`/`construirInterpretacion` en
 * `docs/replay/replay.js`. */
export function AnalisisDetalle({ analisis }: Props) {
  const { informe } = analisis;

  const campos: [string, string][] = [
    ["Intención", analisis.intencion ?? "—"],
    ["Productos", analisis.product_ids.length ? analisis.product_ids.join(", ") : "—"],
    ["Período", analisis.desde && analisis.hasta ? `${analisis.desde} → ${analisis.hasta}` : "—"],
  ];

  return (
    <article className="detalle">
      <header className="ejecucion__encabezado">
        {/* h2, no <p>: es el título de ESTE análisis dentro de la jerarquía
            de la página (h1 "Dashboard" → h2 acá / "Historial de análisis"
            en el rail → h3 en cada bloque de abajo). Antes era un párrafo
            sin jerarquía y los bloques saltaban directo a h3 sin nada
            arriba — WCAG 2.4.6 roto. */}
        <h2 className="consulta">{analisis.consulta}</h2>
        <div className="fichas">
          {campos.map(([clave, valor]) => (
            <span key={clave} className="ficha">
              <span className="ficha__clave">{clave}</span>
              <span className="ficha__valor">{valor}</span>
            </span>
          ))}
        </div>
      </header>

      {analisis.etapas.length > 0 && informe && (
        <section className="bloque">
          <h3 className="bloque__titulo">
            <span className="bloque__orden">1</span> Traza de ejecución
          </h3>
          <Traza trace={informe.trace} />
        </section>
      )}

      {informe ? (
        <section className="bloque">
          <h3 className="bloque__titulo">
            <span className="bloque__orden">2</span> Informe
          </h3>
          <p className="nota">
            Generado por <span className="mono">{informe.modelo_llm}</span>
          </p>

          {(() => {
            const porFuente = new Map(informe.fuentes.map((f) => [f.id, f]));
            return (
              <>
                <Afirmaciones titulo="Resumen ejecutivo" afirmaciones={informe.resumen_ejecutivo} porFuente={porFuente} />
                <MetricasTabla metricas={informe.metricas} />
                <PrediccionesTabla predicciones={informe.predicciones} />
                <Afirmaciones titulo="Contexto de mercado" afirmaciones={informe.contexto_mercado} porFuente={porFuente} />
                <Afirmaciones titulo="Recomendaciones" afirmaciones={informe.recomendaciones} porFuente={porFuente} />
                <FuentesTabla fuentes={informe.fuentes} />
                <Alertas titulo="Advertencias que el informe lleva consigo" advertencias={informe.advertencias} />
                <Limitaciones limitaciones={informe.limitaciones} />
              </>
            );
          })()}

          <DescargarInformeEnlace id={analisis.id} />
        </section>
      ) : (
        <section className="bloque bloque--rechazo">
          <h3 className="bloque__titulo">
            <span className="bloque__orden">2</span> Resultado
          </h3>
          <Rechazo error={analisis.error} advertencias={analisis.advertencias} />
        </section>
      )}
    </article>
  );
}
