"""Evaluación de la calidad del sistema.

Acá viven el golden set y las métricas con las que se mide al agente. No es
código de producción: nada de `agent/`, `apps/` ni `core/` importa este paquete.

La separación importa por una razón: las métricas leen `dbo.ground_truth`, la
tabla que declara los eventos sembrados a propósito, y el agente tiene `DENY`
explícito sobre ella. Si el evaluador y el evaluado compartieran camino de
acceso, la evaluación dejaría de medir algo.
"""
