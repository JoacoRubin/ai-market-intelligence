"""Tests de `ml/forecast.py::pronosticar` — el punto donde el modelo le
responde de verdad al usuario. `test_ml_backtest.py` mide la CALIDAD del
modelo contra baselines; este archivo protege el CAMINO EN VIVO, que hasta
ahora no tenía test propio: `pronosticar()` entrena un modelo nuevo sobre TODA
la serie para el pronóstico real, distinto de los modelos que el backtest
entrena por ventana — pueden tener estabilidad distinta, y solo uno de los dos
estaba bajo test.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from ml.forecast import _proyeccion_es_estable, pronosticar

DESDE, HASTA = date(2026, 1, 1), date(2026, 6, 30)


def _serie_con_tendencia(dias: int = 120, pendiente: float = 0.4) -> np.ndarray:
    rng = np.random.default_rng(7)
    return np.maximum(0.0, 10 + pendiente * np.arange(dias) + rng.normal(0, 2, dias))


class TestProyeccionEsEstable:
    """El guard en aislamiento — arrays armados a mano, sin depender de que
    Ridge reproduzca la inestabilidad (eso lo prueba la clase de abajo,
    contra datos con la forma real de P001)."""

    def test_una_proyeccion_acotada_por_la_historia_es_estable(self) -> None:
        historia = np.array([3.0, 5.0, 2.0, 4.0, 6.0, 3.0, 5.0])
        proyeccion = np.array([4.0, 5.0, 6.0, 5.0, 4.0])
        assert _proyeccion_es_estable(proyeccion, historia)

    def test_una_proyeccion_que_crece_muy_por_encima_del_maximo_historico_no_lo_es(
        self,
    ) -> None:
        # El caso real: P001 nunca vendió más de 10/día y la recursión
        # terminó proyectando 128 — 12,8x el máximo observado.
        historia = np.array([0.0, 0.0, 3.0, 10.0, 6.0, 9.0, 4.0])
        proyeccion = np.array(
            [18.0, 23.5, 32.2, 30.8, 25.3, 16.5, 14.6, 34.8, 60.2, 94.4, 119.2, 128.3]
        )
        assert not _proyeccion_es_estable(proyeccion, historia)

    def test_una_historia_toda_en_cero_no_rompe_el_calculo(self) -> None:
        # max(historia)=0 no puede ser el piso del techo, o cualquier
        # predicción positiva "diverge" — hace falta un piso mínimo.
        historia = np.zeros(10)
        assert _proyeccion_es_estable(np.array([0.0, 1.0, 0.0]), historia)


class TestPronosticar:
    def test_una_serie_con_tendencia_clara_no_cae_al_baseline(self) -> None:
        serie = _serie_con_tendencia()
        r = pronosticar("P_TEST", serie, horizonte=14, desde=DESDE, hasta=HASTA, registrar=False)
        assert not r.uso_baseline
        assert r.valor > 0

    def test_una_proyeccion_que_diverge_cae_al_baseline_aunque_el_backtest_no_lo_note(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reproduce el bug de P001 sin depender de que Ridge lo reproduzca:
        se fuerza `_predecir_recursivo` a devolver la proyección real que
        explotó, y se verifica que `pronosticar()` no la deje pasar. Antes de
        este fix, `usa_baseline` dependía SOLO de si el backtest superaba al
        naive — un modelo entrenado con toda la serie puede diverger aunque
        los modelos del backtest (entrenados con menos datos, en otras
        ventanas) no lo hayan hecho."""
        import ml.forecast as forecast_mod

        historia = np.array([0.0] * 500 + [3.0, 10.0, 6.0, 9.0, 4.0, 2.0])
        divergente = np.array(
            [18.0, 23.5, 32.2, 30.8, 25.3, 16.5, 14.6, 34.8, 60.2, 94.4, 119.2, 128.3, 117.9, 101.8]
        )
        monkeypatch.setattr(forecast_mod, "_predecir_recursivo", lambda *a, **kw: divergente)

        r = pronosticar("P001", historia, horizonte=14, desde=DESDE, hasta=HASTA, registrar=False)

        assert r.uso_baseline
        # El valor servido tiene que quedar del lado de lo que la historia
        # real permite, no de la proyección descartada.
        assert r.valor < float(np.sum(divergente))

    def test_una_serie_demasiado_corta_sigue_devolviendo_el_baseline(self) -> None:
        serie = np.array([1.0, 2.0, 3.0])
        r = pronosticar("P_CORTO", serie, horizonte=7, desde=DESDE, hasta=HASTA, registrar=False)
        assert r.uso_baseline
        assert r.valor >= 0
