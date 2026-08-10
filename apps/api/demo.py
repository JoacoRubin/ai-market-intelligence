"""Recorrido del flujo REST completo, con los códigos de estado a la vista.

Muestra el ciclo de vida de un análisis como recurso: se crea, se consulta, se
sirve en dos representaciones y se elimina. La gracia está en los códigos de
estado — cada uno dice algo distinto y preciso.

Se ejecuta con:  .\\tasks.ps1 api-demo
"""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from apps.api.main import app

if os.name == "nt":
    os.system("")

VERDE, AZUL, GRIS, ROJO, FIN = "\033[92m", "\033[94m", "\033[90m", "\033[91m", "\033[0m"

DESDE, HASTA = "2026-01-01", "2026-03-31"


def paso(metodo: str, ruta: str, codigo: int, nota: str, extra: str = "") -> None:
    color = VERDE if codigo < 400 else ROJO
    print(f"  {AZUL}{metodo:<7}{FIN}{ruta:<38}{color}{codigo}{FIN}  {GRIS}{nota}{FIN}")
    if extra:
        print(f"          {GRIS}{extra}{FIN}")


def main() -> int:
    with TestClient(app) as c:
        print()
        print(f"  {'=' * 76}")
        print("  CICLO DE VIDA DE UN ANÁLISIS COMO RECURSO REST")
        print(f"  {'=' * 76}\n")

        r = c.get("/health")
        paso("GET", "/health", r.status_code, "estado del servicio",
             f"base de datos: {r.json()['base_de_datos']}")

        r = c.get("/products", params={"limite": 200})
        total = r.json()["total"]
        # Se eligen dos productos CON movimiento en el período: comparar contra
        # uno que no vendió nada produce un informe correcto pero vacío, y no
        # muestra nada de lo que el sistema sabe hacer.
        candidatos = [p["id"] for p in r.json()["items"]]
        ids = []
        for pid in candidatos:
            m = c.get(f"/products/{pid}/metrics",
                      params={"desde": DESDE, "hasta": HASTA}).json()
            if m["unidades"] > 0:
                ids.append(pid)
            if len(ids) == 2:
                break
        paso("GET", "/products", r.status_code,
             f"{total} productos en total", f"con ventas en el período: {ids}")

        r = c.get(f"/products/{ids[0]}/metrics",
                  params={"desde": DESDE, "hasta": HASTA})
        m = r.json()
        paso("GET", f"/products/{ids[0]}/metrics", r.status_code,
             "KPIs calculados por SQL",
             f"{m['unidades']} unidades · USD {m['revenue']:,.2f}")

        r = c.get(f"/products/{ids[0]}/metrics",
                  params={"desde": HASTA, "hasta": DESDE})
        paso("GET", "/products/.../metrics (invertido)", r.status_code,
             "entrada inválida, no error del servidor")

        print()
        r = c.post("/analyses", json={
            "product_ids": ids, "desde": DESDE, "hasta": HASTA})
        aid = r.json()["id"]
        paso("POST", "/analyses", r.status_code,
             "ACEPTADO, todavía puede no haber terminado",
             f"Location: {r.headers.get('location')}")

        r = c.get(f"/analyses/{aid}")
        cuerpo = r.json()
        paso("GET", f"/analyses/{aid[:16]}...", r.status_code,
             f"estado: {cuerpo['estado']}",
             f"{len(cuerpo['informe']['resumen_ejecutivo'])} conclusiones, "
             f"{len(cuerpo['informe']['metricas'])} productos analizados")

        print()
        print(f"  {GRIS}Conclusiones derivadas de los KPIs, sin modelo de lenguaje:{FIN}")
        for a in cuerpo["informe"]["resumen_ejecutivo"]:
            print(f"    · {a['texto']}  {GRIS}[{', '.join(a['fuentes'])}]{FIN}")
        for w in cuerpo["informe"]["advertencias"]:
            print(f"    ! {w}")

        print()
        r = c.get(f"/analyses/{aid}", headers={"Accept": "application/pdf"})
        paso("GET", "/analyses/{id}  Accept: pdf", r.status_code,
             "mismo recurso, otra representación",
             f"{len(r.content):,} bytes de PDF")

        r = c.get(f"/analyses/{aid}.pdf")
        paso("GET", "/analyses/{id}.pdf", r.status_code,
             "atajo para enlaces de navegador")

        r = c.get(f"/analyses/{aid}", headers={"Accept": "application/xml"})
        paso("GET", "/analyses/{id}  Accept: xml", r.status_code,
             "existe, pero no en ese formato")

        r = c.get("/analyses/no-existe")
        paso("GET", "/analyses/no-existe", r.status_code, "no hay tal recurso")

        r = c.delete(f"/analyses/{aid}")
        paso("DELETE", "/analyses/{id}", r.status_code, "borrado, sin cuerpo")

        r = c.get(f"/analyses/{aid}")
        paso("GET", "/analyses/{id}", r.status_code, "ya no existe")

        print()
        print(f"  {GRIS}Cada código dice algo distinto: 202 aceptado, 200 acá está,")
        print("  204 hecho y no hay nada que devolver, 404 no existe, 406 existe")
        print(f"  pero no así, 422 tu pedido está mal formado.{FIN}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
