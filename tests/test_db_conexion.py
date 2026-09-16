"""Cómo se arma la cadena de conexión ODBC, y por qué importa.

Estos tests no tocan la base: verifican la CADENA que se le manda al driver.
Es deliberado —corren sin SQL Server, sin marca `db` y en milisegundos— y
cubre exactamente donde estaban los tres defectos:

1. Los valores se concatenaban crudos, así que un `;` en una contraseña no
   fallaba: armaba una cadena de conexión distinta de la esperada.
2. `TrustServerCertificate=yes` se aplicaba siempre. El comentario del código
   decía "el servidor es local", pero nada lo verificaba.
3. `MSSQL_APP_PASSWORD` tenía la contraseña real como valor por defecto, así
   que un entorno mal configurado se conectaba igual en vez de fallar.

`driver_disponible` se reemplaza en todos los casos: si no, estos tests
exigirían un driver ODBC instalado y no correrían en el job de CI que no
levanta SQL Server — que es justamente donde más barato sale correrlos.
"""

from __future__ import annotations

import pyodbc
import pytest

import core.db as db

DRIVER_FALSO = "ODBC Driver 18 for SQL Server"


@pytest.fixture(autouse=True)
def _driver_previsible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "driver_disponible", lambda: DRIVER_FALSO)


def parsear_cadena_odbc(cadena: str) -> dict[str, str]:
    """Parsea una cadena de conexión como la lee un driver ODBC.

    Existe porque `cadena.split(";")` NO es cómo se parsea esto, y un test que
    lo use da falsos positivos: adentro de `{...}` el punto y coma es un
    carácter más, no un separador. Sin respetar las llaves, una contraseña que
    contenga `;` "aparece" como dos parámetros aunque el driver la lea entera.

    Reimplementar la regla acá es lo que permite afirmar que NO hay inyección,
    en vez de afirmar que el texto contiene tal o cual substring.
    """
    parametros: dict[str, str] = {}
    resto = cadena
    while resto:
        clave, _, resto = resto.partition("=")
        clave = clave.strip()
        if resto.startswith("{"):
            resto = resto[1:]
            valor = ""
            while True:
                trozo, _, resto = resto.partition("}")
                valor += trozo
                if resto.startswith("}"):  # `}}` escapado: sigue el mismo valor
                    valor += "}"
                    resto = resto[1:]
                    continue
                break
            resto = resto[1:] if resto.startswith(";") else resto
        else:
            valor, _, resto = resto.partition(";")
        parametros[clave] = valor
    return parametros


# --- encomillado -------------------------------------------------------------

def test_encomillar_encierra_entre_llaves() -> None:
    assert db._encomillar("simple") == "{simple}"


def test_encomillar_duplica_la_llave_de_cierre() -> None:
    """Es el único carácter con significado adentro de las llaves.

    Sin duplicarla, un valor que la contenga cierra el bloque antes de tiempo y
    el resto se interpreta como más parámetros de conexión.
    """
    assert db._encomillar("ab}cd") == "{ab}}cd}"


