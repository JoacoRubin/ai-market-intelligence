interface Props {
  id: string;
}

/** No hace fetch + blob: es un `<a href>` directo a `.pdf`, igual patrón que
 * `.descarga` en `docs/replay/replay.js` — el navegador maneja la descarga
 * nativamente. Solo se renderiza cuando ya hay informe (ver `AnalisisDetalle`),
 * así que no necesita un estado "deshabilitado" propio. */
export function DescargarInformeEnlace({ id }: Props) {
  return (
    <a className="descarga" href={`/analyses/${encodeURIComponent(id)}.pdf`} download={`informe-${id}.pdf`}>
      Descargar el informe en PDF
    </a>
  );
}
