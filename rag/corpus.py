"""Corpus documental sintético.

Cada documento explicativo **describe un evento que el dataset ya contiene**. El
`ground_truth` sabe que P002 tuvo un pico de devoluciones por un lote
defectuoso; acá se genera la comunicación del proveedor que lo reporta.

Esa correspondencia es lo que hace que el RAG aporte algo que SQL no puede dar.
Una consulta muestra QUE las devoluciones se dispararon el 18 de enero. Solo un
documento puede decir POR QUÉ. Un informe que junta las dos cosas es un
análisis; uno que solo tiene números es una tabla con prosa alrededor.

Y como se sabe qué documento explica qué evento, el retrieval queda evaluable:
`Corpus.explicaciones` es el ground truth de recuperación.

**Los distractores no son relleno.** Si todos los documentos fueran relevantes,
cualquier búsqueda acertaría por descarte y la métrica de retrieval mediría
solamente que el índice devuelve algo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

# Tipos de documento que existen solo para ensuciar el espacio de búsqueda.
# Son verosímiles a propósito: un distractor obvio no pone a prueba nada.
ROLES_DISTRACTOR = ("politica", "ficha_producto", "acta_reunion", "newsletter")

PROVEEDORES = ["Textiles del Sur", "Manufactura Andina", "Insumos Ribera",
               "Confecciones Lumen", "Industrias Pampa"]
CANALES_CAMPANIA = ["email", "redes sociales", "display", "buscadores"]
MOTIVOS_ROTURA = [
    "demora del proveedor en la entrega programada",
    "error en la proyección de reposición",
    "un lote rechazado en control de calidad",
]


@dataclass
class Documento:
    id: str
    tipo: str
    titulo: str
    texto: str
    fecha: date
    seccion: str
    product_id: str | None = None


@dataclass
class Explicacion:
    """Vínculo entre un evento del ground truth y el documento que lo explica."""

    evento_idx: int
    doc_id: str
    product_id: str
    tipo_evento: str


@dataclass
class Corpus:
    documentos: list[Documento] = field(default_factory=list)
    explicaciones: list[Explicacion] = field(default_factory=list)

    def por_id(self, doc_id: str) -> Documento | None:
        return next((d for d in self.documentos if d.id == doc_id), None)


def _nombre(productos: pd.DataFrame, pid: str) -> str:
    fila = productos[productos["id"] == pid]
    if fila.empty:
        return pid
    return f"{fila.iloc[0]['brand']} {fila.iloc[0]['category']}"


def _texto_pico_devoluciones(pid: str, nombre: str, fecha: date,
                             proveedor: str, rng) -> str:
    lote = f"L{rng.integers(1000, 9999)}"
    return (
        f"Comunicación recibida del proveedor {proveedor} respecto del artículo "
        f"{pid} ({nombre}).\n\n"
        f"El proveedor informa que el lote {lote}, despachado en la semana previa "
        f"al {fecha.isoformat()}, presentó desvíos en el control de calidad de "
        f"costuras y terminaciones. La inspección posterior detectó que una "
        f"proporción significativa de las unidades de ese lote llegó con defectos "
        f"que no se identificaron en la recepción.\n\n"
        f"Se estima que las unidades afectadas del {pid} ya habían sido "
        f"despachadas a clientes al momento de la detección, lo que explica el "
        f"incremento de devoluciones registrado en los días posteriores. El "
        f"proveedor asume el costo de las unidades devueltas y se compromete a "
        f"reforzar el control de salida.\n\n"
        f"Acción recomendada: revisar el stock remanente del lote {lote} antes de "
        f"continuar la venta del {pid}."
    )


def _texto_caida_ventas(pid: str, nombre: str, fecha: date, dias: int,
                        motivo: str) -> str:
    return (
        f"Reporte de quiebre de stock — artículo {pid} ({nombre})\n\n"
        f"Entre el {fecha.isoformat()} y los {dias} días siguientes, el artículo "
        f"{pid} estuvo sin disponibilidad en el canal principal de venta. La "
        f"causa registrada fue {motivo}.\n\n"
        f"Durante la ventana sin stock, las unidades vendidas del {pid} cayeron "
        f"prácticamente a cero. La caída no refleja una pérdida de demanda: "
        f"refleja imposibilidad de comprar. Los pedidos que llegaron en ese "
        f"período no pudieron cursarse y una parte de esa demanda se perdió "
        f"frente a alternativas del catálogo.\n\n"
        f"Se recomienda no interpretar el descenso del {pid} en ese período como "
        f"una señal de performance comercial."
    )


def _texto_pico_ventas(pid: str, nombre: str, fecha: date, factor: float,
                       canal: str, descuento: int) -> str:
    return (
        f"Cierre de acción promocional — artículo {pid} ({nombre})\n\n"
        f"La acción ejecutada el {fecha.isoformat()} sobre el artículo {pid} "
        f"combinó un descuento del {descuento}% con pauta concentrada en "
        f"{canal}. El objetivo era liquidar stock acumulado antes del cambio de "
        f"temporada.\n\n"
        f"El resultado superó la proyección: la demanda diaria del {pid} se "
        f"multiplicó aproximadamente por {factor:.0f} respecto de su nivel "
        f"habitual. El pico se concentró en la jornada de la acción y no se "
        f"sostuvo en los días posteriores, lo que sugiere adelantamiento de "
        f"compra más que crecimiento de base.\n\n"
        f"Conviene considerar este efecto al proyectar la demanda del {pid} para "
        f"el período siguiente."
    )


def _distractores(productos: pd.DataFrame, rng, desde: date) -> list[Documento]:
    docs: list[Documento] = []

    docs.append(Documento(
        id="doc_pol_devoluciones", tipo="politica",
        titulo="Política de devoluciones y cambios",
        seccion="§1.1", fecha=desde,
        texto=(
            "La política vigente permite la devolución sin costo de cualquier "
            "artículo dentro de los 30 días corridos desde la fecha de compra, "
            "siempre que conserve su empaque original y no presente signos de "
            "uso.\n\nLas devoluciones por defecto de fabricación no tienen plazo "
            "acotado y se gestionan directamente con el proveedor. Los cambios "
            "por talle se aceptan sin límite de plazo mientras haya stock "
            "disponible del artículo equivalente.\n\nLos reintegros se procesan "
            "por el mismo medio de pago utilizado en la compra original, dentro "
            "de los diez días hábiles posteriores a la recepción del artículo."
        ),
    ))

    docs.append(Documento(
        id="doc_pol_garantia", tipo="politica",
        titulo="Garantía de productos y cobertura",
        seccion="§2.1", fecha=desde + timedelta(days=15),
        texto=(
            "Todos los artículos del catálogo cuentan con garantía de seis meses "
            "por defectos de fabricación, contados desde la fecha de entrega al "
            "cliente final.\n\nLa garantía no cubre el desgaste por uso normal, "
            "los daños por uso indebido ni las modificaciones realizadas por "
            "terceros. Las reparaciones se realizan en los talleres autorizados "
            "y el plazo estimado es de quince días hábiles.\n\nEn caso de que la "
            "reparación no sea viable, se ofrece el reemplazo por un artículo "
            "equivalente o la devolución del importe abonado."
        ),
    ))

    docs.append(Documento(
        id="doc_pol_precios", tipo="politica",
        titulo="Criterios de fijación de precios y descuentos",
        seccion="§3.4", fecha=desde + timedelta(days=40),
        texto=(
            "Los precios de lista se revisan trimestralmente considerando la "
            "evolución de costos del proveedor, la posición competitiva del "
            "artículo y el nivel de rotación observado.\n\nLos descuentos "
            "promocionales requieren aprobación cuando superan el 20% del precio "
            "de lista, dado el impacto directo sobre el margen. Se recomienda "
            "evaluar el margen resultante antes de definir la profundidad del "
            "descuento, y no únicamente el volumen incremental esperado.\n\nLas "
            "acciones sobre artículos de baja rotación pueden autorizarse con "
            "criterios más flexibles."
        ),
    ))

    # Fichas de producto: describen el artículo sin explicar ningún evento.
    muestra = productos.sample(n=min(10, len(productos)),
                               random_state=int(rng.integers(0, 10_000)))
    for _, p in muestra.iterrows():
        docs.append(Documento(
            id=f"doc_ficha_{p['id']}", tipo="ficha_producto",
            titulo=f"Ficha técnica — {p['brand']} {p['category']} ({p['id']})",
            seccion="§1.1", fecha=p["launch_date"], product_id=p["id"],
            texto=(
                f"Artículo {p['id']} de la línea {p['brand']}, categoría "
                f"{p['category']}. Incorporado al catálogo el "
                f"{p['launch_date'].isoformat()}.\n\n"
                f"El artículo se posiciona en el segmento medio del catálogo y "
                f"comparte proveedor con el resto de la línea {p['brand']}. Las "
                f"especificaciones de materiales, cuidados y equivalencias de "
                f"talle se detallan en la documentación del fabricante.\n\n"
                f"La disponibilidad habitual se mantiene en el canal principal y "
                f"la reposición sigue el ciclo semanal estándar del catálogo."
            ),
        ))

    # Actas y newsletters: contexto operativo genérico, semánticamente cercano.
    for i in range(6):
        f = desde + timedelta(days=int(rng.integers(0, 400)))
        docs.append(Documento(
            id=f"doc_acta_{i:02d}", tipo="acta_reunion",
            titulo=f"Acta de reunión comercial — {f.isoformat()}",
            seccion="§2.3", fecha=f,
            texto=(
                f"Reunión del equipo comercial del {f.isoformat()}.\n\n"
                "Se repasó la evolución general del catálogo y el avance del "
                "plan trimestral. El equipo destacó la necesidad de anticipar "
                "las compras de temporada y de mejorar la coordinación con "
                "logística para reducir los tiempos de reposición.\n\n"
                "Se acordó revisar el criterio de asignación de pauta entre "
                "categorías y presentar una propuesta en la próxima reunión. "
                "No se tomaron decisiones sobre precios ni sobre el mix de "
                "productos en esta instancia."
            ),
        ))

    for i in range(4):
        f = desde + timedelta(days=int(rng.integers(0, 400)))
        docs.append(Documento(
            id=f"doc_news_{i:02d}", tipo="newsletter",
            titulo=f"Novedades del sector — {f.isoformat()}",
            seccion="§1.2", fecha=f,
            texto=(
                f"Resumen sectorial del {f.isoformat()}.\n\n"
                "El sector mantuvo un nivel de actividad estable durante el "
                "período, con variaciones moderadas entre categorías. Los "
                "operadores del rubro señalan presión sobre los costos de "
                "insumos importados y tiempos de reposición más largos que en "
                "el ciclo anterior.\n\n"
                "En el canal digital continúa la tendencia hacia compras de "
                "menor ticket y mayor frecuencia. No se registran cambios "
                "regulatorios relevantes para la operación."
            ),
        ))

    return docs


def generar_corpus(dataset: dict[str, pd.DataFrame], seed: int = 42) -> Corpus:
    """Genera el corpus documental a partir del dataset y su ground truth."""
    rng = np.random.default_rng(seed)
    productos = dataset["products"]
    gt = dataset["ground_truth"]

    corpus = Corpus()
    fecha_min = min(dataset["orders"]["created_at"]).date()

    for idx, evento in gt.iterrows():
        pid = evento["product_id"]
        nombre = _nombre(productos, pid)
        # El documento se fecha cerca del evento: un reporte de dos meses
        # después no lo explica, lo recuerda.
        fecha_doc = evento["fecha"] + timedelta(days=int(rng.integers(1, 8)))

        if evento["tipo"] == "pico_devoluciones":
            doc = Documento(
                id=f"doc_prov_{idx:03d}", tipo="comunicacion_proveedor",
                titulo=f"Reporte de calidad de lote — {pid}",
                seccion="§1.1", fecha=fecha_doc, product_id=pid,
                texto=_texto_pico_devoluciones(
                    pid, nombre, evento["fecha"],
                    str(rng.choice(PROVEEDORES)), rng),
            )
        elif evento["tipo"] == "caida_ventas":
            doc = Documento(
                id=f"doc_stock_{idx:03d}", tipo="reporte_operativo",
                titulo=f"Quiebre de stock — {pid}",
                seccion="§2.2", fecha=fecha_doc, product_id=pid,
                texto=_texto_caida_ventas(
                    pid, nombre, evento["fecha"], int(evento["magnitud"]),
                    str(rng.choice(MOTIVOS_ROTURA))),
            )
        else:  # pico_ventas
            doc = Documento(
                id=f"doc_promo_{idx:03d}", tipo="reporte_campania",
                titulo=f"Cierre de acción promocional — {pid}",
                seccion="§3.2", fecha=fecha_doc, product_id=pid,
                texto=_texto_pico_ventas(
                    pid, nombre, evento["fecha"], float(evento["magnitud"]),
                    str(rng.choice(CANALES_CAMPANIA)),
                    int(rng.choice([15, 20, 25, 30]))),
            )

        corpus.documentos.append(doc)
        corpus.explicaciones.append(Explicacion(
            evento_idx=int(idx), doc_id=doc.id, product_id=pid,
            tipo_evento=str(evento["tipo"]),
        ))

    corpus.documentos.extend(_distractores(productos, rng, fecha_min))
    return corpus
