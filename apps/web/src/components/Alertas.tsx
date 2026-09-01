interface Props {
  titulo: string;
  advertencias: string[];
}

/** Una predicción peor que su baseline es más grave que una nota de alcance:
 * el ícono y la palabra lo dicen, el borde solo acompaña. Misma regex que
 * `docs/replay/replay.js::seccionAlertas`. */
export function Alertas({ titulo, advertencias }: Props) {
  if (!advertencias.length) return null;

  return (
    <div className="subbloque">
      <p className="etiqueta">{titulo}</p>
      <ul className="alertas">
        {advertencias.map((texto, i) => {
          const grave = /peor que el baseline/i.test(texto);
          return (
            <li key={i} className={`alerta ${grave ? "alerta--grave" : "alerta--aviso"}`}>
              <span className="alerta__icono">{grave ? "!!" : "!"}</span>
              <span>{texto}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
