"""Modelo de una ejecución congelada y su índice.

La decisión de fondo acá es que la captura sea un **modelo tipado** y no un dict
armado a mano. El sitio estático consume este JSON: si el harness escribiera
`duracion` donde el sitio lee `duracion_ms`, no fallaría nada —se vería un
gráfico vacío en el navegador de otra persona, quizá un recruiter—. Con un
modelo, el contrato se rompe acá, en la corrida que lo genera.

Es el mismo argumento que sostiene a `AnalysisState`: un dict suelto convierte
un error de tipeo en un bug que aparece tres capas después.

**Nada se recalcula.** Los tiempos, el plan, las fuentes y las advertencias
viajan tal como quedaron en el estado. Un replay que redondee o rellene está
mostrando una ejecución que no ocurrió, y la trazabilidad que el proyecto
defiende se cae por el lugar más tonto.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, computed_field

from agent.state import AnalysisState, Intencion, PasoPlan, Periodo
from core.report import PasoTrace, Report

# Cómo volver a generar estas capturas. Viaja en el manifiesto y se publica junto
# al replay: sin esto, "es una ejecución real" es una afirmación sin respaldo.
REPRODUCIBLE_CON = "docker compose up -d && .\\tasks.ps1 replay"


class Captura(BaseModel):
    """Una ejecución real del agente, lista para reproducirse sin backend."""

    # --- identidad ---
    id: str
    consulta: str
    capturada_en: datetime
    modelo_llm: str

    # --- cómo interpretó el pedido ---
    intencion: Intencion | None = None
    entidades: list[str] = Field(default_factory=list)
    periodo: Periodo | None = None

    # --- qué decidió hacer y cuánto tardó ---
    plan: list[PasoPlan] = Field(default_factory=list)
    trace: list[PasoTrace] = Field(default_factory=list)
    llamadas_tools: int = 0
    reintentos: int = 0

    # --- qué produjo ---
    informe: Report | None = None
    advertencias: list[str] = Field(default_factory=list)
    error: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duracion_total_ms(self) -> int:
        """Se serializa para que el sitio no tenga que sumar en JavaScript.

        Es un campo calculado y no almacenado justamente para que no pueda
        contradecir al trace: si se guardara aparte, alguien podría editarlo.
        """
        return sum(p.duracion_ms for p in self.trace)

    @classmethod
    def desde_estado(
        cls,
        caso_id: str,
        estado: AnalysisState,
        *,
        capturada_en: datetime,
        modelo_llm: str,
    ) -> Captura:
        """Congela un `AnalysisState` ya ejecutado.

        Que `informe` pueda ser `None` no es un caso degradado que haya que
        disculpar: es lo que pasa cuando el agente decide que la consulta está
        fuera de su alcance y corta en el router. Poder decir "esto no me
        corresponde" es una capacidad del sistema, y el replay la muestra igual
        que a un análisis exitoso.
        """
        return cls(
            id=caso_id,
            consulta=estado.consulta,
            capturada_en=capturada_en,
            modelo_llm=modelo_llm,
            intencion=estado.intencion,
            entidades=list(estado.entidades),
            periodo=estado.periodo,
            plan=list(estado.plan),
            trace=list(estado.trace),
            llamadas_tools=estado.llamadas_tools,
            reintentos=estado.reintentos,
            informe=estado.informe,
            advertencias=list(estado.advertencias),
            error=estado.error,
        )


class ResumenCaso(BaseModel):
    """Fila del índice. Deliberadamente liviana.

    No lleva el informe. Si lo llevara, el navegador descargaría todas las
    ejecuciones completas para dibujar una lista de títulos.
    """

    id: str
    consulta: str
    intencion: Intencion | None = None
    duracion_total_ms: int
    nodos: list[str] = Field(default_factory=list)
    tiene_informe: bool


class Manifiesto(BaseModel):
    """Índice del replay y su declaración pública.

    Es el archivo que el sitio carga primero, y el que sostiene la honestidad
    del demo: dice qué modelo generó estas ejecuciones, cuándo, y cómo
    reproducirlas. Se publica como corrida grabada, no como sistema en vivo.
    """

    capturado_en: datetime
    modelo_llm: str
    total: int
    casos: list[ResumenCaso]
    reproducible_con: str = REPRODUCIBLE_CON

    @classmethod
    def desde_capturas(
        cls, capturas: list[Captura], *, capturado_en: datetime
    ) -> Manifiesto:
        if not capturas:
            raise ValueError(
                "un manifiesto necesita al menos una captura: publicar un "
                "replay sin ejecuciones es publicar una promesa vacía"
            )

        modelos = {c.modelo_llm for c in capturas}
        if len(modelos) > 1:
            # El manifiesto declara UN modelo. Si las corridas salieron de
            # varios, esa declaración es falsa y nadie lo notaría mirando el
            # sitio: se corta acá, donde todavía se ve.
            raise ValueError(
                f"un manifiesto declara un solo modelo, y estas capturas vienen "
                f"de {len(modelos)}: {sorted(modelos)}. Volvé a capturar todo "
                "con el mismo modelo."
            )

        return cls(
            capturado_en=capturado_en,
            modelo_llm=modelos.pop(),
            total=len(capturas),
            casos=[
                ResumenCaso(
                    id=c.id,
                    consulta=c.consulta,
                    intencion=c.intencion,
                    duracion_total_ms=c.duracion_total_ms,
                    nodos=[p.nodo for p in c.trace],
                    tiene_informe=c.informe is not None,
                )
                for c in capturas
            ],
        )
