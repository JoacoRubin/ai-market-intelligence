"""Tests de la verificación previa a capturar.

Capturar contra un entorno incompleto no produce un error: produce cinco
ejecuciones plausibles y vacías, que tardan minutos en generarse y que alguien
podría publicar sin mirar. Por eso la comprobación va ANTES y corta.

La regla: un requisito que se imprime en pantalla y no se comprueba no es un
requisito, es una decoración.
"""

from __future__ import annotations

from replay.entorno import problemas_de_entorno


class _ClienteVivo:
    nombre = "llama3.2:3b"

    def disponible(self) -> bool:
        return True


class _ClienteCaido:
    nombre = "llama3.2:3b"

    def disponible(self) -> bool:
        return False


def _base_con(n: int):
    return lambda: n


def _base_caida():
    def sonda() -> int:
        raise RuntimeError("no se pudo conectar")
    return sonda


def test_sin_problemas_devuelve_lista_vacia():
    assert problemas_de_entorno(_ClienteVivo(), _base_con(120)) == []


def test_detecta_ollama_caido():
    problemas = problemas_de_entorno(_ClienteCaido(), _base_con(120))

    assert len(problemas) == 1
    assert "Ollama" in problemas[0]


def test_detecta_la_base_inaccesible():
    problemas = problemas_de_entorno(_ClienteVivo(), _base_caida())

    assert len(problemas) == 1
    assert "SQL Server" in problemas[0]
    assert "db-up" in problemas[0]


def test_detecta_la_base_vacia():
    """Una base levantada pero sin sembrar es el caso más traicionero.

    Conecta, responde, no falla — y el agente produce informes correctos sobre
    cero unidades vendidas. Se ven bien y no dicen nada.
    """
    problemas = problemas_de_entorno(_ClienteVivo(), _base_con(0))

    assert len(problemas) == 1
    assert "sin datos" in problemas[0]
    assert "seed" in problemas[0]


def test_informa_todos_los_problemas_juntos():
    """Arreglar de a uno obliga a esperar la conexión de nuevo cada vez."""
    problemas = problemas_de_entorno(_ClienteCaido(), _base_caida())

    assert len(problemas) == 2
    assert any("Ollama" in p for p in problemas)
    assert any("SQL Server" in p for p in problemas)


def test_los_mensajes_dicen_que_comando_lo_arregla():
    """Un diagnóstico sin la acción que lo corrige deja a la persona igual."""
    for problemas in (
        problemas_de_entorno(_ClienteCaido(), _base_con(120)),
        problemas_de_entorno(_ClienteVivo(), _base_caida()),
        problemas_de_entorno(_ClienteVivo(), _base_con(0)),
    ):
        assert problemas
        assert any(c in problemas[0] for c in ("tasks.ps1", "ollama")), problemas[0]
