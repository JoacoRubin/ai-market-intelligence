"""Modelo del informe: la fuente única de verdad.

Todo lo que el sistema muestra —la API, la web y el PDF— se renderiza desde este
modelo. Si cada salida armara su propio texto, en dos semanas el PDF diría una
cosa y el dashboard otra, y nadie sabría cuál creer.

El modelo hace cumplir las reglas de trazabilidad **en el constructor**. Un
informe que las viola no se puede construir: no es que se detecte tarde, es que
no llega a existir. Esa es la diferencia entre una validación que hay que
acordarse de llamar y una que corre siempre.

Reglas que el modelo garantiza:

1. Toda afirmación de tipo `hecho` tiene al menos una fuente.
2. Toda fuente citada existe en la lista de fuentes declaradas.
3. El Executive Summary no admite recomendaciones, y las recomendaciones no
   admiten hechos: la frontera entre "lo que pasó" y "lo que convendría hacer"
   no se cruza.
4. Se registra siempre qué modelo generó el informe.
5. Las predicciones sin backtesting, o peores que su baseline, producen una
   advertencia automática que viaja con el informe.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

TipoAfirmacion = Literal["hecho", "prediccion", "recomendacion"]
TipoFuente = Literal["sql", "documento", "api_publica", "modelo_ml"]


class Fuente(BaseModel):
    """Origen verificable de una afirmación.

    `consultada_en` importa más de lo que parece: un dato público consultado
    hace tres meses puede haber cambiado, y el lector del PDF no tiene forma de
    saberlo si el informe no lo dice.
    """

    id: str
    tipo: TipoFuente
    referencia: str
    consultada_en: datetime
    seccion: str | None = None
    url: str | None = None


class Afirmacion(BaseModel):
    """Una frase del informe, con su naturaleza declarada.

    El tipo no es metadato decorativo: define cómo debe leerse. Un hecho se
    verifica, una predicción se cuestiona, una recomendación se discute.
    Presentar los tres con el mismo formato es lo que hace que un informe
    generado por IA sea peligroso.
    """

    texto: str = Field(min_length=1)
    tipo: TipoAfirmacion
    fuentes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _hecho_requiere_fuente(self) -> Afirmacion:
        if self.tipo == "hecho" and not self.fuentes:
            raise ValueError(
                f"afirmación de tipo 'hecho' sin fuente: {self.texto!r}. "
                "Todo hecho del informe debe poder rastrearse."
            )
        return self


class MetricaProducto(BaseModel):
    """KPIs de un producto. Estos números vienen de SQL, nunca del LLM."""

    product_id: str
    nombre: str
    unidades: int = Field(ge=0)
    revenue: float = Field(ge=0)
    margen_pct: float
    crecimiento_pct: float | None = None
    tasa_devolucion_pct: float | None = Field(default=None, ge=0, le=100)
    fuente: str


class Prediccion(BaseModel):
    """Salida de un modelo de ML, con su calidad medida.

    `mape_baseline` no es opcional por capricho de diseño: sin comparar contra
    un baseline naïve, un MAPE aislado no dice si el modelo sirve. Se permite
    ausente, pero el informe lo advierte.
    """

    product_id: str
    horizonte_dias: int = Field(gt=0)
    valor: float
    intervalo_inferior: float | None = None
    intervalo_superior: float | None = None
    mape_backtest: float | None = Field(default=None, ge=0)
    mape_baseline: float | None = Field(default=None, ge=0)
    modelo_version: str | None = None

    @property
    def supera_al_baseline(self) -> bool | None:
        if self.mape_backtest is None or self.mape_baseline is None:
            return None
        return self.mape_backtest < self.mape_baseline


class Anomalia(BaseModel):
    """Desvío detectado, con la evidencia que lo explica (si la hay)."""

    product_id: str
    fecha: date
    tipo: str
    desvios: float
    descripcion: str
    evidencia: list[str] = Field(default_factory=list)


class PasoTrace(BaseModel):
    """Una etapa del grafo, con lo que tardó. Alimenta 'Cómo se obtuvo'."""

    nodo: str
    duracion_ms: int = Field(ge=0)
    tool: str | None = None


class Report(BaseModel):
    """Informe ejecutivo completo. Renderizable a JSON, HTML, Markdown y PDF."""

    request_id: str
    consulta: str
    generado_en: datetime
    modelo_llm: str

    resumen_ejecutivo: list[Afirmacion] = Field(default_factory=list)
    metricas: list[MetricaProducto] = Field(default_factory=list)
    predicciones: list[Prediccion] = Field(default_factory=list)
    anomalias: list[Anomalia] = Field(default_factory=list)
    contexto_mercado: list[Afirmacion] = Field(default_factory=list)
    recomendaciones: list[Afirmacion] = Field(default_factory=list)

    fuentes: list[Fuente] = Field(default_factory=list)
    trace: list[PasoTrace] = Field(default_factory=list)
    advertencias: list[str] = Field(default_factory=list)
    limitaciones: list[str] = Field(default_factory=list)

    # --- separación de naturaleza ------------------------------------------

    @field_validator("resumen_ejecutivo")
    @classmethod
    def _resumen_sin_recomendaciones(cls, v: list[Afirmacion]) -> list[Afirmacion]:
        malas = [a for a in v if a.tipo == "recomendacion"]
        if malas:
            raise ValueError(
                "el Executive Summary reporta lo que pasó, no lo que habría que "
                f"hacer. Mover a 'recomendaciones': {malas[0].texto!r}"
            )
        return v

    @field_validator("recomendaciones")
    @classmethod
    def _recomendaciones_sin_hechos(cls, v: list[Afirmacion]) -> list[Afirmacion]:
        malas = [a for a in v if a.tipo == "hecho"]
        if malas:
            raise ValueError(
                "las recomendaciones son juicios, no hechos. Mover a "
                f"'resumen_ejecutivo': {malas[0].texto!r}"
            )
        return v

    # --- trazabilidad cruzada ----------------------------------------------

    @model_validator(mode="after")
    def _toda_cita_existe(self) -> Report:
        declaradas = {f.id for f in self.fuentes}

        def revisar(ids: list[str], donde: str) -> None:
            faltantes = [i for i in ids if i not in declaradas]
            if faltantes:
                raise ValueError(
                    f"{donde} cita una fuente no declarada: {faltantes}. "
                    f"Fuentes disponibles: {sorted(declaradas) or 'ninguna'}"
                )

        for grupo, nombre in (
            (self.resumen_ejecutivo, "resumen_ejecutivo"),
            (self.contexto_mercado, "contexto_mercado"),
            (self.recomendaciones, "recomendaciones"),
        ):
            for a in grupo:
                revisar(a.fuentes, nombre)

        for m in self.metricas:
            revisar([m.fuente], f"métrica de {m.product_id}")

        for an in self.anomalias:
            revisar(an.evidencia, f"anomalía de {an.product_id}")

        return self

    # --- advertencias automáticas ------------------------------------------

    @model_validator(mode="after")
    def _advertir_sobre_predicciones(self) -> Report:
        """Genera advertencias que el informe debe llevar sí o sí.

        No las escribe el LLM y no dependen de que alguien se acuerde: se
        derivan de los datos. Un número de predicción sin margen de error se
        lee como un hecho, y esa confusión es del sistema, no del lector.
        """
        nuevas: list[str] = []
        for p in self.predicciones:
            if p.mape_backtest is None or p.mape_baseline is None:
                nuevas.append(
                    f"La predicción de {p.product_id} a {p.horizonte_dias} días "
                    "no tiene backtesting ni baseline: su error es desconocido."
                )
            elif p.supera_al_baseline is False:
                nuevas.append(
                    f"La predicción de {p.product_id} tiene un error "
                    f"({p.mape_backtest}%) PEOR que el baseline naïve "
                    f"({p.mape_baseline}%). Tomarla con reserva."
                )
        for w in nuevas:
            if w not in self.advertencias:
                self.advertencias.append(w)
        return self

    # --- ayudas para los renderers -----------------------------------------

    def fuente_por_id(self, id_: str) -> Fuente | None:
        return next((f for f in self.fuentes if f.id == id_), None)

    @property
    def duracion_total_ms(self) -> int:
        return sum(p.duracion_ms for p in self.trace)
