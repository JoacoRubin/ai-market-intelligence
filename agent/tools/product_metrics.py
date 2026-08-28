"""Tool `product_metrics`: KPIs comerciales desde SQL Server.

Los argumentos de esta función los genera un modelo de lenguaje. Eso los
convierte en entrada no confiable —igual que los de un formulario público— y el
esquema de abajo es la primera de tres capas de defensa:

  1. **Este esquema**: describe qué es un argumento válido. Lista blanca.
  2. **Consulta parametrizada** (`core.kpis`): los valores nunca se concatenan.
  3. **Usuario read-only** (`core.db`): el motor no permite escribir.

La validación es por lista BLANCA y no por lista negra. No se intenta detectar
ataques —esa carrera se pierde siempre, siempre aparece una codificación nueva—
sino describir con precisión qué es un identificador de producto: la letra P y
hasta seis dígitos. Todo lo demás se rechaza sin analizarlo.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator, model_validator

from agent.tools.registry import ToolName
from core.kpis import metricas_de_producto
from core.report import MetricaProducto

if TYPE_CHECKING:
    from agent.state import AnalysisState

NOMBRE = ToolName.PRODUCT_METRICS
PATRON_PRODUCTO = re.compile(r"^P\d{1,6}$")
MAX_PRODUCTOS = 10
MAX_DIAS_RANGO = 366 * 3


class EntradaProductMetrics(BaseModel):
    """Argumentos válidos de la herramienta."""

    product_ids: list[str] = Field(
        min_length=1, max_length=MAX_PRODUCTOS,
        description="Identificadores de producto, con formato P seguido de dígitos "
                    "(por ejemplo P001).",
    )
    desde: date = Field(description="Inicio del período, inclusive (AAAA-MM-DD).")
    hasta: date = Field(description="Fin del período, inclusive (AAAA-MM-DD).")

    @field_validator("product_ids")
    @classmethod
    def _formato_de_identificador(cls, valores: list[str]) -> list[str]:
        invalidos = [v for v in valores if not PATRON_PRODUCTO.match(v)]
        if invalidos:
            raise ValueError(
                f"identificadores con formato inválido: {invalidos}. "
                "Se espera la letra P seguida de hasta seis dígitos, como 'P001'."
            )
        # Duplicados: el modelo repite productos con frecuencia. Normalizar es
        # mejor que rechazar la llamada entera y gastar un reintento en algo
        # que no cambia el resultado.
        return list(dict.fromkeys(valores))

    @model_validator(mode="after")
    def _rango_razonable(self) -> EntradaProductMetrics:
        if self.desde > self.hasta:
            raise ValueError(
                f"período invertido: desde={self.desde} es posterior a hasta={self.hasta}"
            )
        if (self.hasta - self.desde) > timedelta(days=MAX_DIAS_RANGO):
            raise ValueError(
                f"el rango supera los {MAX_DIAS_RANGO} días. Un período así no es "
                "un análisis comercial: es un escaneo completo de la tabla."
            )
        return self


def esquema_para_llm() -> dict[str, Any]:
    """Esquema en formato de tool calling de Ollama.

    Se deriva del mismo modelo Pydantic que valida la entrada. Escribirlo a mano
    lo dejaría desincronizado del validador, y entonces el modelo mandaría algo
    que la descripción permitía y el código rechaza — un fallo que parece
    alucinación del modelo y en realidad es nuestro.
    """
    esquema = EntradaProductMetrics.model_json_schema()
    return {
        "type": "function",
        "function": {
            "name": NOMBRE,
            "description": (
                "Consulta KPIs comerciales reales de uno o más productos en un "
                "período: unidades vendidas, revenue, margen, crecimiento contra "
                "el período previo y tasa de devolución. Los valores salen de la "
                "base de datos transaccional. Usar SIEMPRE que la pregunta "
                "involucre números de ventas, comparaciones de performance o "
                "rentabilidad de productos."
            ),
            "parameters": {
                "type": "object",
                "properties": esquema["properties"],
                "required": esquema.get("required", []),
            },
        },
    }


def ejecutar_product_metrics(
    entrada: EntradaProductMetrics, estado: AnalysisState
) -> list[MetricaProducto]:
    """Ejecuta la herramienta y devuelve los KPIs encontrados.

    Un producto que no existe no aborta la ejecución: se informa y se sigue con
    los demás. El modelo puede alucinar un identificador con formato válido, y
    tirar abajo toda la llamada desperdiciaría los datos que sí se obtuvieron.
    """
    import time

    if not estado.puede_llamar_tool():
        estado.registrar_llamada_tool()  # deja la advertencia correspondiente
        return []

    estado.registrar_llamada_tool()
    inicio = time.perf_counter()

    encontrados: list[MetricaProducto] = []
    faltantes: list[str] = []

    for pid in entrada.product_ids:
        metrica = metricas_de_producto(pid, entrada.desde, entrada.hasta)
        # Un producto inexistente devuelve todo en cero y el nombre igual al id:
        # es la señal de que la consulta no encontró la fila del catálogo.
        if metrica.nombre == pid and metrica.unidades == 0:
            faltantes.append(pid)
            continue
        encontrados.append(metrica)

    duracion = int((time.perf_counter() - inicio) * 1000)
    estado.registrar_paso("sql_tool", duracion, tool=NOMBRE)

    if faltantes:
        estado._advertir(
            f"No se encontraron datos para: {', '.join(faltantes)}. "
            "El análisis continúa con los productos restantes."
        )

    estado.registrar_resultado(NOMBRE, {m.product_id: m for m in encontrados})
    return encontrados
