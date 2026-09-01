import type { Afirmacion, Fuente } from "../api/types";

interface Props {
  titulo: string;
  afirmaciones: Afirmacion[];
  porFuente: Map<string, Fuente>;
}

/** `.sello--{tipo}` con la palabra siempre visible — nunca solo color, porque
 * un hecho se verifica, una predicción se cuestiona y una recomendación se
 * discute, y mostrar las tres igual es lo que vuelve peligroso a un informe
 * generado por IA (comentario original en `estilos.css`). */
export function Afirmaciones({ titulo, afirmaciones, porFuente }: Props) {
  if (!afirmaciones.length) return null;

  return (
    <div className="subbloque">
      <p className="etiqueta">{titulo}</p>
      <ul className="afirmaciones">
        {afirmaciones.map((a, i) => (
          <li key={i} className="afirmacion">
            <span className={`sello sello--${a.tipo}`}>{a.tipo}</span>
            <p className="afirmacion__texto">
              {a.texto}
              {a.fuentes.length > 0 && (
                <span className="citas">
                  {a.fuentes.map((id) => {
                    const f = porFuente.get(id);
                    return (
                      <span key={id} className="cita" title={f ? `${f.tipo} · ${f.referencia}` : "fuente no declarada"}>
                        {id}
                      </span>
                    );
                  })}
                </span>
              )}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
