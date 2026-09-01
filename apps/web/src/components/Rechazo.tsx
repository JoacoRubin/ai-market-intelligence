import { Alertas } from "./Alertas";

interface Props {
  error: string | null;
  advertencias: string[];
}

/**
 * Que el agente no produzca informe no es una falla que haya que disimular.
 * Poder decir "esto no me corresponde" es la capacidad más difícil de
 * construir y la que casi ningún demo muestra — mismo texto y criterio que
 * `docs/replay/replay.js::construirRechazo`.
 *
 * Cubre DOS causas distintas del mismo bloque visual: `error` presente es
 * una excepción no controlada (estado `fallido`); su ausencia con
 * `informe===null` es el router descartando la consulta con
 * `estado==='completado'` igual (`application/analisis.py:83-104`) — el
 * grafo terminó bien, simplemente no había nada que analizar.
 */
export function Rechazo({ error, advertencias }: Props) {
  return (
    <div className="rechazo">
      {error ? (
        <>
          <h4 className="rechazo__titulo">La ejecución terminó con error</h4>
          <p className="rechazo__texto mono">{error}</p>
        </>
      ) : (
        <>
          <h4 className="rechazo__titulo">El agente decidió que la consulta está fuera de su alcance</h4>
          <p className="rechazo__texto">
            Cortó en el router, antes de planificar o tocar la base. Un agente que siempre intenta
            responder siempre responde algo, aunque no tenga con qué.
          </p>
        </>
      )}
      {advertencias.length > 0 && <Alertas titulo="Advertencias" advertencias={advertencias} />}
    </div>
  );
}
