import { useState } from "react";
import { fechaLegible } from "../lib/formato";
import type { Fuente, TipoFuente } from "../api/types";

interface Props {
  fuentes: Fuente[];
}

/** Rótulo legible por tipo — `Fuente.tipo` es el vocabulario del backend
 * (`core/report.py`), no necesariamente el que quiere leer un humano. */
const ETIQUETA_TIPO: Record<TipoFuente, string> = {
  sql: "Base de datos",
  documento: "Documento interno",
  api_publica: "API pública",
  modelo_ml: "Modelo predictivo",
};

interface CardProps {
  fuente: Fuente;
}

/** Una fuente, expandible. Colapsada muestra lo que alguien necesita para
 * decidir si confía en la afirmación (qué tipo de fuente, cuál, cuándo);
 * expandida suma el identificador técnico y la sección — detalle real, no
 * un "fragmento relevante" ni un score inventados: `Fuente` no trae esos
 * campos (`apps/web/src/api/types.ts`), y este componente no les mockea
 * uno solo para parecer más rico. */
function FuenteCard({ fuente }: CardProps) {
  const [abierta, setAbierta] = useState(false);
  const detalleId = `fuente-detalle-${fuente.id}`;

  return (
    <li className="fuente-card">
      <button
        type="button"
        className="fuente-card__cabecera"
        aria-expanded={abierta}
        aria-controls={detalleId}
        onClick={() => setAbierta((v) => !v)}
      >
        <span className={`fuente-card__tipo fuente-card__tipo--${fuente.tipo}`}>
          {ETIQUETA_TIPO[fuente.tipo]}
        </span>
        <span className="fuente-card__referencia">{fuente.referencia}</span>
        <span className="fuente-card__fecha mono">{fechaLegible(fuente.consultada_en)}</span>
        <svg
          className={`fuente-card__flecha ${abierta ? "fuente-card__flecha--abierta" : ""}`}
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          aria-hidden="true"
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>

      {abierta && (
        <div id={detalleId} className="fuente-card__detalle">
          <dl className="fuente-card__campos">
            <div>
              <dt>Identificador</dt>
              <dd className="mono">{fuente.id}</dd>
            </div>
            {fuente.seccion && (
              <div>
                <dt>Sección</dt>
                <dd>{fuente.seccion}</dd>
              </div>
            )}
            {fuente.url && (
              <div>
                <dt>Enlace</dt>
                <dd>
                  <a href={fuente.url} rel="noopener noreferrer" target="_blank">
                    {fuente.url}
                  </a>
                </dd>
              </div>
            )}
          </dl>
        </div>
      )}
    </li>
  );
}

export function FuentesTabla({ fuentes }: Props) {
  if (!fuentes.length) return null;

  return (
    <div className="subbloque">
      <p className="etiqueta">Fuentes declaradas</p>
      <ul className="fuentes-lista">
        {fuentes.map((f) => (
          <FuenteCard key={f.id} fuente={f} />
        ))}
      </ul>
    </div>
  );
}
