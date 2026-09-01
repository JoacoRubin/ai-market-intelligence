import { useEffect, useRef, useState } from "react";
import { obtenerAnalisis, ErrorApi } from "../api/client";
import type { Analisis } from "../api/types";

const INTERVALO_MS = 3000;
const TERMINALES = new Set<Analisis["estado"]>(["completado", "fallido", "cancelado"]);

interface ResultadoPoll {
  analisis: Analisis | null;
  error: string | null;
}

/**
 * Pollea `GET /analyses/{id}` cada 3s hasta un estado terminal. Intervalo
 * fijo, sin backoff: es tráfico local (Vite → localhost), sin costo de red
 * real, y el caso más largo medido en esta sesión (~2m35s de comparación en
 * lenguaje natural) son apenas ~50 pedidos triviales. Ver ADR-013.
 *
 * Un error de red reintenta en el próximo tick en vez de cortar la sesión —
 * un blip no debería tirar todo. El `cleanup` (`cancelado` + `clearTimeout`)
 * es lo que evita que un poll de un análisis viejo siga escribiendo estado
 * después de que el usuario disparó uno nuevo.
 */
export function useAnalysisPoll(id: string | null): ResultadoPoll {
  const [analisis, setAnalisis] = useState<Analisis | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Evita el error clásico de closures obsoletas: el timeout programado en un
  // tick necesita saber, cuando dispara, si YA fue cancelado por un cleanup
  // posterior — una variable de estado de React no sirve acá porque no se lee
  // sincrónicamente dentro del callback ya programado.
  const canceladoRef = useRef(false);

  useEffect(() => {
    setAnalisis(null);
    setError(null);
    if (!id) return;

    canceladoRef.current = false;
    let timer: number | undefined;

    async function tick() {
      try {
        const data = await obtenerAnalisis(id as string);
        if (canceladoRef.current) return;
        setAnalisis(data);
        setError(null);
        if (!TERMINALES.has(data.estado)) {
          timer = window.setTimeout(tick, INTERVALO_MS);
        }
      } catch (e) {
        if (canceladoRef.current) return;
        setError(e instanceof ErrorApi ? e.message : "error de red inesperado");
        timer = window.setTimeout(tick, INTERVALO_MS);
      }
    }

    tick();
    return () => {
      canceladoRef.current = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [id]);

  return { analisis, error };
}
