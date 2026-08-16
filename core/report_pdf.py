"""Renderer PDF del informe.

Este módulo **no calcula nada**. Toma un `Report` ya validado y lo dibuja. Si
necesitara sumar dos columnas o derivar un porcentaje, dejaría de haber una
única fuente de verdad: el PDF empezaría a decir cosas que la API no dice, y
tarde o temprano las dos versiones se contradirían.

Todo número que aparece en el PDF sale tal cual del modelo. Hay un test que lo
verifica extrayendo el texto del PDF generado.

Elección de ReportLab: WeasyPrint sería más lindo (HTML/CSS a PDF) pero depende
de GTK/Pango nativos que no vienen en el wheel y no están en Windows. Verificado
en la máquina, no supuesto — no hay ADR para esto porque la razón entra en dos
renglones y es la que está acá arriba.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import partial
from pathlib import Path
from typing import Any, BinaryIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from core.report import Afirmacion, Report

TINTA = colors.HexColor("#1f2937")
SUAVE = colors.HexColor("#6b7280")
BORDE = colors.HexColor("#d1d5db")
FONDO_AVISO = colors.HexColor("#fef3c7")
ACENTO = colors.HexColor("#1d4ed8")


def _estilos() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "titulo": ParagraphStyle(
            "titulo", parent=base["Title"], fontSize=20, spaceAfter=2 * mm,
            textColor=TINTA,
        ),
        "subtitulo": ParagraphStyle(
            "subtitulo", parent=base["Normal"], fontSize=9.5, textColor=SUAVE,
            spaceAfter=5 * mm,
        ),
        "seccion": ParagraphStyle(
            "seccion", parent=base["Heading2"], fontSize=13, textColor=TINTA,
            spaceBefore=6 * mm, spaceAfter=2.5 * mm,
        ),
        "cuerpo": ParagraphStyle(
            "cuerpo", parent=base["Normal"], fontSize=9.5, leading=14,
            alignment=TA_JUSTIFY, spaceAfter=1.8 * mm,
        ),
        "nota": ParagraphStyle(
            "nota", parent=base["Normal"], fontSize=8, textColor=SUAVE,
            leading=11, spaceAfter=2 * mm,
        ),
        "aviso": ParagraphStyle(
            "aviso", parent=base["Normal"], fontSize=8.5, leading=12,
            textColor=TINTA,
        ),
    }


def _pie_de_pagina(canvas: Any, doc: Any, informe: Report) -> None:
    """Se dibuja en TODAS las páginas.

    Un PDF se parte: alguien imprime y reparte hojas sueltas en una reunión.
    Cada hoja tiene que seguir diciendo que la generó un sistema de IA, con qué
    modelo y cuándo. Un disclaimer solo en la portada desaparece con la portada.
    """
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(SUAVE)

    fecha = informe.generado_en.strftime("%d/%m/%Y %H:%M")
    izquierda = (
        f"Generado por IA · modelo {informe.modelo_llm} · {fecha} · "
        f"solicitud {informe.request_id}"
    )
    canvas.drawString(18 * mm, 12 * mm, izquierda)
    canvas.drawRightString(A4[0] - 18 * mm, 12 * mm, f"Página {doc.page}")

    canvas.setStrokeColor(BORDE)
    canvas.setLineWidth(0.4)
    canvas.line(18 * mm, 15 * mm, A4[0] - 18 * mm, 15 * mm)
    canvas.restoreState()


def _tabla(datos: list[list[Any]], anchos: list[float],
           alinear_derecha: bool = True) -> Table:
    t = Table(datos, colWidths=anchos, repeatRows=1)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), TINTA),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
    ]
    if alinear_derecha:
        estilo.append(("ALIGN", (1, 1), (-1, -1), "RIGHT"))
    t.setStyle(TableStyle(estilo))
    return t


def _num(valor: float, decimales: int = 1) -> str:
    """Formato español: punto de miles, coma decimal. Sin redondeos creativos."""
    texto = f"{valor:,.{decimales}f}"
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _afirmaciones(bloque: Iterable[Afirmacion], est: dict[str, Any],
                  mostrar_fuentes: bool = True) -> list[Any]:
    salida = []
    for a in bloque:
        texto = a.texto
        if mostrar_fuentes and a.fuentes:
            texto += f'  <font size="7" color="#6b7280">[{", ".join(a.fuentes)}]</font>'
        salida.append(Paragraph(f"• {texto}", est["cuerpo"]))
    return salida


def render_pdf(informe: Report, destino: str | Path | BinaryIO) -> Path | BinaryIO:
    """Renderiza el informe a PDF.

    `destino` puede ser una ruta o cualquier objeto con `write`. La API sirve
    los bytes directamente desde memoria: escribir un archivo temporal para
    después leerlo y borrarlo es trabajo de disco que nadie pidió, y deja basura
    cuando algo falla en el medio.
    """
    # Se resuelve a una variable nueva en vez de reasignar `destino`: al
    # pisarla, el tipo declarado sigue incluyendo `str` y todo lo que venga
    # después tiene que volver a demostrar que ya no puede serlo.
    if isinstance(destino, str | Path):
        ruta = Path(destino)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        salida: Path | BinaryIO = ruta
    else:
        salida = destino
    es_buffer = not isinstance(salida, Path)
    est = _estilos()

    doc = SimpleDocTemplate(
        salida if es_buffer else str(salida), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=22 * mm,
        title=f"Informe {informe.request_id}",
        author=f"AI Market Intelligence · {informe.modelo_llm}",
        subject=informe.consulta,
    )

    hist: list[Any] = []
    ancho = doc.width

    # --- encabezado --------------------------------------------------------
    hist.append(Paragraph("Informe ejecutivo", est["titulo"]))
    hist.append(Paragraph(
        f"<b>Consulta:</b> {informe.consulta}", est["subtitulo"]))

    aviso = Table(
        [[Paragraph(
            "<b>Documento generado por un sistema de IA.</b> Las métricas provienen "
            "de consultas a base de datos y modelos estadísticos; el texto fue "
            "redactado automáticamente. Revisar antes de tomar decisiones.",
            est["aviso"])]],
        colWidths=[ancho],
    )
    aviso.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), FONDO_AVISO),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#f59e0b")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    hist.append(aviso)
    hist.append(Spacer(1, 5 * mm))

    # --- resumen ejecutivo -------------------------------------------------
    if informe.resumen_ejecutivo:
        hist.append(Paragraph("Resumen ejecutivo", est["seccion"]))
        hist.extend(_afirmaciones(informe.resumen_ejecutivo, est))

    # --- performance -------------------------------------------------------
    if informe.metricas:
        hist.append(Paragraph("Performance", est["seccion"]))
        filas = [["Producto", "Unidades", "Revenue", "Margen", "Crecim.", "Devol."]]
        for m in informe.metricas:
            filas.append([
                f"{m.nombre} ({m.product_id})",
                _num(m.unidades, 0),
                f"USD {_num(m.revenue, 2)}",
                "—" if m.margen_pct is None else f"{_num(m.margen_pct)}%",
                "—" if m.crecimiento_pct is None else f"{_num(m.crecimiento_pct)}%",
                "—" if m.tasa_devolucion_pct is None
                else f"{_num(m.tasa_devolucion_pct)}%",
            ])
        anchos = [ancho * f for f in (0.30, 0.14, 0.20, 0.12, 0.12, 0.12)]
        hist.append(_tabla(filas, anchos))
        hist.append(Paragraph(
            "Valores calculados por consulta SQL sobre la base transaccional. "
            "No fueron generados por el modelo de lenguaje.", est["nota"]))

    # --- predicciones ------------------------------------------------------
    if informe.predicciones:
        hist.append(Paragraph("Predicciones", est["seccion"]))
        filas = [["Producto", "Horizonte", "Predicción", "MAPE", "Baseline", "Modelo"]]
        for p in informe.predicciones:
            filas.append([
                p.product_id,
                f"{p.horizonte_dias} días",
                _num(p.valor, 0),
                "—" if p.mape_backtest is None else f"{_num(p.mape_backtest)}%",
                "—" if p.mape_baseline is None else f"{_num(p.mape_baseline)}%",
                p.modelo_version or "—",
            ])
        anchos = [ancho * f for f in (0.16, 0.16, 0.18, 0.14, 0.16, 0.20)]
        hist.append(_tabla(filas, anchos))
        hist.append(Paragraph(
            "Los valores de esta sección son <b>predicciones</b>, no hechos "
            "observados. MAPE es el error porcentual medio del modelo medido "
            "en backtesting: cuanto más bajo, mejor. Se compara contra un "
            "baseline naïve (repetir el último valor conocido); si el modelo no "
            "lo supera, no aporta valor.", est["nota"]))

    # --- anomalías ---------------------------------------------------------
    if informe.anomalias:
        hist.append(Paragraph("Anomalías detectadas", est["seccion"]))
        for a in informe.anomalias:
            evidencia = (
                f'  <font size="7" color="#6b7280">[{", ".join(a.evidencia)}]</font>'
                if a.evidencia else
                '  <font size="7" color="#6b7280">[sin evidencia asociada]</font>'
            )
            hist.append(Paragraph(
                f"• <b>{a.product_id}</b> · {a.fecha.strftime('%d/%m/%Y')} · "
                f"{a.tipo} · {_num(a.desvios)} desvíos<br/>{a.descripcion}{evidencia}",
                est["cuerpo"]))

    # --- contexto de mercado -----------------------------------------------
    if informe.contexto_mercado:
        hist.append(Paragraph("Contexto de mercado", est["seccion"]))
        hist.extend(_afirmaciones(informe.contexto_mercado, est))

    # --- recomendaciones ---------------------------------------------------
    if informe.recomendaciones:
        hist.append(Paragraph("Recomendaciones", est["seccion"]))
        hist.append(Paragraph(
            "Las recomendaciones son cursos de acción sugeridos a partir del "
            "análisis. <b>No son hechos verificados</b> y requieren criterio "
            "humano antes de ejecutarse.", est["nota"]))
        hist.extend(_afirmaciones(informe.recomendaciones, est,
                                  mostrar_fuentes=False))

    # --- advertencias y limitaciones ---------------------------------------
    if informe.advertencias or informe.limitaciones:
        bloque = [Paragraph("Advertencias y limitaciones", est["seccion"])]
        for w in informe.advertencias:
            bloque.append(Paragraph(f"• {w}", est["cuerpo"]))
        for lim in informe.limitaciones:
            bloque.append(Paragraph(f"• {lim}", est["cuerpo"]))
        hist.append(KeepTogether(bloque))

    # --- fuentes y trazabilidad --------------------------------------------
    hist.append(PageBreak())
    hist.append(Paragraph("Fuentes", est["seccion"]))
    if informe.fuentes:
        filas = [["ID", "Tipo", "Referencia", "Consultada"]]
        for f in informe.fuentes:
            ref = f.referencia + (f" · {f.seccion}" if f.seccion else "")
            filas.append([
                Paragraph(f'<font size="8">{f.id}</font>', est["aviso"]),
                f.tipo,
                Paragraph(f'<font size="8">{ref}</font>', est["aviso"]),
                f.consultada_en.strftime("%d/%m/%Y %H:%M"),
            ])
        anchos = [ancho * fr for fr in (0.24, 0.16, 0.38, 0.22)]
        hist.append(_tabla(filas, anchos, alinear_derecha=False))
    else:
        hist.append(Paragraph("Sin fuentes declaradas.", est["cuerpo"]))

    if informe.trace:
        hist.append(Paragraph("Cómo se obtuvo", est["seccion"]))
        filas = [["Etapa", "Herramienta", "Duración (ms)"]]
        for paso in informe.trace:
            filas.append([paso.nodo, paso.tool or "—", _num(paso.duracion_ms, 0)])
        filas.append(["Total", "", _num(informe.duracion_total_ms, 0)])
        anchos = [ancho * fr for fr in (0.40, 0.35, 0.25)]
        tabla = _tabla(filas, anchos)
        tabla.setStyle(TableStyle([
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f3f4f6")),
        ]))
        hist.append(tabla)

    dibujar = partial(_pie_de_pagina, informe=informe)
    doc.build(hist, onFirstPage=dibujar, onLaterPages=dibujar)
    return salida
