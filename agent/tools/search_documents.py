"""Tool `search_documents`: evidencia documental con citas rastreables.

Lo que esta herramienta aporta y SQL no puede: el **porqué**. Una consulta
muestra que las devoluciones de P002 se dispararon el 18 de enero. Solo un
documento puede decir que el proveedor reportó un lote con defectos de costura
esa misma semana.

Dos decisiones que hacen que la evidencia sea confiable:

**Cada pasaje viaja con su identificador.** El informe cita `doc_prov_009 §1.1`,
no "según los documentos internos". Una cita que no se puede abrir es
decoración: parece rigor y no lo es.

**El corte de relevancia es por posición, no por umbral.** El modelo e5 produce
similitudes comprimidas entre 0,80 y 0,90 (ver `rag/indice.py`), así que un
"score > 0,7" dejaría pasar cualquier cosa. Se toman los k primeros y se
descartan los que quedan muy por debajo del mejor.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator

from rag.indice import IndiceVectorial, Resultado

if TYPE_CHECKING:
    from agent.state import AnalysisState

NOMBRE = "search_documents"
MAX_TOP_K = 8

# Un pasaje cuya similitud queda esta distancia por debajo del mejor resultado
# aporta ruido, no contexto. Es relativo al mejor y no absoluto justamente
# porque la escala de e5 está comprimida.
CAIDA_MAXIMA = 0.06


class EntradaSearchDocuments(BaseModel):
    """Argumentos válidos de la herramienta."""

    consulta: str = Field(
        min_length=3, max_length=300,
        description="Qué se busca explicar, en lenguaje natural.",
    )
    product_id: str | None = Field(
        default=None,
        description="Acota la búsqueda a un producto (formato P001).",
    )
    top_k: int = Field(
        default=4, ge=1, le=MAX_TOP_K,
        description="Cantidad máxima de pasajes a recuperar.",
    )

    @field_validator("product_id")
    @classmethod
    def _formato(cls, v: str | None) -> str | None:
        import re

        if v is not None and not re.fullmatch(r"P\d{1,6}", v):
            raise ValueError(
                f"identificador inválido: {v!r}. Se espera P seguido de dígitos."
            )
        return v


def esquema_para_llm() -> dict[str, Any]:
    esquema = EntradaSearchDocuments.model_json_schema()
    return {
        "type": "function",
        "function": {
            "name": NOMBRE,
            "description": (
                "Busca evidencia en documentos internos: comunicaciones de "
                "proveedores, reportes operativos, cierres de campaña y "
                "políticas. Usar para explicar POR QUÉ pasó algo que los "
                "números muestran, o para dar contexto a una anomalía."
            ),
            "parameters": {
                "type": "object",
                "properties": esquema["properties"],
                "required": esquema.get("required", []),
            },
        },
    }


def _filtrar_por_relevancia(resultados: list[Resultado]) -> list[Resultado]:
    if not resultados:
        return []
    mejor = resultados[0].score
    return [r for r in resultados if mejor - r.score <= CAIDA_MAXIMA]


def ejecutar_search_documents(
    entrada: EntradaSearchDocuments,
    estado: AnalysisState,
    indice: IndiceVectorial,
) -> list[dict[str, Any]]:
    """Recupera pasajes relevantes y los deja en la evidencia del estado."""
    if not estado.puede_llamar_tool():
        estado.registrar_llamada_tool()  # deja la advertencia correspondiente
        return []

    estado.registrar_llamada_tool()
    inicio = time.perf_counter()

    periodo = estado.periodo
    resultados = indice.buscar(
        entrada.consulta,
        top_k=entrada.top_k,
        product_id=entrada.product_id,
        # No se filtra por `desde`: un documento anterior al período puede
        # explicarlo igual (una política vigente, un lote despachado antes).
        # Sí se excluye lo posterior: nada fechado después puede haber causado
        # lo que pasó antes.
        hasta=periodo.hasta if periodo else None,
    )
    relevantes = _filtrar_por_relevancia(resultados)

    evidencia = [
        {
            "doc_id": r.chunk.doc_id,
            "chunk_id": r.chunk.chunk_id,
            "titulo": r.chunk.titulo,
            "tipo": r.chunk.tipo,
            "seccion": r.chunk.seccion,
            "fecha": r.chunk.fecha.isoformat(),
            "product_id": r.chunk.product_id,
            "texto": r.chunk.texto,
            "score": round(r.score, 4),
        }
        for r in relevantes
    ]

    duracion = int((time.perf_counter() - inicio) * 1000)
    estado.registrar_paso("rag_tool", duracion, tool=NOMBRE)

    if not evidencia:
        estado._advertir(
            "No se encontró evidencia documental relevante para la consulta. "
            "El análisis se apoya únicamente en los datos cuantitativos."
        )

    estado.evidencia.extend(evidencia)
    estado.registrar_resultado(NOMBRE, evidencia)
    return evidencia
