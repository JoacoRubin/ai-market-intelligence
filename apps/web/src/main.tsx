import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

// Self-hosted (npm), no Google Fonts CDN: un origen de red nuevo evitable
// sigue sin justificarse cuando @fontsource resuelve lo mismo local. Ver
// ADR-013 — es la primera dependencia de runtime del proyecto además de
// React. Pesos reales de la familia (400/500/600/700), nada de negrita
// falseada.
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-sans/700.css";
import "@fontsource/ibm-plex-mono/400.css";

import "./styles/theme.css";
import "./styles/app.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
