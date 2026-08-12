"""Métricas de calidad del informe, medidas contra los eventos sembrados.

El ADR-003 dejó un riesgo abierto y escrito: la auditoría numérica dio 22 de 22
cifras verificadas y cero inventadas, y **el informe seguía estando mal**.
Invertía el significado del MAPE, recomendaba sobre el producto equivocado y
tenía en el contexto dos documentos que explicaban la anomalía sin usarlos.

    "Un validador numérico es necesario y no es suficiente. Detecta alucinación
    de cifras; no detecta interpretaciones invertidas, atribuciones equivocadas
    ni evidencia sin usar."

Cada métrica de este módulo es uno de esos defectos, convertido en algo que se
puede contar.

**Por qué no hay un LLM de juez.** Porque no hace falta: `dbo.ground_truth`
declara qué producto tuvo el evento, cuándo y de qué magnitud, y el informe es un
modelo tipado con sus afirmaciones, sus fuentes y sus citas. Todo lo que hay que
comparar está disponible. Delegarlo al modelo daría un número difuso, distinto en
cada corrida, y encima haría que el evaluador comparta el modo de falla del
evaluado. El principio del proyecto aplica también acá: si se puede resolver de
forma determinística, no se delega al LLM.

**Límite conocido:** las métricas 3 y 5 leen texto con reglas léxicas. Detectan
los defectos que ya ocurrieron; no pretenden entender el informe. Una redacción
lo bastante creativa puede burlarlas, y eso está bien: son un piso verificable,
no un techo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, ConfigDict

from core.report import Report

# Los identificadores de producto del dominio: P001, P010, P055.
_PRODUCTO = re.compile(r"\bP\d{3,}\b")

# Palabras que nombran acierto. Si una de estas aparece describiendo un MAPE, el
# informe está diciendo lo contrario de lo que el número significa.
_LEXICO_DE_ACIERTO = ("precis", "exactitud", "acierto", "certeza")


class EventoSembrado(BaseModel):
    """Una fila de `dbo.ground_truth`: la respuesta que el agente no puede ver.

    Es un modelo de Pydantic y no un `dataclass` porque **cruza un borde**: los
    datos vienen del driver ODBC, que devuelve las columnas `DATE` como texto.
    Un dataclass anota `fecha: date` y no valida nada, así que el string viajaba
    intacto hasta reventar tres capas más adelante en un `strftime`. Acá se
    convierte donde el dato entra.

    `Hallazgo`, en cambio, sigue siendo un dataclass: se construye solo desde
    este módulo y nunca viene de afuera. La diferencia no es de estilo.
    """

    model_config = ConfigDict(frozen=True)

    tipo: str
    product_id: str
    fecha: date
    magnitud: float | None = None
    descripcion: str = ""


@dataclass(frozen=True)
class Hallazgo:
    """El resultado de una métrica sobre un informe.

    `cumple` es `bool | None`, y el `None` es la parte importante: significa que
    la métrica **no aplicó**, no que el informe la haya satisfecho. Sin
    predicciones no hay MAPE que malinterpretar; sin recomendaciones no hay
    atribución que juzgar. Contar esos casos como aprobados infla el resultado y
    hace que un medidor que nunca corrió se vea idéntico a uno impecable.

    Es la misma distinción que `MetricaProducto` hace entre `None` y `0`:
    devolver un valor donde no se sabe nada convierte una ausencia en una
    afirmación.

    `detalle` existe porque un booleano suelto no sirve para arreglar nada: al
    fallar hay que saber qué se esperaba y qué se encontró.
    """

    nombre: str
    cumple: bool | None
    detalle: str


@dataclass(frozen=True)
class Proporcion:
    """Cumplimiento de una métrica, con su cobertura a la vista.

    `aplicables` no es un dato de color: una métrica que cumplió 2 de 2 sobre
    veinte corridas no dice lo mismo que una que cumplió 20 de 20. Sin ese
    número, el porcentaje se lee con una confianza que no tiene.
    """

    cumplidos: int
    aplicables: int
    total: int

    @property
    def valor(self) -> float | None:
        """Proporción sobre los casos donde la métrica aplicó. `None` si ninguno."""
        return self.cumplidos / self.aplicables if self.aplicables else None


# --- utilidades de texto -----------------------------------------------------

def _textos(informe: Report) -> list[str]:
    """Todo lo que el informe le dice a una persona, en un solo lugar."""
    return [
        a.texto
        for grupo in (informe.resumen_ejecutivo, informe.contexto_mercado,
                      informe.recomendaciones)
        for a in grupo
    ]


def _sin_separadores(texto: str) -> str:
    """Colapsa los separadores de miles para poder buscar el número crudo.

    Solo colapsa cuando siguen exactamente tres dígitos: así `1.243` y `1 243`
    se vuelven `1243`, pero `5,7` queda intacto y no se convierte en `57`.
    Sin esa precisión, un decimal cualquiera daría falsos positivos.
    """
    return re.sub(r"(?<=\d)[.,\s](?=\d{3}(?!\d))", "", texto)


def _menciona_numero(texto: str, valor: float) -> bool:
    entero = int(abs(valor))
    return str(entero) in _sin_separadores(texto)


def _citadas(informe: Report) -> set[str]:
    return {
        f
        for grupo in (informe.resumen_ejecutivo, informe.contexto_mercado,
                      informe.recomendaciones)
        for a in grupo
        for f in a.fuentes
    }


# --- las cinco métricas ------------------------------------------------------

def _analiza_el_producto_del_evento(informe: Report, evento: EventoSembrado) -> Hallazgo:
    analizados = {m.product_id for m in informe.metricas}
    cumple = evento.product_id in analizados
    return Hallazgo(
        "analiza_el_producto_del_evento", cumple,
        f"el evento es de {evento.product_id}; el informe analiza "
        f"{sorted(analizados) or 'ningún producto'}",
    )


def _atribuye_al_producto_correcto(informe: Report, evento: EventoSembrado) -> Hallazgo:
    """El defecto del ADR-003: recomendar sobre el producto que no tuvo el evento.

    Solo se juzga cuando alguna recomendación nombra un producto. No recomendar
    nada, o recomendar sin nombrar a nadie, no cuenta como error: si la ausencia
    penalizara, el incentivo sería recomendar siempre — justo el comportamiento
    que este sistema evita.
    """
    mencionados: set[str] = set()
    for a in informe.recomendaciones:
        mencionados.update(_PRODUCTO.findall(a.texto))

    if not mencionados:
        return Hallazgo("atribuye_al_producto_correcto", None,
                        "ninguna recomendación nombra un producto: nada que atribuir")

    cumple = evento.product_id in mencionados
    return Hallazgo(
        "atribuye_al_producto_correcto", cumple,
        f"las recomendaciones apuntan a {sorted(mencionados)}; "
        f"el evento fue en {evento.product_id}",
    )


def _reporta_magnitudes_absolutas(informe: Report, _evento: EventoSembrado) -> Hallazgo:
    """El ADR-003: 'omite todas las magnitudes absolutas, habla solo en porcentajes'.

    Un informe que dice "creció 12%" sin decir de cuánto a cuánto obliga al
    lector a confiar. Alcanza con que aparezca UNA magnitud real: no se exige
    recitar la tabla, se exige anclar el análisis en algún número concreto.
    """
    if not informe.metricas:
        return Hallazgo("reporta_magnitudes_absolutas", None,
                        "el informe no trae métricas: no hay magnitud que omitir")

    esperadas = [(m.product_id, v)
                 for m in informe.metricas
                 for v in (m.unidades, m.revenue) if v]
    texto = " ".join(_textos(informe))
    encontradas = [f"{pid}:{int(v)}" for pid, v in esperadas
                   if _menciona_numero(texto, v)]

    return Hallazgo(
        "reporta_magnitudes_absolutas", bool(encontradas),
        f"magnitudes absolutas presentes en el texto: {encontradas or 'ninguna'}",
    )


def _usa_la_evidencia_documental(informe: Report, _evento: EventoSembrado) -> Hallazgo:
    """¿Usó la evidencia que tenía? El ADR-003: 'tiene en el contexto dos
    documentos que explican el pico y concluye sugiere una posible causa externa'.

    Alcanza con **uno**, y eso no es laxitud: es lo que pide la regla 7 del
    prompt del sintetizador. Si el RAG recupera cuatro pasajes y solo uno explica
    el evento, exigir que los cite a los cuatro obligaría a citar los
    irrelevantes. Una métrica no debe pedir más de lo que el sistema debería
    hacer.

    **Hubo una métrica hermana, `no_declara_documentos_sin_usar`, y se eliminó
    el 2026-08-12.** Contaba como defecto todo documento declarado que ningún
    texto citara, y con eso contradecía por escrito al párrafo de arriba: juntas
    equivalían a "citalos todos". Dio 17% sobre un umbral de 75% y el detalle
    mostró por qué. El caso P012 fallaba por no citar `doc_ficha_P012` —la ficha
    del producto, que no explica ningún evento— y el resto, por no citar
    documentos que el RAG había traído sin que vinieran al caso.

    El defecto no estaba en el informe sino en la métrica: `Report.fuentes`
    declara TODA la evidencia recuperada (`synthesizer._fuentes_documentales`),
    porque la lista de fuentes es la biblioteca consultada y no la bibliografía
    citada. Medir el sobrante contra esa lista es medir el recall del RAG y
    llamarlo rigor del informe.
    """
    documentales = {f.id for f in informe.fuentes if f.tipo == "documento"}
    if not documentales:
        return Hallazgo("usa_la_evidencia_documental", None,
                        "no se recuperó evidencia documental: nada que integrar")

    usados = documentales & _citadas(informe)
    return Hallazgo(
        "usa_la_evidencia_documental", bool(usados),
        f"citó {sorted(usados) or 'ningún documento'} "
        f"de {len(documentales)} disponible(s)",
    )


def _no_invierte_el_sentido_del_error(informe: Report, _evento: EventoSembrado) -> Hallazgo:
    """El ADR-003: 'describe un MAPE de 8,3% como precisión del 8,3%'.

    El número es correcto y la afirmación es falsa. Es el caso exacto que un
    validador numérico no puede ver: no hay ninguna cifra que contradecir.
    """
    mapes = [v for p in informe.predicciones
             for v in (p.mape_backtest, p.mape_baseline) if v is not None]
    if not mapes:
        return Hallazgo("no_invierte_el_sentido_del_error", None,
                        "el informe no reporta MAPE: no hay error que malinterpretar")

    for texto in _textos(informe):
        bajo = texto.lower()
        if not any(p in bajo for p in _LEXICO_DE_ACIERTO):
            continue
        for mape in mapes:
            if _menciona_numero(texto, mape) or f"{mape:.1f}".replace(".", ",") in texto:
                return Hallazgo(
                    "no_invierte_el_sentido_del_error", False,
                    f"llama acierto a un error de {mape}: {texto[:90]!r}",
                )

    return Hallazgo("no_invierte_el_sentido_del_error", True,
                    "el MAPE se describe como error")


_METRICAS = (
    _analiza_el_producto_del_evento,
    _atribuye_al_producto_correcto,
    _reporta_magnitudes_absolutas,
    _usa_la_evidencia_documental,
    _no_invierte_el_sentido_del_error,
)


def evaluar(informe: Report, evento: EventoSembrado) -> list[Hallazgo]:
    """Corre las cinco métricas sobre un informe. Orden estable."""
    return [metrica(informe, evento) for metrica in _METRICAS]


def resumir(corridas: list[list[Hallazgo]]) -> dict[str, Proporcion]:
    """Cumplimiento por métrica, contado solo donde la métrica aplicó.

    Las corridas en las que una métrica no aplicó quedan **fuera del
    denominador**. Meterlas dentro fue exactamente el error de la primera
    versión: dos métricas informaron 100% sobre seis corridas en las que no
    habían juzgado nada, y el reporte las mostraba en verde junto a las que sí
    midieron algo.

    Cero corridas devuelve un diccionario vacío y no un 100%: un eval que no
    corrió no puede parecerse a uno que salió impecable.
    """
    if not corridas:
        return {}

    total = len(corridas)
    cumplidos: dict[str, int] = {}
    aplicables: dict[str, int] = {}

    for hallazgos in corridas:
        for h in hallazgos:
            cumplidos.setdefault(h.nombre, 0)
            aplicables.setdefault(h.nombre, 0)
            if h.cumple is None:
                continue
            aplicables[h.nombre] += 1
            cumplidos[h.nombre] += int(h.cumple)

    return {
        nombre: Proporcion(cumplidos[nombre], aplicables[nombre], total)
        for nombre in cumplidos
    }
