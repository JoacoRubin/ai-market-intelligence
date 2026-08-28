"""Nodo ReportValidator: decide si lo que el modelo escribió puede salir.

Es determinístico. Verificar que un número del informe salió de una herramienta
es aritmética, no comprensión — y es justamente por eso que se puede confiar en
él para auditar a algo que sí es probabilístico.

Dos lecciones del primer día del proyecto están codificadas acá:

**Un validador numérico es necesario y no es suficiente.** La auditoría inicial
midió 100% de groundedness sobre un informe que igual estaba mal: describía un
MAPE de 8,3% como "precisión del 8,3%" (MAPE es error, no precisión) y
recomendaba reducir devoluciones al producto que menos devolvía. Los números
eran correctos y las afirmaciones falsas. Lo que este nodo sí atrapa —y es lo
más grave— es la cifra inventada.

**El instrumento también hay que validarlo.** El prototipo del auditor contaba
`doc_112` y `§3.2` como claims numéricos, inflando la métrica cerca del doble.
Un eval con falsos positivos es peor que no tener eval: da confianza donde no la
hay. Por eso `extraer_numeros_de_negocio` limpia las referencias antes de mirar
las cifras.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from agent.nodes.comparaciones import Veredicto, explicar, verificar_comparacion
from agent.nodes.numeros import (
    TOLERANCIA_ABSOLUTA,
    TOLERANCIA_RELATIVA,
    extraer_numeros_de_negocio,
)
from core.report import Afirmacion, MetricaProducto, Report

# Fórmulas con las que se pide una acción. El modelo suele redactar
# recomendaciones y etiquetarlas como "hecho": el informe valida la etiqueta,
# no el texto, así que pasan el control y terminan mezcladas con los datos
# verificados. Detectarlas es determinístico y barato.
RECOMENDACION = re.compile(
    r"\b(?:"
    r"se\s+recomienda|recomendamos|recomendable"
    r"|habría\s+que|habria\s+que|convendría|convendria"
    r"|conviene\s+\w+r\b|sería\s+conveniente|seria\s+conveniente"
    r"|se\s+sugiere|sugerimos|deberían?\s+\w+r\b|deberian?\s+\w+r\b"
    r"|es\s+aconsejable|se\s+aconseja"
    r")",
    re.IGNORECASE,
)


def parece_recomendacion(texto: str) -> bool:
    """Indica si el texto pide una acción en vez de describir un hecho."""
    return bool(RECOMENDACION.search(texto or ""))


def _numeros_disponibles(resultados_tools: dict[str, Any]) -> set[float]:
    """Todas las magnitudes que las herramientas realmente produjeron."""
    disponibles: set[float] = set()
    for resultado in resultados_tools.values():
        valores = resultado.values() if isinstance(resultado, dict) else resultado
        for m in valores or []:
            if not isinstance(m, MetricaProducto):
                continue
            for campo in (m.unidades, m.revenue, m.margen_pct,
                          m.crecimiento_pct, m.tasa_devolucion_pct):
                if campo is not None:
                    disponibles.add(float(campo))
                    disponibles.add(abs(float(campo)))
    return disponibles


def _numeros_del_documento(
    evidencia: list[dict[str, Any]], doc_ids: set[str]
) -> set[float]:
    """Cifras que figuran textualmente en los pasajes que la afirmación cita.

    Un documento recuperado es una fuente, y este nodo no la estaba mirando:
    `_numeros_disponibles` recorre solo `MetricaProducto`, así que toda cifra
    tomada de un pasaje se leía como inventada.

    Medido el 2026-08-12: el sintetizador citaba `doc_promo_001` en 3 de 3
    corridas de P016 y el eval registraba ese caso como fallado. En el medio
    estaba este nodo, borrando "La campaña de descuento del 30% ... explicó ..."
    porque el 30 no salía de SQL. Con la afirmación se iba la cita, y el informe
    perdía la única frase que decía POR QUÉ.

    **El permiso es por documento citado, no por informe.** Habilitar cualquier
    número del corpus para cualquier afirmación aflojaría el guardrail de más:
    una frase sin fuente podría tomar prestada una cifra que nunca miró. Así, la
    cita deja de ser decorativa y pasa a ser lo que autoriza el número.
    """
    numeros: set[float] = set()
    for pasaje in evidencia:
        if pasaje.get("doc_id") not in doc_ids:
            continue
        for valor in extraer_numeros_de_negocio(pasaje.get("texto", "")):
            numeros.add(valor)
            numeros.add(abs(valor))
    return numeros


# Mitad de la sustancia de la afirmación tiene que estar en el documento que
# cita. El umbral se fijó sobre un hueco ANCHO, no ajustado al decimal: medido
# contra las tres citas documentales de las capturas del replay, la afirmación
# fabricada dio 20% y las dos correctas 75% y 100%.
#
# Dos muestras buenas son pocas para calibrar un número, y por eso el umbral va
# donde el hueco es más grande y no donde separa por poco. Es un piso
# verificable, no un techo: una paráfrasis lo bastante creativa lo pasa, y está
# bien que así sea.
ANCLAJE_MINIMO = 0.5

# Cuatro letras para descartar preposiciones y artículos sin listarlos, y raíz
# de cinco para que el español no pelee contra sí mismo: "costura"/"costuras" y
# "explica"/"explican" son la misma palabra, y compararlas enteras produce un
# falso negativo. Lo produjo, de hecho, en la primera medición de este arreglo.
_LARGO_MINIMO_DE_PALABRA = 4
_LARGO_DE_RAIZ = 5


def _raices(texto: str) -> set[str]:
    """Las raíces de las palabras con contenido, sin tildes ni mayúsculas."""
    plano = unicodedata.normalize("NFD", texto.lower())
    plano = "".join(c for c in plano if unicodedata.category(c) != "Mn")
    return {
        p[:_LARGO_DE_RAIZ]
        for p in re.findall(r"[a-z0-9]+", plano)
        if len(p) >= _LARGO_MINIMO_DE_PALABRA
    }


def _identidad_de_productos(resultados_tools: dict[str, Any]) -> set[str]:
    """Las palabras que nombran a los productos analizados.

    Se descuentan del anclaje porque **aparecen en todo documento sobre ese
    producto**: la ficha, el reporte de stock y la comunicación del proveedor
    dicen los tres "Vertex calzado (P001)". Que coincidan no dice nada sobre si
    el documento sostiene la afirmación.

    Era exactamente lo único que coincidía en el caso publicado: la afirmación
    inventada compartía con su ficha `vertex`, `calzado`, `p001` y `proveedor`,
    y ninguna de las cuatro palabras que decían algo.
    """
    identidad: set[str] = set()
    for resultado in resultados_tools.values():
        valores = resultado.values() if isinstance(resultado, dict) else resultado
        for m in valores or []:
            if isinstance(m, MetricaProducto):
                identidad |= _raices(f"{m.nombre} {m.product_id}")
    return identidad


def _anclada_en_su_cita(
    texto: str, fuentes: list[str], evidencia: list[dict[str, Any]],
    identidad: set[str],
) -> bool:
    """¿El documento citado sostiene lo que la afirmación dice?

    No pretende entender el texto: verifica que la afirmación esté hecha de las
    palabras del documento. Es el mismo criterio que ya se le aplica a las
    cifras —un número vale si figura en el pasaje citado— extendido a la prosa
    de las afirmaciones que no traen ninguna.

    Sin pasajes del documento citado no se juzga: la ausencia de evidencia no es
    evidencia de invención, y castigarla borraría afirmaciones válidas cada vez
    que el trace no arrastre el texto.
    """
    citados = {p.get("doc_id") for p in evidencia} & set(fuentes)
    if not citados:
        return True

    del_documento = _raices(" ".join(
        str(p.get("texto", "")) for p in evidencia
        if p.get("doc_id") in citados
    ))
    if not del_documento:
        return True

    sustancia = _raices(texto) - identidad
    if not sustancia:
        return False

    return len(sustancia & del_documento) / len(sustancia) >= ANCLAJE_MINIMO


def _esta_respaldado(valor: float, disponibles: set[float]) -> bool:
    for referencia in disponibles:
        if abs(valor - referencia) <= TOLERANCIA_ABSOLUTA:
            return True
        if referencia and abs(valor - referencia) / abs(referencia) <= TOLERANCIA_RELATIVA:
            return True
    return False


@dataclass
class ResultadoValidacion:
    informe: Report
    aprobado: bool
    groundedness: float
    afirmaciones_rechazadas: list[str] = field(default_factory=list)


def _filtrar(
    afirmaciones: list[Afirmacion], disponibles: set[float],
    evidencia: list[dict[str, Any]] | None = None,
    identidad: set[str] | None = None,
) -> tuple[list[Afirmacion], list[str], int, int]:
    """Aplica las verificaciones a cada afirmación.

    Primero de dónde salieron los números; después si la relación que el texto
    afirma sobre ellos es cierta. Una afirmación puede pasar la primera y fallar
    la segunda: "lidera con 242 frente a 257" tiene ambas cifras respaldadas y
    dice algo aritméticamente falso.

    Y las que no traen números se verifican contra el documento que citan, que
    hasta el 2026-08-28 era el hueco por donde entraba todo: sin cifras que
    auditar, la afirmación se aceptaba sin mirarla.
    """
    aceptadas, rechazadas = [], []
    con_numeros = respaldadas = 0

    for a in afirmaciones:
        numeros = extraer_numeros_de_negocio(a.texto)
        if not numeros:
            # Sin cifras, lo único que respalda a la afirmación es el documento
            # que cita. Se descarta la AFIRMACIÓN y no solo la cita: quitarle la
            # fuente la dejaría atribuida a los datos, y un hecho inventado con
            # el sello de SQL es peor que uno con una cita que no lo sostiene.
            if not _anclada_en_su_cita(
                a.texto, a.fuentes, evidencia or [], identidad or set()
            ):
                rechazadas.append(
                    f"{a.texto!r} — el documento citado "
                    f"({', '.join(a.fuentes)}) no sostiene lo que afirma"
                )
                continue
            aceptadas.append(a)
            continue

        con_numeros += 1
        # Los datos de las herramientas, más las cifras del documento que ESTA
        # afirmación cita. La cita es lo que habilita el número.
        permitidos = disponibles | _numeros_del_documento(
            evidencia or [], set(a.fuentes))
        inventados = [n for n in numeros if not _esta_respaldado(n, permitidos)]
        if inventados:
            rechazadas.append(
                f"{a.texto!r} — cifras sin respaldo en los datos: "
                f"{sorted(inventados)}"
            )
            continue

        # Segunda capa: los números son reales, ¿la comparación se sostiene?
        if verificar_comparacion(a.texto) is Veredicto.CONTRADICTORIA:
            rechazadas.append(explicar(a.texto))
            continue

        respaldadas += 1
        aceptadas.append(a)

    return aceptadas, rechazadas, con_numeros, respaldadas


def validar_informe(informe: Report, resultados_tools: dict[str, Any],
                    evidencia: list[dict[str, Any]] | None = None
                    ) -> ResultadoValidacion:
    """Verifica que cada cifra del informe provenga de una herramienta.

    Las afirmaciones que inventan números **se eliminan**, no se marcan.
    Dejarlas con una nota al pie confía en que alguien lea la nota; el informe
    tiene que ser correcto por sí mismo.

    Las recomendaciones no se validan numéricamente: son juicios derivados, no
    datos. Exigirles respaldo las eliminaría siempre y el informe perdería su
    parte accionable.
    """
    disponibles = _numeros_disponibles(resultados_tools)

    # Antes de validar cifras: las recomendaciones mal etiquetadas se mueven a
    # su sección. No se descartan — la información es útil, el problema es dónde
    # estaba puesta.
    reubicadas = [a for a in informe.resumen_ejecutivo
                  if parece_recomendacion(a.texto)]
    if reubicadas:
        informe.resumen_ejecutivo = [
            a for a in informe.resumen_ejecutivo if a not in reubicadas
        ]
        informe.recomendaciones = [
            *informe.recomendaciones,
            *[Afirmacion(texto=a.texto, tipo="recomendacion") for a in reubicadas],
        ]

    identidad = _identidad_de_productos(resultados_tools)

    resumen, rech_resumen, con_num_r, ok_r = _filtrar(
        informe.resumen_ejecutivo, disponibles, evidencia, identidad)
    contexto, rech_contexto, con_num_c, ok_c = _filtrar(
        informe.contexto_mercado, disponibles, evidencia, identidad)

    informe.resumen_ejecutivo = resumen
    informe.contexto_mercado = contexto

    rechazadas = rech_resumen + rech_contexto
    total_con_numeros = con_num_r + con_num_c
    respaldadas = ok_r + ok_c

    groundedness = 1.0 if total_con_numeros == 0 else respaldadas / total_con_numeros

    if rechazadas:
        # El motivo concreto viaja en cada entrada: puede ser una cifra sin
        # respaldo o una comparación aritméticamente falsa. Un informe que
        # explica mal por qué descartó algo confunde más de lo que aclara.
        plural = "afirmaciones" if len(rechazadas) > 1 else "afirmación"
        informe.advertencias.append(
            f"Se descartaron {len(rechazadas)} {plural} que no superaron la "
            f"validación: {'; '.join(rechazadas)}"
        )

    return ResultadoValidacion(
        informe=informe,
        aprobado=not rechazadas,
        groundedness=groundedness,
        afirmaciones_rechazadas=rechazadas,
    )
