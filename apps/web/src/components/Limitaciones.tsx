interface Props {
  limitaciones: string[];
}

export function Limitaciones({ limitaciones }: Props) {
  if (!limitaciones.length) return null;

  return (
    <div className="subbloque">
      <p className="etiqueta">Limitaciones declaradas</p>
      <ul className="limitaciones">
        {limitaciones.map((texto, i) => (
          <li key={i}>{texto}</li>
        ))}
      </ul>
    </div>
  );
}
