"""Almacenamiento de análisis.

PROVISORIO: guarda en memoria del proceso. Los análisis se pierden al reiniciar
y no se comparten entre workers.

Alcanza para la Fase 1 porque el análisis tarda milisegundos y hay un solo
proceso. Cuando entre el LLM —dos minutos por informe en esta máquina— hará
falta un backend real con Redis y un worker aparte (Fase 6 del roadmap).

Está detrás de una interfaz mínima justamente para que ese cambio sea sustituir
esta clase y nada más. Lo que NO hay que hacer es esparcir accesos a un dict
global por los handlers: eso convierte un reemplazo de veinte líneas en una
refactorización.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

from apps.api.schemas import Analisis

MAX_ANALISIS_EN_MEMORIA = 200


class AlmacenAnalisis:
    """Almacén en memoria con desalojo del más viejo.

    El límite existe para que un proceso de larga vida no crezca sin techo: sin
    él, cada análisis quedaría retenido para siempre y la memoria sería una
    fuga lenta que aparece recién en producción.
    """

    def __init__(self, capacidad: int = MAX_ANALISIS_EN_MEMORIA) -> None:
        self._datos: OrderedDict[str, Analisis] = OrderedDict()
        self._lock = threading.Lock()
        self._capacidad = capacidad

    def guardar(self, analisis: Analisis) -> None:
        with self._lock:
            self._datos[analisis.id] = analisis
            self._datos.move_to_end(analisis.id)
            while len(self._datos) > self._capacidad:
                self._datos.popitem(last=False)

    def obtener(self, id_: str) -> Analisis | None:
        with self._lock:
            return self._datos.get(id_)

    def listar(self, limite: int = 50, offset: int = 0) -> tuple[int, list[Analisis]]:
        with self._lock:
            todos = list(reversed(self._datos.values()))
        return len(todos), todos[offset:offset + limite]

    def eliminar(self, id_: str) -> bool:
        with self._lock:
            return self._datos.pop(id_, None) is not None

    def limpiar(self) -> None:
        with self._lock:
            self._datos.clear()


almacen = AlmacenAnalisis()
