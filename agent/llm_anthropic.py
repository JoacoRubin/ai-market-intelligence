"""Adaptador Anthropic del puerto `ClienteLLM`.

Es el tercer adaptador del mismo puerto y el primero contra un proveedor que no
es Ollama. Eso es exactamente lo que lo hace valer: `ClienteOllama` y
`ClienteLangChain` hablan con el mismo backend por dos caminos, así que un
puerto moldeado sobre los detalles de Ollama los pasaría a los dos sin
protestar. Este no. Ver ADR-008.

**Por qué el SDK nativo y no `ChatAnthropic` de LangChain.** El adaptador de
LangChain ya existe y agregar un proveedor ahí sería una línea. Se eligió el SDK
igual por una razón concreta: este adaptador tiene que reportar **tokens
consumidos** para que el eval pueda calcular el costo de una corrida, y el
objeto `usage` que trae cada respuesta —con entrada, salida y lecturas de
cache— el SDK lo expone directo mientras LangChain lo normaliza y lo tapa.
Medir es el objetivo del ejercicio; no se elige la capa que esconde la medición.

**Lo que NO se puede sostener igual que en los otros dos adaptadores.**
`ClienteOllama` y `ClienteLangChain` fijan temperatura 0 para clasificar y 0.3
para redactar, y las constantes están compartidas justamente para que el golden
set mida lo mismo por los dos caminos. Acá eso es imposible: los modelos Claude
actuales **rechazan `temperature` con un 400**, el parámetro no existe más. Es
una divergencia real entre proveedores, no una omisión, y significa que la
comparación entre modelos tiene un confound declarado: no se está variando solo
el modelo, también el régimen de sampling. Decirlo es lo que separa una medición
de una decoración.
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal, cast

from langsmith import traceable

from agent.llm import Uso

# Opus 5 es el modelo de referencia. Que sea el más caro es deliberado: fija el
# techo de la comparación, y el resto de la tabla se lee contra él. Se cambia
# por `ANTHROPIC_MODEL` sin tocar código, que es lo que permite barrer modelos.
MODELO_POR_DEFECTO = "claude-opus-5"

ESFUERZOS_VALIDOS = ("low", "medium", "high", "xhigh", "max")

# En Opus 5 el thinking viene ENCENDIDO por defecto y sus tokens se facturan
# como salida. Nuestras dos llamadas son mecánicas —clasificar una consulta,
# redactar cinco oraciones sobre números ya calculados—, así que el esfuerzo
# arranca bajo. Es una hipótesis sobre la relación costo/calidad, no un dogma:
# por eso es parámetro del constructor y un eje más que el eval puede barrer.
ESFUERZO_POR_DEFECTO: Literal["low"] = "low"


def _cerrar(esquema: Any) -> Any:
    """Devuelve el esquema con `additionalProperties: False` en cada objeto.

    Las salidas estructuradas quieren el esquema cerrado; los `ESQUEMA` de los
    nodos se escribieron para Ollama, que no lo pide.

    Se cierra **acá, en el borde, y sobre una copia**. Tocar los `ESQUEMA` de los
    nodos cambiaría también el prompt que manda `ClienteOllama`, y el golden set
    dejaría de medir el mismo sistema que venía midiendo. Mutarlos in-place
    sería todavía peor: son constantes de módulo compartidas, y un `import`
    bastaría para cambiarle la llamada a otro adaptador sin que nadie toque ese
    archivo.

    Recursivo porque el esquema del synthesizer tiene objetos adentro de un
    array, y ese es justo el nivel donde el modelo inventa claves.
    """
    if isinstance(esquema, dict):
        copia = {k: _cerrar(v) for k, v in esquema.items()}
        if copia.get("type") == "object":
            copia.setdefault("additionalProperties", False)
        return copia
    if isinstance(esquema, list):
        return [_cerrar(x) for x in esquema]
    return esquema


class ClienteAnthropic:
    """Cliente contra la API de Anthropic, a través del SDK oficial.

    El cliente del SDK se inyecta para poder testear la traducción sin red y sin
    credenciales. Cuando no se pasa, se construye con la resolución de
    credenciales del propio SDK (`ANTHROPIC_API_KEY`, o el perfil de `ant auth
    login`): duplicar esa lógica acá sería reimplementar algo que el SDK ya hace
    y hace mejor.
    """

    def __init__(
        self,
        modelo: str = MODELO_POR_DEFECTO,
        *,
        cliente: Any | None = None,
        esfuerzo: str = ESFUERZO_POR_DEFECTO,
    ) -> None:
        if esfuerzo not in ESFUERZOS_VALIDOS:
            # Falla al construir y no a mitad de una corrida paga: descubrir que
            # un typo cayó al default después de gastar treinta llamadas es
            # exactamente el defecto de instrumento que ya nos costó una sesión.
            raise ValueError(
                f"esfuerzo={esfuerzo!r} no existe. "
                f"Valores válidos: {', '.join(ESFUERZOS_VALIDOS)}."
            )
        self.nombre = modelo
        self._esfuerzo = esfuerzo
        self._uso = Uso()
        if cliente is None:
            import anthropic

            cliente = anthropic.Anthropic()
        self._cliente = cliente

    # --- El puerto -----------------------------------------------------------

    # Mismo motivo que en ClienteOllama (ver ADR-009): este adaptador usa el
    # SDK nativo de Anthropic, no ChatAnthropic de LangChain, así que no hay
    # callbacks de por medio que lo tracen gratis. `metadata` fijo porque
    # `self.nombre`/`self._esfuerzo` varían por instancia y no por llamada —
    # el reporte de tokens real (`Uso`) ya lo expone `uso()`, que es lo que
    # usa `eval/costo.py`; duplicarlo acá sería otra fuente de verdad para el
    # mismo número.
    @traceable(run_type="llm", name="ClienteAnthropic.estructurado")
    def estructurado(
        self, sistema: str, usuario: str, esquema: dict[str, Any]
    ) -> dict[str, Any]:
        respuesta = self._crear(
            sistema,
            usuario,
            max_tokens=4096,
            output_config={
                "effort": self._esfuerzo,
                "format": {"type": "json_schema", "schema": _cerrar(esquema)},
            },
        )
        # `output_config.format` obliga por gramática, pero lo que vuelve sigue
        # siendo texto, y `json.loads` acepta arrays y escalares. El puerto
        # promete `dict`, así que la frontera se verifica acá y no tres nodos
        # más arriba, donde el error ya no sabe de dónde vino.
        datos = json.loads(self._texto(respuesta))
        if not isinstance(datos, dict):
            raise TypeError(
                f"ClienteAnthropic esperaba un dict del modelo y recibió "
                f"{type(datos).__name__}. El modelo no respetó el esquema."
            )
        return cast(dict[str, Any], datos)

    @traceable(run_type="llm", name="ClienteAnthropic.redactar")
    def redactar(self, sistema: str, usuario: str, max_tokens: int = 700) -> str:
        respuesta = self._crear(
            sistema,
            usuario,
            max_tokens=max_tokens,
            output_config={"effort": self._esfuerzo},
        )
        return self._texto(respuesta)

    def disponible(self) -> bool:
        """Indica si el proveedor responde con estas credenciales.

        Usa `models.list`, que no consume tokens: preguntar si el backend está
        vivo no puede costar plata. Existe por el mismo motivo que en los otros
        adaptadores —`replay` y `demo` cortan temprano— y acá pesa más: un
        entorno mal configurado contra un proveedor pago no falla ruidosamente,
        produce capturas plausibles y vacías después de haber facturado.
        """
        try:
            self._cliente.models.list(limit=1)
            return True
        except Exception:
            return False

    def uso(self) -> Uso:
        """Lo consumido desde que se creó el cliente."""
        return self._uso

    def contar_tokens(
        self, sistema: str, usuario: str, esquema: dict[str, Any] | None = None
    ) -> int:
        """Cuántos tokens de entrada tendría esa llamada. **No cuesta nada.**

        `count_tokens` es un endpoint gratis, y eso lo vuelve la herramienta más
        útil que tiene este adaptador: permite calcular el costo exacto de una
        corrida **antes** de gastar un centavo, con el tokenizador del proveedor
        en vez de una regla de tres sobre caracteres.

        Es la misma disciplina que ya ordena el resto del eval —fijar los
        umbrales antes de medir, predecir antes de correr— aplicada a la
        factura: si el número real sorprende, la sorpresa aparece cuando todavía
        es gratis arreglarla.

        A propósito **no suma a `uso()`**: no se factura, y meterlo ahí inflaría
        el costo reportado de cada corrida con tokens que nadie cobró.
        """
        extra: dict[str, Any] = {}
        if esquema is not None:
            # Se cuenta con el mismo esquema cerrado que se manda de verdad: el
            # esquema viaja en el request y ocupa tokens. Contar sin él daría un
            # número más chico que la llamada real, que es la dirección
            # equivocada del error.
            extra["output_config"] = {
                "format": {"type": "json_schema", "schema": _cerrar(esquema)}
            }
        respuesta = self._cliente.messages.count_tokens(
            model=self.nombre,
            system=sistema,
            messages=[{"role": "user", "content": usuario}],
            **extra,
        )
        return int(respuesta.input_tokens)

    # --- Traducción ----------------------------------------------------------

    def _crear(self, sistema: str, usuario: str, **kwargs: Any) -> Any:
        """Arma la llamada y contabiliza lo que consumió.

        `sistema` va como parámetro `system` y no como un mensaje más del array:
        en `/api/chat` de Ollama el prompt de sistema es un mensaje, en Anthropic
        es un campo aparte. Mandarlo adentro de `messages` no rompería nada
        visible —el modelo lo lee igual y clasifica un poco peor—, que es
        justamente lo que lo vuelve peligroso.

        Y no se manda `temperature`: los modelos Claude actuales la rechazan con
        un 400. Ver el encabezado del módulo.
        """
        respuesta = None
        try:
            respuesta = self._cliente.messages.create(
                model=self.nombre,
                system=sistema,
                messages=[{"role": "user", "content": usuario}],
                **kwargs,
            )
            return respuesta
        finally:
            # En el `finally` a propósito: una respuesta que no cumple el
            # esquema igual se cobra. Un contador que solo suma los éxitos
            # subestima la factura justo en las corridas que salieron mal, que
            # son las que uno repite.
            if respuesta is not None:
                self._contabilizar(respuesta)

    def _contabilizar(self, respuesta: Any) -> None:
        u = getattr(respuesta, "usage", None)
        if u is None:
            return
        self._uso = self._uso + Uso(
            tokens_entrada=getattr(u, "input_tokens", 0) or 0,
            tokens_salida=getattr(u, "output_tokens", 0) or 0,
            tokens_cacheados=getattr(u, "cache_read_input_tokens", 0) or 0,
            llamadas=1,
        )

    @staticmethod
    def _texto(respuesta: Any) -> str:
        """Junta los bloques de texto de la respuesta, y solo los de texto.

        Se concatenan todos porque una respuesta puede venir partida en varios
        bloques: leer solo el primero devolvería un informe truncado a mitad de
        oración, sin error y sin aviso.

        Y se filtra por `type` porque con thinking activo la respuesta trae
        bloques `thinking` antes del texto. Concatenarlos a ciegas metería el
        razonamiento del modelo adentro del informe.
        """
        return "".join(
            b.text for b in respuesta.content if getattr(b, "type", None) == "text"
        )


def hay_credenciales() -> bool:
    """Indica si hay con qué autenticarse, sin construir el cliente.

    Vive suelta y no en la clase por el mismo motivo que `ollama_responde`: la
    pregunta no depende de CÓMO se le hable al modelo. La usan los tests
    marcados `llm` para saltarse solos cuando no hay key, en vez de fallar y
    hacer creer que se rompió el adaptador.
    """
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
