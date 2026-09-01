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

/** Spinner + reloj de tiempo transcurrido. Sin barra de progreso: no hay ETA
 * confiable (el rango medido hoy va de 6 s a ~2m35s), y una barra que no
 * sabe cuánto falta miente más de lo que informa. */
export function PanelEstado({ estado, creadoEn }: Props) {
  const [segundos, setSegundos] = useState(() => segundosDesde(creadoEn));

  useEffect(() => {
    const t = window.setInterval(() => setSegundos(segundosDesde(creadoEn)), 1000);
    return () => window.clearInterval(t);
  }, [creadoEn]);

  if (estado !== "pendiente" && estado !== "procesando") return null;

  return (
    <div className="panel-estado">
      <span className="panel-estado__spinner" aria-hidden="true" />
      <div>
        <p className="panel-estado__titulo">{ROTULO[estado]}</p>
        <p className="traza__reloj mono">{segundos}s transcurridos</p>
      </div>
    </div>
  );
}
