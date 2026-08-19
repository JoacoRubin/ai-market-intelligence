"""Tarifas de los modelos y conversión de tokens a dólares.

Vive separado de `agent/llm.py` a propósito. `Uso` guarda **tokens**, que son un
hecho que reporta el proveedor y no cambia nunca. El **costo** es una
interpretación: depende de una tabla de precios que se mueve varias veces al
año y que a veces tiene promociones con fecha de vencimiento.

Guardar dólares dentro del cliente mezclaría las dos cosas y volvería
irreproducible el registro: dentro de seis meses, leyendo una corrida vieja,
nadie podría saber con qué precio se calculó ese número. Así, el registro guarda
los tokens —que se pueden recalcular con cualquier tarifa— y anota contra qué
tabla se convirtieron.

**Por qué el costo importa acá y no es una curiosidad.** El eval mide calidad
desde la Fase 5. Medir calidad sin costo contesta "¿anda bien?" y no contesta
"¿conviene?", que es la pregunta que decide si un sistema se pone en producción.
Un informe que sale perfecto a un dólar la consulta y otro que sale 95% igual a
tres centavos no son el mismo producto, aunque el golden set les dé casi lo
mismo.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.llm import Uso

# Contra qué día son ciertos estos precios. Sin esto la tabla es una afirmación
# sin contexto: los precios de las APIs se mueven, y un número sin la fecha en
# que era cierto no se puede auditar ni actualizar con criterio.
FECHA_TARIFAS = "2026-08-19"


@dataclass(frozen=True)
class Tarifa:
    """Precio de un modelo, en dólares por millón de tokens."""

    usd_por_millon_entrada: float
    usd_por_millon_salida: float
    # Una lectura de cache cuesta ~10% de un token de entrada normal. Se guarda
    # como factor y no como tercer precio porque es una proporción del precio de
    # entrada: si el modelo cambia de precio, esto sigue valiendo.
    factor_cache: float = 0.10


TARIFAS: dict[str, Tarifa] = {
    # --- Anthropic (precios de lista) ---
    "claude-opus-5": Tarifa(5.00, 25.00),
    # Sonnet 5 tiene precio promocional de $2.00/$10.00 hasta el 2026-08-31. Se
    # carga el precio de LISTA a propósito: una tabla que baquea una promoción
    # empieza a subestimar el costo el día que vence, en silencio y justo en la
    # dirección peligrosa. Errar hacia caro es recuperable; errar hacia barato
    # se descubre con la factura.
    "claude-sonnet-5": Tarifa(3.00, 15.00),
    "claude-haiku-4-5": Tarifa(1.00, 5.00),
    # --- Local ---
    # Está en la tabla con tarifa cero y no ausente. Es la diferencia entre "sé
    # que es gratis" y "no sé cuánto cuesta", y la columna de costo de la tabla
    # comparativa necesita la primera.
    "llama3.2:3b": Tarifa(0.0, 0.0),
}


def tarifa_de(modelo: str) -> Tarifa | None:
    """La tarifa del modelo, o `None` si no está cargada."""
    return TARIFAS.get(modelo)


def costo_usd(uso: Uso, modelo: str) -> float | None:
    """Cuánto costó ese uso con ese modelo. `None` si no hay tarifa.

    `None` y no `0.0`: un modelo del que no sabemos el precio **no es gratis**.
    Devolver cero metería un modelo pago en la tabla comparativa como si no
    costara nada, que es la conclusión más cara que alguien podría sacar
    leyendo el registro.

    Es el mismo criterio que ya aplica `registro.py` a las métricas que no
    aplican: una métrica que no juzgó nada no reprobó.
    """
    tarifa = tarifa_de(modelo)
    if tarifa is None:
        return None
    return (
        uso.tokens_entrada * tarifa.usd_por_millon_entrada
        + uso.tokens_salida * tarifa.usd_por_millon_salida
        + uso.tokens_cacheados * tarifa.usd_por_millon_entrada * tarifa.factor_cache
    ) / 1e6
