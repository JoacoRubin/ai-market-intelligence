import { useEffect, useRef, useState } from "react";
import { nf1 } from "../lib/formato";
import type { PasoTrace } from "../api/types";

/** Cuánto dura la reproducción en pantalla — igual criterio que
 * `docs/replay/replay.js`: una corrida real ronda el minuto o más; hacer
 * esperar eso al usuario reproduciría el problema que la animación existe
 * para comunicar rápido. Los tiempos que se MUESTRAN son los reales. */
const DURACION_PANTALLA_MS = 3200;

/**
 * Reproduce la traza real acelerada — puerto directo de `animar()` en
 * `docs/replay/replay.js`, con un cambio de arquitectura obligatorio: el
 * original muta el DOM directo dentro de un loop de
 * `requestAnimationFrame` (hasta 60 escrituras por segundo). Portar eso a
 * `setState` por frame re-renderizaría el árbol de React 60 veces por
 * segundo — janky y completamente innecesario. Acá el reloj y cada
 * `tramo__relleno` se escriben vía refs imperativos, igual que el original;
 * SOLO `animando` y `etiquetaBoton` son estado de React, porque cambian dos
 * veces por reproducción (arranca / termina), no sesenta.
 *
 * `reproducir()` es opt-in: nunca se llama sola. Un autoplay al abrir cada
 * análisis del historial sería ruido, no interactividad — ver ADR-013.
 */
export function useReproducirTraza(trace: PasoTrace[], total: number) {
  const [animando, setAnimando] = useState(false);
  const [etiquetaBoton, setEtiquetaBoton] = useState("Reproducir");
  const rellenosRef = useRef<(HTMLDivElement | null)[]>([]);
  const relojRef = useRef<HTMLSpanElement | null>(null);
  const frameRef = useRef<number | null>(null);

  // Limpieza si el componente se desmonta (se selecciona otro análisis)
  // mientras la animación está en curso — sin esto, requestAnimationFrame
  // sigue programándose sobre refs que ya no apuntan a nada montado.
  useEffect(() => {
    return () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    };
  }, []);

  function reproducir() {
    // Reduced-motion no es un guard de CSS acá: la animación corre por
    // requestAnimationFrame, no por `transition`/`animation` de CSS, así que
    // el guard global de theme.css no la alcanza. Se salta directo al
    // estado final, mismo criterio que el original.
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      if (relojRef.current) relojRef.current.textContent = nf1(total / 1000);
      return;
    }
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);

    setAnimando(true);
    setEtiquetaBoton("Reproduciendo…");
    for (const relleno of rellenosRef.current) {
      if (relleno) relleno.style.transform = "scaleX(0)";
    }

    const inicios: number[] = [];
    let acumulado = 0;
    for (const paso of trace) {
      inicios.push(acumulado);
      acumulado += paso.duracion_ms;
    }

    const arranque = performance.now();

    function marco(ahora: number) {
      const avance = Math.min((ahora - arranque) / DURACION_PANTALLA_MS, 1);
      const msReales = avance * total;

      trace.forEach((paso, i) => {
        const local = paso.duracion_ms ? (msReales - inicios[i]) / paso.duracion_ms : 1;
        const relleno = rellenosRef.current[i];
        if (relleno) relleno.style.transform = `scaleX(${Math.min(Math.max(local, 0), 1)})`;
      });

      if (relojRef.current) relojRef.current.textContent = nf1(msReales / 1000);

      if (avance < 1) {
        frameRef.current = requestAnimationFrame(marco);
      } else {
        frameRef.current = null;
        setAnimando(false);
        setEtiquetaBoton("Reproducir de nuevo");
      }
    }

    frameRef.current = requestAnimationFrame(marco);
  }

  return { animando, etiquetaBoton, reproducir, rellenosRef, relojRef };
}
