import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Proxy de dev, no CORS en la API: el navegador solo habla con este origen
// (localhost:5173) y Vite reenvía a la API real. Ver ADR-013 — la alternativa
// era agregar CORSMiddleware a apps/api/main.py, un archivo con 712 tests
// pasando que no necesitaba tocarse por una necesidad puramente de dev server.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/analyses": "http://127.0.0.1:8000",
      "/products": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
});
