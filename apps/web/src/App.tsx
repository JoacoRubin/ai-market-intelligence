import { useState } from "react";
import { AnalisisDetalle } from "./components/AnalisisDetalle";
import { PanelEstado } from "./components/PanelEstado";
import { Rechazo } from "./components/Rechazo";
import { SolicitudForm } from "./components/SolicitudForm";
import { crearAnalisis, ErrorApi } from "./api/client";
import { useAnalysisPoll } from "./hooks/useAnalysisPoll";
import type { SolicitudAnalisis } from "./api/types";

/**
 * Máquina de estados de una sola pantalla: formulario → polling → detalle.
 * Sin router (nada que bookmarkear, herramienta local de una sesión) y sin
 * librería de estado (todo el grafo cabe en cuatro variables). Ver ADR-013.
 */
export default function App() {
  const [analisisId, setAnalisisId] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [erroresEnvio, setErroresEnvio] = useState<string[] | null>(null);

  const { analisis, error: errorPoll } = useAnalysisPoll(analisisId);

  async function manejarEnvio(solicitud: SolicitudAnalisis) {
    setEnviando(true);
    setErroresEnvio(null);
    try {
      const creado = await crearAnalisis(solicitud);
      setAnalisisId(creado.id);
    } catch (e) {
      setErroresEnvio(e instanceof ErrorApi ? e.detalles : ["error inesperado al enviar"]);
    } finally {
      setEnviando(false);
    }
  }

  function nuevoAnalisis() {
    setAnalisisId(null);
    setErroresEnvio(null);
  }

  return (
    <div className="lienzo">
      <header className="app-header">
        <p className="etiqueta">AI Market &amp; Product Intelligence — Fase 7</p>
        <h1 className="app-header__titulo">Dashboard</h1>
        <p className="app-header__bajada">
          Lanzá un análisis contra el agente en vivo y mirá el detalle completo: KPIs, evidencia
          documental, traza etapa por etapa del grafo, y el informe descargable en PDF.
        </p>
      </header>

      <main>
        {!analisisId && (
          <SolicitudForm enviando={enviando} errores={erroresEnvio} onEnviar={manejarEnvio} />
        )}

        {analisisId && (
          <div className="resultado">
            <button type="button" className="boton" onClick={nuevoAnalisis}>
              ← Nuevo análisis
            </button>

            {errorPoll && !analisis && (
              <div className="vacio">
                <h2>No se pudo consultar el análisis</h2>
                <p>{errorPoll}</p>
              </div>
            )}

            {!analisis && !errorPoll && <div className="cargando">Consultando estado…</div>}

            {analisis && <PanelEstado estado={analisis.estado} creadoEn={analisis.creado_en} />}

            {analisis?.estado === "completado" && <AnalisisDetalle analisis={analisis} />}

            {analisis?.estado === "fallido" && (
              <section className="bloque bloque--rechazo">
                <Rechazo error={analisis.error} advertencias={analisis.advertencias} />
              </section>
            )}

            {analisis?.estado === "cancelado" && (
              <div className="vacio">
                <h2>Este análisis fue cancelado</h2>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
