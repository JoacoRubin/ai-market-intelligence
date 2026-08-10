"""Tests de la validación semántica de comparaciones.

Motivo concreto: en una corrida real del grafo, `llama3.2:3b` escribió

    "Ribera accesorios lidera en unidades con 242, frente a las 257 de Calma"

242 no lidera sobre 257. El `ReportValidator` la aprobó —y con razón— porque
ambos números salieron de SQL: la groundedness numérica era perfecta y la
afirmación falsa.

Es el modo de falla peligroso de un LLM en informes. No dice disparates: dice
cosas plausibles, bien redactadas y con datos reales, que están mal.

**El riesgo del remedio.** Un validador ingenuo que exija "el primer número
siempre mayor" borraría afirmaciones verdaderas como:

    "Calma tiene una tasa más baja con 3,7%, frente a las 7,1% de Ribera"

Ahí la relación esperada es la inversa, porque el comparativo es de
inferioridad. Un validador que vacía informes correctos es tan inútil como uno
que deja pasar mentiras.

Por eso la regla es: **ante la duda, NO se rechaza**. Solo se elimina cuando la
contradicción es inequívoca.
"""

import pytest

from agent.nodes.comparaciones import Veredicto, verificar_comparacion

# --- Comparativos de superioridad -------------------------------------------

@pytest.mark.parametrize("texto", [
    "Alfa lidera en unidades con 1.500, frente a las 900 de Beta",
    "Beta alcanza mayor ingreso con 71.155, superando los 35.517 de Alfa",
    "Alfa supera a Beta con 250 unidades contra 180",
    "Alfa está por encima con 45,2% frente al 30,1% de Beta",
])
def test_acepta_comparaciones_de_superioridad_correctas(texto):
    assert verificar_comparacion(texto) is Veredicto.CORRECTA


@pytest.mark.parametrize("texto", [
    "Ribera lidera en unidades con 242, frente a las 257 de Calma",
    "Beta supera a Alfa con 100 unidades contra 500",
    "Alfa alcanza mayor ingreso con 12.000, superando los 90.000 de Beta",
])
def test_rechaza_comparaciones_de_superioridad_invertidas(texto):
    """El caso que motivó todo este módulo."""
    assert verificar_comparacion(texto) is Veredicto.CONTRADICTORIA


# --- Comparativos de inferioridad -------------------------------------------

@pytest.mark.parametrize("texto", [
    "Calma tiene una tasa más baja con 3,7%, frente a las 7,1% de Ribera",
    "Alfa registra menor devolución con 2,1% contra 5,7% de Beta",
    "Beta queda por debajo con 900 unidades frente a las 1.500 de Alfa",
])
def test_acepta_comparaciones_de_inferioridad_correctas(texto):
    """La trampa del validador ingenuo.

    Con "más baja" la relación esperada se invierte. Exigir siempre que el
    primer número sea mayor eliminaría afirmaciones verdaderas.
    """
    assert verificar_comparacion(texto) is Veredicto.CORRECTA


@pytest.mark.parametrize("texto", [
    "Calma tiene una tasa más baja con 7,1%, frente a las 3,7% de Ribera",
    "Alfa registra menor devolución con 8,9% contra 2,1% de Beta",
])
def test_rechaza_comparaciones_de_inferioridad_invertidas(texto):
    assert verificar_comparacion(texto) is Veredicto.CONTRADICTORIA


# --- Ante la duda, no se rechaza --------------------------------------------

@pytest.mark.parametrize("texto", [
    "Alfa vendió 1.243 unidades en el período",
    "El margen de Beta fue de 24,8%",
    "La tendencia del trimestre resultó favorable",
])
def test_una_afirmacion_sin_comparacion_no_se_evalua(texto):
    assert verificar_comparacion(texto) is Veredicto.NO_APLICA


def test_una_comparacion_sin_dos_numeros_no_se_evalua():
    """Sin dos cifras que comparar no hay relación que verificar."""
    assert verificar_comparacion(
        "Alfa lidera cómodamente en unidades sobre Beta"
    ) is Veredicto.NO_APLICA


def test_con_mas_de_dos_numeros_no_se_evalua():
    """Con tres o más cifras no se sabe cuáles se están comparando.

    Adivinar acá produciría falsos positivos, y un falso positivo borra una
    afirmación verdadera del informe.
    """
    assert verificar_comparacion(
        "Alfa lidera con 500 unidades y 30,2% de margen, frente a 400 de Beta"
    ) is Veredicto.NO_APLICA


def test_numeros_iguales_no_contradicen_un_comparativo_debil():
    """Si los dos números son iguales, la afirmación es discutible pero no es
    una contradicción aritmética. No se rechaza."""
    assert verificar_comparacion(
        "Alfa lidera con 250, frente a 250 de Beta"
    ) is not Veredicto.CORRECTA


def test_ignora_identificadores_al_contar_numeros():
    """`P002` y `doc_112` no son cifras comparables.

    Es el mismo falso positivo que tenía el auditor prototipo, ahora en otro
    lugar: si se contaran, esta afirmación tendría cuatro números y no se
    evaluaría nunca.
    """
    assert verificar_comparacion(
        "El P002 lidera en unidades con 1.500, frente a las 900 del P003"
    ) is Veredicto.CORRECTA


# --- Robustez ----------------------------------------------------------------

@pytest.mark.parametrize("texto", ["", "   ", "lidera frente a"])
def test_no_explota_con_texto_degenerado(texto):
    assert isinstance(verificar_comparacion(texto), Veredicto)


def test_maneja_negativos():
    assert verificar_comparacion(
        "Alfa crece 18,4% mientras que Beta queda por debajo con -3,1%"
    ) is not Veredicto.CONTRADICTORIA
