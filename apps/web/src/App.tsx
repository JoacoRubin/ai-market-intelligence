import { useCallback, useEffect, useRef, useState } from "react";
import { AnalisisDetalle } from "./components/AnalisisDetalle";
import { HistorialAnalisis } from "./components/HistorialAnalisis";
import { PanelEstado } from "./components/PanelEstado";
import { Rechazo } from "./components/Rechazo";
import { SolicitudForm } from "./components/SolicitudForm";
import { crearAnalisis, listarAnalisis, ErrorApi } from "./api/client";
import { useAnalysisPoll } from "./hooks/useAnalysisPoll";
import type { AnalisisResumen, SolicitudAnalisis } from "./api/types";

const EN_CURSO = new Set(["pendiente", "procesando"]);

/**
 * Máquina de estados de una sola pantalla: formulario → polling → detalle,
 * más el historial navegable en el rail izquierdo. Sin router (nada que
 * bookmarkear, herramienta local de una sesión) y sin librería de estado
 * (todo el grafo cabe en un puñado de variables). Ver ADR-013.
 *
 * Seleccionar un ítem del historial NO necesita un hook nuevo: `useAnalysisPoll`
 * ya hace un solo fetch y para si el estado ya es terminal — es el mismo
 * `setAnalisisId(id)` que ya existe para un análisis recién creado.
 */
export default function App() {
  const [analisisId, setAnalisisId] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [erroresEnvio, setErroresEnvio] = useState<string[] | null>(null);

  const [historial, setHistorial] = useState<AnalisisResumen[]>([]);
  const [cargandoHistorial, setCargandoHistorial] = useState(true);
  const [errorHistorial, setErrorHistorial] = useState<string | null>(null);

  const [mensajePolite, setMensajePolite] = useState("");
  const [mensajeAssertive, setMensajeAssertive] = useState("");
  const estadoAnteriorRef = useRef<string | null>(null);

  const { analisis, error: errorPoll } = useAnalysisPoll(analisisId);

  const refrescarHistorial = useCallback(async () => {
    try {
      const r = await listarAnalisis();
      setHistorial(r.items);
      setErrorHistorial(null);
    } catch (e) {
      setErrorHistorial(e instanceof ErrorApi ? e.message : "no se pudo cargar el historial");
    } finally {
      setCargandoHistorial(false);
    }
  }, []);

  useEffect(() => {
    refrescarHistorial();
  }, [refrescarHistorial]);

  // Reinicia la memoria de transición al cambiar de análisis activo — abrir
  // un ítem ya terminado del historial no debe anunciarse como si acabara
  // de pasar, solo una transición presenciada EN VIVO durante esta sesión.
  useEffect(() => {
    estadoAnteriorRef.current = null;
  }, [analisisId]);

  useEffect(() => {
    if (!analisis) return;
    const anterior = estadoAnteriorRef.current;
    estadoAnteriorRef.current = analisis.estado;

    const eraEnCurso = anterior !== null && EN_CURSO.has(anterior);
    const esTerminalAhora = !EN_CURSO.has(analisis.estado);
    if (!eraEnCurso || !esTerminalAhora) return;

    refrescarHistorial();

    if (analisis.estado === "fallido") {
      setMensajeAssertive("El análisis terminó con error.");
    } else if (analisis.estado === "completado") {
      setMensajePolite(analisis.informe ? "Análisis completado." : "El agente rechazó la consulta.");
    } else if (analisis.estado === "cancelado") {
      setMensajePolite("El análisis fue cancelado.");
    }
  }, [analisis, refrescarHistorial]);

  async function manejarEnvio(solicitud: SolicitudAnalisis) {
    setEnviando(true);
    setErroresEnvio(null);
    try {
      const creado = await crearAnalisis(solicitud);
      setAnalisisId(creado.id);
      refrescarHistorial();
    } catch (e) {
      setErroresEnvio(e instanceof ErrorApi ? e.detalles : ["error inesperado al enviar"]);
    } finally {
      setEnviando(false);
    }
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

      {/* Anuncia SOLO la transición terminal a quien usa lector de pantalla —
          nunca el reloj de segundos, que sería un anuncio por segundo. */}
      <div className="sr-only" role="status" aria-live="polite">
        {mensajePolite}
      </div>
      <div className="sr-only" role="alert" aria-live="assertive">
        {mensajeAssertive}
      </div>

      <div className="tablero">
        <HistorialAnalisis
          items={historial}
          activoId={analisisId}
          onSeleccionar={(id) => {
            setAnalisisId(id);
            setErroresEnvio(null);
          }}
          cargando={cargandoHistorial}
          error={errorHistorial}
        />

        <main>
          {!analisisId && (
            <SolicitudForm enviando={enviando} errores={erroresEnvio} onEnviar={manejarEnvio} />
          )}

          {analisisId && (
            <div className="resultado">
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
    </div>
  );
}
