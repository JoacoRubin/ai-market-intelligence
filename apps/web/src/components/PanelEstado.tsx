import { useEffect, useState } from "react";
import { segundosDesde } from "../lib/formato";
import type { EstadoAnalisis } from "../api/types";

interface Props {
  estado: EstadoAnalisis;
  creadoEn: string;
}

const ROTULO: Record<"pendiente" | "procesando", string> = {
  pendiente: "En cola",
  procesando: "El agente está trabajando",
};

/** Cifras reales medidas en esta sesión, documentadas como constante — mismo
 * criterio que `DURACION_PANTALLA_MS` en `docs/replay/replay.js`: si se
 * remide el sistema, esto se actualiza acá, no se inventa de nuevo. */
const RANGO_MEDIDO = [
  { segundos: 6, etiqueta: "6s — rechazo" },
  { segundos: 90, etiqueta: "90s — estructurado" },
  { segundos: 280, etiqueta: "280s — lenguaje natural" },
];
const MAXIMO_OBSERVADO_S = 280;

/** Tiempo transcurrido contra el rango real ya medido — NO una barra de
 * progreso. `MetricasTabla` usa `.barra`/`.barra__relleno` para una
 * proporción CONOCIDA (unidades vendidas); acá no hay ETA confiable, así que
 * reusar ese mismo relleno mentiría por la forma aunque el texto dijera lo
 * contrario — la gente lee la forma antes que la etiqueta. Por eso esto es
 * un track SIN relleno con un solo marcador puntual: comunica "no sé cuánto
 * falta, pero sé más o menos cuánto tarda esto normalmente", que es lo único
 * cierto que hay. Pasado el máximo observado el marcador se clava en el
 * borde y el texto lo dice, en vez de quedar mintiendo que "está cerca". */
export function PanelEstado({ estado, creadoEn }: Props) {
  const [segundos, setSegundos] = useState(() => segundosDesde(creadoEn));

  useEffect(() => {
    const t = window.setInterval(() => setSegundos(segundosDesde(creadoEn)), 1000);
    return () => window.clearInterval(t);
  }, [creadoEn]);

  if (estado !== "pendiente" && estado !== "procesando") return null;

  const superado = segundos > MAXIMO_OBSERVADO_S;
  const posicionMarcador = Math.min((segundos / MAXIMO_OBSERVADO_S) * 100, 100);

  return (
    <div className="panel-estado">
      <p className="panel-estado__titulo">{ROTULO[estado]}</p>

      <p className="nota">
        Rango real medido en esta sesión — no hay forma de saber cuánto falta, esto no es una
        barra de progreso.
      </p>

      <div className="rango-contexto">
        <div className="rango-contexto__track">
          {RANGO_MEDIDO.map((marca) => (
            <div
              key={marca.segundos}
              className="rango-contexto__marca"
              style={{ left: `${(marca.segundos / MAXIMO_OBSERVADO_S) * 100}%` }}
            >
              <span className="rango-contexto__marca-etiqueta">{marca.etiqueta}</span>
            </div>
          ))}
          <div
            className="rango-contexto__marcador"
            style={{ left: `${posicionMarcador}%` }}
            aria-hidden="true"
          />
        </div>
      </div>

      <p className="traza__reloj mono">
        {segundos}s transcurridos{superado && " — ya superó el máximo observado"}
      </p>
    </div>
  );
}