@pytest.mark.parametrize("password", [
    "x;TrustServerCertificate=yes",
    "x;Encrypt=no",
    "pass;word;con;muchos;puntoycoma",
])
def test_el_punto_y_coma_de_una_password_no_inyecta_parametros(
    password: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El caso que motivó el cambio.

    El `;` es el separador de parámetros de una cadena ODBC. Concatenado crudo,
    una contraseña que lo contenga deja de ser un valor y pasa a ser sintaxis.
    Encomillada, el driver la lee entera como lo que es.
    """
    monkeypatch.setattr(db, "SERVIDOR", "localhost,1433")

    parametros = parsear_cadena_odbc(db._cadena("usuario", password, "ami"))

    # La contraseña llega ENTERA como un solo valor...
    assert parametros["PWD"] == password
    # ...y la cadena tiene exactamente los parámetros que este código pone.
    # Nada de lo que venía después del `;` se convirtió en uno nuevo.
    assert set(parametros) == {
        "Driver", "Server", "UID", "PWD", "Database", "TrustServerCertificate",
    }


@pytest.mark.parametrize("password", [
    "con}llave",
    "}",
    "}}",
    "a}b;c}d",
    "{llave;de;apertura",
])
def test_una_password_con_llaves_sobrevive_el_viaje_de_ida_y_vuelta(
    password: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La prueba más fuerte del encomillado: escribir y volver a leer.

    La llave de cierre es el único carácter con significado adentro del bloque.
    Si el escape estuviera mal, el valor que lee el driver no sería el que se
    quiso mandar — y el síntoma sería un "login failed" imposible de diagnosticar
    desde afuera, porque la contraseña en el `.env` se ve correcta.
    """
    monkeypatch.setattr(db, "SERVIDOR", "localhost,1433")

    parametros = parsear_cadena_odbc(db._cadena("u", password, "ami"))

    assert parametros["PWD"] == password


def test_el_usuario_y_la_base_tambien_se_encomillan(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(db, "SERVIDOR", "localhost,1433")

    cadena = db._cadena("ami_reader", "secreta", "ami")

    assert "UID={ami_reader}" in cadena
    assert "Database={ami}" in cadena


def test_el_servidor_no_se_encomilla(monkeypatch: pytest.MonkeyPatch) -> None:
    """`host,puerto` es sintaxis que el driver parsea, no un literal.

    Encerrarlo en llaves lo volvería un hostname con una coma adentro y la
    conexión dejaría de resolver el puerto.
    """
    monkeypatch.setattr(db, "SERVIDOR", "localhost,1433")

    assert "Server=localhost,1433" in db._cadena("u", "p", "ami")


# --- servidor local vs remoto ------------------------------------------------

@pytest.mark.parametrize("servidor", [
    "localhost",
    "localhost,1433",
    "127.0.0.1,1433",
    "LOCALHOST,1433",
    "[::1],1433",
    "localhost\\SQLEXPRESS",
    # Nombre del servicio y del container en docker-compose.yml: la API y el
    # worker llegan a la base por la red privada de Compose, que no sale del
    # host. Si esto diera "remoto", los dos dejarían de conectar.
    "sqlserver,1433",
    "ami-sqlserver",
])
def test_reconoce_los_servidores_locales(servidor: str) -> None:
    assert db.es_servidor_local(servidor) is True


@pytest.mark.parametrize("servidor", [
    "mi-servidor.database.windows.net,1433",
    "10.0.0.5,1433",
    "produccion.interna",
    "localhost.evil.com",
])
def test_cualquier_otra_cosa_es_remota(servidor: str) -> None:
    """El default es desconfiar.

    Equivocarse hacia "remoto" cuesta una conexión que falla y se ve. Hacia
    "local" cuesta aceptar cualquier certificado sin que nadie se entere.

    `localhost.evil.com` está en la lista a propósito: un match por substring
    lo habría dado por local.
    """
    assert db.es_servidor_local(servidor) is False


def test_contra_servidor_local_se_confia_en_el_certificado(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """SQL Server se autofirma y no hay red en el medio: es aceptable."""
    monkeypatch.setattr(db, "SERVIDOR", "localhost,1433")

    cadena = db._cadena("u", "p", "ami")

    assert "TrustServerCertificate=yes" in cadena
    assert "Encrypt=yes" not in cadena


def test_contra_servidor_remoto_se_exige_cifrado_validado(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """La regresión que este test existe para impedir.

    Antes la opción se aplicaba siempre. Con `MSSQL_SERVER` apuntando a un host
    remoto, la conexión seguía aceptando cualquier certificado — un
    man-in-the-middle sobre credenciales de base, habilitado por una decisión
    tomada para el caso local.
    """
    monkeypatch.setattr(db, "SERVIDOR", "mi-servidor.database.windows.net,1433")

    cadena = db._cadena("u", "p", "ami")

    assert "TrustServerCertificate=yes" not in cadena
    assert "Encrypt=yes" in cadena


def test_el_driver_legacy_no_recibe_ninguna_de_las_dos_opciones(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """El driver "SQL Server" de Windows no las entiende: las ignora o falla."""
    monkeypatch.setattr(db, "driver_disponible", lambda: "SQL Server")
    monkeypatch.setattr(db, "SERVIDOR", "localhost,1433")

    cadena = db._cadena("u", "p", "ami")

    assert "TrustServerCertificate" not in cadena
    assert "Encrypt=" not in cadena


# --- credenciales exigidas ---------------------------------------------------

def test_la_password_de_lectura_no_tiene_valor_por_defecto(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """La regresión más importante de este archivo.

    `conectar_lectura` traía `"Reader_Local_2026!"` como default. Un entorno sin
    la variable NO fallaba: se conectaba con una credencial escrita en el
    repositorio, y el error quedaba disfrazado de sistema sano.
    """
    monkeypatch.delenv("MSSQL_APP_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="MSSQL_APP_PASSWORD"):
        db.conectar_lectura()


def test_la_password_administrativa_sigue_siendo_obligatoria(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MSSQL_SA_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="MSSQL_SA_PASSWORD"):
        db.conectar_admin()


def test_el_usuario_de_lectura_si_conserva_su_default(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Es un nombre de usuario, no un secreto.

    Coincide con el login que crea `infra/sql/02_readonly_user.sql`, así que
    exigirlo por entorno sería fricción sin ninguna ganancia de seguridad.
    """
    monkeypatch.delenv("MSSQL_APP_USER", raising=False)
    monkeypatch.setenv("MSSQL_APP_PASSWORD", "loquesea")
    monkeypatch.setattr(db, "SERVIDOR", "localhost,1433")

    capturada: dict[str, str] = {}

    def _falso_connect(cadena: str, **_kwargs: object) -> object:
        capturada["cadena"] = cadena
        return object()

    monkeypatch.setattr(pyodbc, "connect", _falso_connect)
    db.conectar_lectura()

    assert "UID={ami_reader}" in capturada["cadena"]


def test_la_conexion_de_lectura_se_pide_en_modo_solo_lectura(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """`readonly=True` es una capa más, arriba de los DENY del motor."""
    monkeypatch.setenv("MSSQL_APP_PASSWORD", "loquesea")
    monkeypatch.setattr(db, "SERVIDOR", "localhost,1433")

    capturados: dict[str, object] = {}

    def _falso_connect(cadena: str, **kwargs: object) -> object:
        capturados.update(kwargs)
        return object()

    monkeypatch.setattr(pyodbc, "connect", _falso_connect)
    db.conectar_lectura()

    assert capturados["readonly"] is True
