/**
 * Espejo manual del contrato de la API — `application/models.py` y
 * `core/report.py`. No hay generación automática de tipos (ni OpenAPI
 * codegen, ni Pydantic-to-TS): el proyecto es chico, el contrato cambia poco
 * y una dependencia de generación agregaría un paso de build que hoy nadie
 * necesita. Si el backend cambia un campo, este archivo se actualiza a mano
 * — el mismo trade-off que ya aceptó `theme.css` con el CSS del replay.
 */

export type EstadoAnalisis =
  | "pendiente"
  | "procesando"
  | "completado"
  | "fallido"
  | "cancelado";

export type TipoAfirmacion = "hecho" | "prediccion" | "recomendacion";
export type TipoFuente = "sql" | "documento" | "api_publica" | "modelo_ml";

export interface Fuente {
  id: string;
  tipo: TipoFuente;
  referencia: string;
  consultada_en: string;
  seccion: string | null;
  url: string | null;
}

export interface Afirmacion {
  texto: string;
  tipo: TipoAfirmacion;
  fuentes: string[];
}

export interface MetricaProducto {
  product_id: string;
  nombre: string;
  unidades: number;
  revenue: number;
  margen_pct: number | null;
  crecimiento_pct: number | null;
  tasa_devolucion_pct: number | null;
  fuente: string;
}

export interface Prediccion {
  product_id: string;
  horizonte_dias: number;
  valor: number;
  intervalo_inferior: number | null;
  intervalo_superior: number | null;
  mape_backtest: number | null;
  mape_baseline: number | null;
  modelo_version: string | null;
}

export interface Anomalia {
  product_id: string;
  fecha: string;
  tipo: string;
  desvios: number;
  descripcion: string;
  evidencia: string[];
}

export interface PasoTrace {
  nodo: string;
  duracion_ms: number;
  tool: string | null;
}

export interface Report {
  request_id: string;
  consulta: string;
  generado_en: string;
  modelo_llm: string;
  resumen_ejecutivo: Afirmacion[];
  metricas: MetricaProducto[];
  predicciones: Prediccion[];
  anomalias: Anomalia[];
  contexto_mercado: Afirmacion[];
  recomendaciones: Afirmacion[];
  fuentes: Fuente[];
  trace: PasoTrace[];
  advertencias: string[];
  limitaciones: string[];
}

export interface AnalisisResumen {
  id: string;
  estado: EstadoAnalisis;
  creado_en: string;
  consulta: string;
  product_ids: string[];
  desde: string | null;
  hasta: string | null;
  version: number;
}

export interface Analisis extends AnalisisResumen {
  intencion: string | null;
  informe: Report | null;
  error: string | null;
  etapas: string[];
  advertencias: string[];
}

/**
 * Cuerpo de POST /analyses. Las dos formas son excluyentes — el backend
 * (`apps/api/schemas.py::SolicitudAnalisis`) es la autoridad; este tipo solo
 * modela el shape, la regla XOR se valida en `SolicitudForm`, no acá.
 */
export type SolicitudAnalisis =
  | { consulta: string; product_ids?: undefined; desde?: undefined; hasta?: undefined }
  | { consulta?: undefined; product_ids: string[]; desde: string; hasta: string };

export interface Producto {
  id: string;
  brand: string;
  category: string;
  price: number;
  cost: number;
  launch_date: string;
}

export interface ListaProductos {
  total: number;
  items: Producto[];
}

/** `GET /analyses`. Devuelve `AnalisisResumen` (sin `informe`, liviano) para
 * el historial — más nuevo primero, paginado. `CANCELADO` nunca aparece acá:
 * `apps/api/store.py::listar()` lo excluye. */
export interface ListaAnalisis {
  total: number;
  items: AnalisisResumen[];
}
