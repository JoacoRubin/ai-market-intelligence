"""Verificación previa a capturar.

Existe por un fallo concreto: con Docker Desktop apagado, `tasks.ps1 replay`
arrancó igual. Ollama respondía, así que el único chequeo que había pasó, y el
runner se puso a gastar minutos de CPU produciendo capturas que iban a salir sin
un solo dato.

El problema de fondo es que un entorno incompleto **no produce un error**:
produce ejecuciones plausibles y vacías. El agente conecta, no encuentra nada,
replanifica dos veces, se rinde y redacta un informe correcto sobre la nada. Eso
se ve bien, tarda lo mismo, y no dice absolutamente nada.

Por eso la comprobación corre antes y corta, y por eso informa TODOS los
problemas juntos: arreglar de a uno obliga a esperar la conexión otra vez.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class _ClienteConSalud(Protocol):
    def disponible(self) -> bool: ...


def contar_productos() -> int:
    """Sonda real: lee por el mismo camino que usan las tools del agente.

    No alcanza con preguntar si el servidor responde. Hay cuatro formas de que
    la captura salga vacía —servidor apagado, esquema sin crear, usuario
    read-only inexistente, base sin sembrar— y un SELECT por la conexión del
    agente las cubre las cuatro de una sola vez.
    """
    from core.db import cursor_lectura

    with cursor_lectura() as cur:
        return int(cur.execute("SELECT COUNT(*) FROM dbo.products").fetchone()[0])


def problemas_de_entorno(
    cliente: _ClienteConSalud,
    sonda_base: Callable[[], int] = contar_productos,
) -> list[str]:
    """Devuelve los motivos por los que NO se puede capturar. Vacía si se puede.

    Cada mensaje trae el comando que lo arregla: un diagnóstico sin la acción
    que lo corrige deja a la persona exactamente donde estaba.
    """
    problemas: list[str] = []

    if not cliente.disponible():
        problemas.append(
            "Ollama no responde. Levantalo con 'ollama serve' y verificá que el "
            "modelo esté descargado."
        )

    try:
        productos = sonda_base()
    except Exception as e:
        problemas.append(
            f"SQL Server no responde por la conexión de lectura ({type(e).__name__}). "
            "Levantá Docker Desktop y corré '.\\tasks.ps1 db-up'. Si es la primera "
            "vez, después '.\\tasks.ps1 db-init'."
        )
    else:
        if productos == 0:
            problemas.append(
                "La base responde pero está sin datos: 0 productos. Sembrala con "
                "'.\\tasks.ps1 seed'. Capturar contra una base vacía produce "
                "informes correctos sobre nada."
            )

    return problemas
