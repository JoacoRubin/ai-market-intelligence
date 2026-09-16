/* ============================================================================
   Usuario read-only para las tools del agente.

   Este script es el guardrail más importante del sistema, y conviene entender
   por qué existe.

   El agente construye consultas a partir de una entrada en lenguaje natural que
   viene del usuario y de documentos recuperados por RAG. Ninguna de esas dos
   fuentes es confiable: un documento puede contener texto diseñado para inyectar
   instrucciones, y el modelo puede equivocarse solo, sin ayuda de nadie.

   El diseño no apuesta a que eso no pase. Apuesta a que, cuando pase, no importe.

   Defensa en capas:
     1. Tools con parámetros tipados (no SQL arbitrario)  -> capa de aplicación
     2. Consultas parametrizadas                          -> capa de driver
     3. ESTE USUARIO: solo SELECT                         -> capa de motor

   Las dos primeras las escribe un humano y pueden tener bugs. La tercera la
   hace cumplir SQL Server. Si el agente intentara un DROP TABLE, el motor lo
   rechaza — no porque el código lo haya previsto, sino porque el permiso no
   existe.
   ============================================================================ */

/* La contraseña NO está escrita en este archivo: entra como variable de
   sqlcmd y sale del entorno de quien corre el script.

       sqlcmd ... -v APP_PASSWORD="$env:MSSQL_APP_PASSWORD" -i 02_readonly_user.sql

   `:setvar` NO define un valor por defecto acá a propósito. Si la variable no
   se pasa, sqlcmd aborta con "variable not defined" y el usuario no se crea —
   que es mejor que crearlo con una contraseña conocida y versionada, como
   pasaba antes. Un script que se completa con una credencial pública es peor
   que uno que falla.

   `-b` en la invocación hace que ese fallo devuelva un exit code distinto de
   cero, para que no pase inadvertido en un pipeline. */
:on error exit
GO

USE master;
GO

IF NOT EXISTS (SELECT 1 FROM sys.sql_logins WHERE name = 'ami_reader')
BEGIN
    /* CHECK_POLICY se deja en OFF de forma deliberada y acotada: este login
       existe solo en bases locales y efímeras (desarrollo y el job de CI), y
       la política de Windows rechazaría contraseñas generadas por corrida.
       No aplica a ningún entorno persistente — si alguna vez lo hiciera, esta
       línea es lo primero que hay que sacar. */
    CREATE LOGIN ami_reader
        WITH PASSWORD = '$(APP_PASSWORD)',
             CHECK_POLICY = OFF,
             DEFAULT_DATABASE = ami;
END
GO

USE ami;
GO

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = 'ami_reader')
BEGIN
    CREATE USER ami_reader FOR LOGIN ami_reader;
END
GO

/* Solo lectura sobre el esquema de negocio. */
ALTER ROLE db_datareader ADD MEMBER ami_reader;
GO

/* Denegaciones explícitas. db_datareader ya no otorga escritura, pero un DENY
   es más fuerte que cualquier GRANT posterior: si alguien agrega este usuario a
   otro rol por error, la denegación sigue ganando. */
DENY INSERT, UPDATE, DELETE, ALTER, CREATE TABLE, EXECUTE TO ami_reader;
GO

/* El ground truth queda fuera del alcance del agente.
   Darle acceso sería hacer trampa: el sistema debe DETECTAR las anomalías,
   no leer la lista de respuestas. Cualquier evaluación sobre datos que el
   agente puede leer directamente no mide nada. */
DENY SELECT ON dbo.ground_truth TO ami_reader;
GO

PRINT 'Usuario ami_reader creado con permisos de solo lectura.';
GO
