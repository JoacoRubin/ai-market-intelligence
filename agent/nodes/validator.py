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
from dataclasses import dataclass, field

from core.report import Afirmacion, MetricaProducto, Report

# Patrones que se eliminan del texto ANTES de buscar números, porque contienen
# dígitos que no son magnitudes de negocio.
REFERENCIAS = re.compile(
    r"""
      doc_\d+                  # identificadores de documento
    | §\s*\d+(?:\.\d+)*        # números de sección
    | \bP\d{1,6}\b             # identificadores de producto
    | \b\w+_v\d+\b             # versiones de modelo (sales_v3)
    | \b[a-z]+\d+(?:\.\d+)*:\S+  # nombres de modelo (llama3.2:3b)
    | \bsql:\S+ | \bml:\S+     # identificadores de fuente
    """,
    re.VERBOSE | re.IGNORECASE,
)

NUMERO = re.compile(r"-?\d[\d.,]*")

# Tolerancia relativa para diferencias de redondeo: el modelo puede escribir
# 31,2% donde la métrica dice 31,23%. Eso es redondeo, no invención, y
# rechazarlo vaciaría informes correctos.
TOLERANCIA_RELATIVA = 0.02
TOLERANCIA_ABSOLUTA = 0.5


def _a_float(token: str) -> float | None:
    """Interpreta un número en formato español (1.243 -> 1243 ; 31,2 -> 31.2)."""
    token = token.strip(".,")
    if not token or not any(c.isdigit() for c in token):
        return None
    negativo = token.startswith("-")
    token = token.lstrip("-")
    try:
        if "," in token:
            valor = float(token.replace(".", "").replace(",", "."))
        else:
            partes = token.split(".")
            if len(partes) > 1 and all(len(p) == 3 for p in partes[1:]):
                valor = float("".join(partes))
            else:
                valor = float(token)
    except ValueError:
        return None
    return -valor if negativo else valor


def extraer_numeros_de_negocio(texto: str) -> set[float]:
    """Devuelve las magnitudes del texto, ignorando referencias e identificadores.

    Limpiar primero y extraer después es lo que evita el falso positivo que
    tenía el prototipo: `doc_112` aportaba un 112 que nunca fue un dato.
    """
    limpio = REFERENCIAS.sub(" ", texto)
    valores = {_a_float(m.group()) for m in NUMERO.finditer(limpio)}
    return {v for v in valores if v is not None}


def _numeros_disponibles(resultados_tools: dict) -> set[float]:
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
    afirmaciones: list[Afirmacion], disponibles: set[float]
) -> tuple[list[Afirmacion], list[str], int, int]:
    """Separa las afirmaciones respaldadas de las que inventan cifras."""
    aceptadas, rechazadas = [], []
    con_numeros = respaldadas = 0

    for a in afirmaciones:
        numeros = extraer_numeros_de_negocio(a.texto)
        if not numeros:
            aceptadas.append(a)
            continue

        con_numeros += 1
        inventados = [n for n in numeros if not _esta_respaldado(n, disponibles)]
        if inventados:
            rechazadas.append(
                f"{a.texto!r} — cifras sin respaldo en los datos: "
                f"{sorted(inventados)}"
            )
        else:
            respaldadas += 1
            aceptadas.append(a)

    return aceptadas, rechazadas, con_numeros, respaldadas


def validar_informe(informe: Report, resultados_tools: dict) -> ResultadoValidacion:
    """Verifica que cada cifra del informe provenga de una herramienta.

    Las afirmaciones que inventan números **se eliminan**, no se marcan.
    Dejarlas con una nota al pie confía en que alguien lea la nota; el informe
    tiene que ser correcto por sí mismo.

    Las recomendaciones no se validan numéricamente: son juicios derivados, no
    datos. Exigirles respaldo las eliminaría siempre y el informe perdería su
    parte accionable.
    """
    disponibles = _numeros_disponibles(resultados_tools)

    resumen, rech_resumen, con_num_r, ok_r = _filtrar(
        informe.resumen_ejecutivo, disponibles)
    contexto, rech_contexto, con_num_c, ok_c = _filtrar(
        informe.contexto_mercado, disponibles)

    informe.resumen_ejecutivo = resumen
    informe.contexto_mercado = contexto

    rechazadas = rech_resumen + rech_contexto
    total_con_numeros = con_num_r + con_num_c
    respaldadas = ok_r + ok_c

    groundedness = 1.0 if total_con_numeros == 0 else respaldadas / total_con_numeros

    if rechazadas:
        informe.advertencias.append(
            f"Se descartaron {len(rechazadas)} afirmaciones con cifras no "
            f"respaldadas por los datos consultados: {'; '.join(rechazadas)}"
        )

    return ResultadoValidacion(
        informe=informe,
        aprobado=not rechazadas,
        groundedness=groundedness,
        afirmaciones_rechazadas=rechazadas,
    )
