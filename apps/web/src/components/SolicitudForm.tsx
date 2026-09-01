import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { listarProductos } from "../api/client";
import type { Producto, SolicitudAnalisis } from "../api/types";

interface Props {
  enviando: boolean;
  errores: string[] | null;
  onEnviar: (solicitud: SolicitudAnalisis) => void;
}

type Forma = "natural" | "estructurada";

const FORMAS: { id: Forma; etiqueta: string }[] = [
  { id: "natural", etiqueta: "Lenguaje natural" },
  { id: "estructurada", etiqueta: "Estructurada" },
];

const HOY = new Date().toISOString().slice(0, 10);
const HACE_30_DIAS = new Date(Date.now() - 30 * 86_400_000).toISOString().slice(0, 10);
const MAX_PRODUCTOS = 10;

/**
 * Dos formas excluyentes, igual que `SolicitudAnalisis` en
 * `apps/api/schemas.py`: lenguaje natural (dispara el agente completo,
 * router incluido) o estructurada (product_ids + rango, salta el router).
 * La validación de acá ESPEJA al backend, no lo reemplaza — el 422 real
 * sigue siendo la autoridad si algo se cuela.
 */
export function SolicitudForm({ enviando, errores, onEnviar }: Props) {
  const [forma, setForma] = useState<Forma>("natural");
  const [consulta, setConsulta] = useState("");
  const [productos, setProductos] = useState<Producto[]>([]);
  const [seleccionados, setSeleccionados] = useState<string[]>([]);
  const [desde, setDesde] = useState(HACE_30_DIAS);
  const [hasta, setHasta] = useState(HOY);
  const [erroresLocales, setErroresLocales] = useState<string[]>([]);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    let vivo = true;
    listarProductos()
      .then((r) => {
        if (vivo) setProductos(r.items);
      })
      .catch(() => {
        // El catálogo es una comodidad del formulario (autocompletar ids),
        // no un requisito: si falla, la forma estructurada sigue disponible
        // escribiendo el id a mano.
      });
    return () => {
      vivo = false;
    };
  }, []);

  function validar(): string[] {
    if (forma === "natural") {
      const largo = consulta.trim().length;
      if (largo < 3) return ["la consulta necesita al menos 3 caracteres"];
      if (largo > 500) return ["la consulta no puede superar los 500 caracteres"];
      return [];
    }
    const problemas: string[] = [];
    if (seleccionados.length === 0) problemas.push("elegí al menos un producto");
    if (seleccionados.length > MAX_PRODUCTOS) problemas.push(`máximo ${MAX_PRODUCTOS} productos por análisis`);
    if (desde > hasta) problemas.push("el rango está invertido: 'desde' es posterior a 'hasta'");
    return problemas;
  }

  function manejarEnvio(e: FormEvent) {
    e.preventDefault();
    const problemas = validar();
    setErroresLocales(problemas);
    if (problemas.length) return;

    if (forma === "natural") {
      onEnviar({ consulta: consulta.trim() });
    } else {
      onEnviar({ product_ids: seleccionados, desde, hasta });
    }
  }

  function alternarProducto(id: string) {
    setSeleccionados((prev) => (prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]));
  }

  /** Patrón WAI-ARIA APG de pestañas completo, no a medias: `role="tab"`
   * promete flechas + Home/End, y hasta esta iteración el componente
   * declaraba el rol sin implementar el teclado — peor que no declararlo.
   * Roving tabIndex: solo la pestaña activa tiene tabIndex 0, así Tab entra
   * al grupo una sola vez y las flechas mueven el foco DENTRO. */
  function manejarTeclaPestaña(e: KeyboardEvent<HTMLButtonElement>, indice: number) {
    let siguiente: number;
    if (e.key === "ArrowRight") siguiente = (indice + 1) % FORMAS.length;
    else if (e.key === "ArrowLeft") siguiente = (indice - 1 + FORMAS.length) % FORMAS.length;
    else if (e.key === "Home") siguiente = 0;
    else if (e.key === "End") siguiente = FORMAS.length - 1;
    else return;

    e.preventDefault();
    setForma(FORMAS[siguiente].id);
    tabRefs.current[siguiente]?.focus();
  }

  const todosLosErrores = [...erroresLocales, ...(errores ?? [])];

  return (
    <form className="solicitud" onSubmit={manejarEnvio}>
      <div className="solicitud__pestañas" role="tablist" aria-label="Forma de la consulta">
        {FORMAS.map((f, i) => (
          <button
            key={f.id}
            ref={(el) => {
              tabRefs.current[i] = el;
            }}
            type="button"
            id={`tab-${f.id}`}
            role="tab"
            aria-selected={forma === f.id}
            aria-controls={`panel-${f.id}`}
            tabIndex={forma === f.id ? 0 : -1}
            className={`pestaña ${forma === f.id ? "pestaña--activa" : ""}`}
            onClick={() => setForma(f.id)}
            onKeyDown={(e) => manejarTeclaPestaña(e, i)}
          >
            {f.etiqueta}
          </button>
        ))}
      </div>

      {forma === "natural" ? (
        <div
          className="solicitud__campo"
          role="tabpanel"
          id="panel-natural"
          aria-labelledby="tab-natural"
          tabIndex={0}
        >
          <label htmlFor="consulta" className="etiqueta">
            Consulta
          </label>
          <textarea
            id="consulta"
            className="solicitud__textarea"
            placeholder="Compará P002 y P003 en los últimos 30 días"
            value={consulta}
            onChange={(e) => setConsulta(e.target.value)}
            rows={3}
            maxLength={500}
          />
          <p className="nota">
            Dispara el agente completo, empezando por el router (LLM): entre 6 s (rechazo) y
            varios minutos según lo que pida.
          </p>
        </div>
      ) : (
        <div
          className="solicitud__campo"
          role="tabpanel"
          id="panel-estructurada"
          aria-labelledby="tab-estructurada"
          tabIndex={0}
        >
          <label className="etiqueta">Productos ({seleccionados.length}/{MAX_PRODUCTOS})</label>
          <div className="solicitud__productos">
            {productos.length === 0 && <p className="nota">Cargando catálogo…</p>}
            {productos.map((p) => (
              <label key={p.id} className="solicitud__producto">
                <input
                  type="checkbox"
                  checked={seleccionados.includes(p.id)}
                  onChange={() => alternarProducto(p.id)}
                />
                <span className="mono">{p.id}</span> {p.brand}
              </label>
            ))}
          </div>
          <div className="solicitud__rango">
            <label>
              <span className="etiqueta">Desde</span>
              <input type="date" value={desde} onChange={(e) => setDesde(e.target.value)} />
            </label>
            <label>
              <span className="etiqueta">Hasta</span>
              <input type="date" value={hasta} onChange={(e) => setHasta(e.target.value)} />
            </label>
          </div>
          <p className="nota">Salta el router: entre 44 s y ~2 min según lo que involucre.</p>
        </div>
      )}

      {todosLosErrores.length > 0 && (
        <ul className="alertas">
          {todosLosErrores.map((err, i) => (
            <li key={i} className="alerta alerta--grave">
              <span className="alerta__icono">!!</span>
              <span>{err}</span>
            </li>
          ))}
        </ul>
      )}

      <button type="submit" className="boton boton--primario" disabled={enviando}>
        {enviando ? "Enviando…" : "Lanzar análisis"}
      </button>
    </form>
  );
}
