"""Demostración en vivo de los guardrails de seguridad.

Los tests prueban que estas defensas funcionan. Esto las muestra funcionando,
que es distinto: sirve para la demo de portfolio y para entender de un vistazo
qué está protegido y por quién.

Se ejecuta con:  .\\tasks.ps1 demo
"""

from __future__ import annotations

import os
import sys

import pyodbc

from core.db import conectar_lectura, driver_disponible

# La consola clásica de Windows no interpreta secuencias ANSI hasta que se
# habilita el modo VT. `os.system("")` lo activa como efecto colateral. Sin
# esto, el usuario ve los códigos de escape crudos en pantalla.
if os.name == "nt":
    os.system("")

_COLOR = sys.stdout.isatty() or os.name == "nt"


def _c(codigo: str) -> str:
    return codigo if _COLOR else ""


VERDE = _c("\033[92m")
ROJO = _c("\033[91m")
AMARILLO = _c("\033[93m")
GRIS = _c("\033[90m")
FIN = _c("\033[0m")

# (descripción, SQL, ¿debería funcionar?)
INTENTOS: list[tuple[str, str, bool]] = [
    ("Consultar KPIs de productos",
     "SELECT TOP 3 id, brand, price FROM dbo.products", True),
    ("Agregar unidades por producto",
     "SELECT TOP 3 product_id, SUM(quantity) FROM dbo.order_items GROUP BY product_id",
     True),
    ("Insertar un producto falso",
     "INSERT INTO dbo.products (id,brand,category,price,cost,launch_date) "
     "VALUES ('HACK','x','y',1,0.5,'2026-01-01')", False),
    ("Modificar todos los precios",
     "UPDATE dbo.products SET price = 0", False),
    ("Borrar todas las ventas",
     "DELETE FROM dbo.order_items", False),
    ("Eliminar una tabla",
     "DROP TABLE dbo.returns", False),
    ("Crear una tabla propia",
     "CREATE TABLE dbo.puerta_trasera (x INT)", False),
    ("Espiar las respuestas del ground truth",
     "SELECT * FROM dbo.ground_truth", False),
]


def main() -> int:
    print()
    print(f"  {'=' * 66}")
    print("  GUARDRAILS — qué puede y qué no puede hacer el agente")
    print(f"  {'=' * 66}")
    print(f"  {GRIS}Conexión: usuario 'ami_reader', el mismo que usan las tools.{FIN}")
    print(f"  {GRIS}Driver ODBC: {driver_disponible()}{FIN}")
    print()

    try:
        con = conectar_lectura()
    except pyodbc.Error as e:
        print(f"  {ROJO}No se pudo conectar: {str(e)[:80]}{FIN}")
        print(f"  {AMARILLO}¿Levantaste la base? .\\tasks.ps1 db-up{FIN}\n")
        return 1

    fallos = 0
    for descripcion, sql, deberia_funcionar in INTENTOS:
        try:
            con.cursor().execute(sql)
            ok = True
            detalle = ""
        except pyodbc.Error as e:
            ok = False
            detalle = str(e).split("]")[-1].strip()[:58]
        finally:
            con.rollback()

        correcto = ok == deberia_funcionar
        fallos += 0 if correcto else 1

        if deberia_funcionar:
            marca = f"{VERDE}PERMITIDO{FIN}" if ok else f"{ROJO}FALLÓ{FIN}    "
        else:
            marca = f"{VERDE}BLOQUEADO{FIN}" if not ok else f"{ROJO}¡PASÓ!{FIN}   "

        print(f"  {marca}  {descripcion}")
        if detalle and not deberia_funcionar:
            print(f"             {GRIS}{detalle}{FIN}")

    con.close()

    print()
    if fallos == 0:
        print(f"  {VERDE}Todas las defensas se comportaron como corresponde.{FIN}")
        print(f"  {GRIS}Las hace cumplir SQL Server, no el código de la aplicación:{FIN}")
        print(f"  {GRIS}aunque el agente construyera una consulta destructiva, el{FIN}")
        print(f"  {GRIS}motor la rechaza porque el permiso no existe.{FIN}")
    else:
        print(f"  {ROJO}{fallos} defensa(s) no se comportaron como se esperaba.{FIN}")
    print()
    return 0 if fallos == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
