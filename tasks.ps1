<#
.SYNOPSIS
    Runner de tareas del proyecto. Equivalente al Makefile del blueprint,
    en la herramienta nativa de la plataforma.

.DESCRIPTION
    Encapsula la ruta de uv y la variable UV_PROJECT_ENVIRONMENT para que nadie
    tenga que recordarlas. Ese "nadie" incluye a vos dentro de tres semanas.

.EXAMPLE
    .\tasks.ps1 help
    .\tasks.ps1 test
    .\tasks.ps1 demo
#>

param(
    [Parameter(Position = 0)]
    [string]$Tarea = "help"
)

$ErrorActionPreference = "Stop"

# El venv vive FUERA de OneDrive a propósito: sincronizar decenas de miles de
# archivos de un entorno virtual ralentiza la máquina y corrompe archivos en uso.
$env:UV_PROJECT_ENVIRONMENT = "C:\Users\famas\.venvs\ai-market-intelligence"
$UV = "C:\Users\famas\.local\bin\uv.exe"
$RAIZ = $PSScriptRoot

# Sin esto, Python en Windows escribe en la codificación de la consola (cp1252)
# y los acentos salen como basura.
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not (Test-Path $UV)) {
    Write-Host "No se encontró uv en $UV" -ForegroundColor Red
    Write-Host "Instalalo con: irm https://astral.sh/uv/install.ps1 | iex"
    exit 1
}

Set-Location $RAIZ

function Titulo($texto) {
    Write-Host ""
    Write-Host "  $texto" -ForegroundColor Cyan
    Write-Host "  $('-' * $texto.Length)" -ForegroundColor DarkGray
}

switch ($Tarea.ToLower()) {

    "help" {
        Write-Host ""
        Write-Host "  Tareas disponibles" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  setup      Instala dependencias (uv sync)"
        Write-Host "  test       Corre toda la suite de tests"
        Write-Host "  test-fast  Solo los tests que no necesitan base de datos"
        Write-Host "  check      Linter (ruff)"
        Write-Host "  all        check + test. Lo que debe pasar antes de un commit"
        Write-Host ""
        Write-Host "  db-up      Levanta SQL Server y espera a que esté sano"
        Write-Host "  db-init    Crea el esquema y el usuario read-only"
        Write-Host "  db-down    Detiene SQL Server"
        Write-Host "  seed       Genera y carga el dataset sintético"
        Write-Host "  db-shell   Abre una consola sqlcmd contra la base"
        Write-Host ""
        Write-Host "  dataset    Muestra un resumen del dataset generado (sin base)"
        Write-Host "  pdf        Genera un informe PDF de ejemplo y lo abre"
        Write-Host "  demo       Demostración de los guardrails de seguridad"
        Write-Host "  estado     Estado de todos los componentes"
        Write-Host ""
    }

    "setup" {
        Titulo "Instalando dependencias"
        & $UV sync --extra seed --extra report --group dev
    }

    "test" {
        Titulo "Suite completa"
        & $UV run pytest -v
    }

    "test-fast" {
        Titulo "Tests sin base de datos"
        & $UV run pytest -m "not db" -q
    }

    "check" {
        Titulo "Linter"
        & $UV run ruff check .
    }

    "all" {
        Titulo "Linter"
        & $UV run ruff check .
        Titulo "Suite completa"
        & $UV run pytest -q
    }

    "db-up" {
        Titulo "Levantando SQL Server"
        docker compose up -d
        Write-Host "  Esperando healthcheck..." -NoNewline
        for ($i = 0; $i -lt 40; $i++) {
            $estado = docker inspect --format '{{.State.Health.Status}}' ami-sqlserver 2>$null
            if ($estado -eq "healthy") { Write-Host " listo" -ForegroundColor Green; break }
            Write-Host "." -NoNewline
            Start-Sleep -Seconds 3
        }
        if ($estado -ne "healthy") {
            Write-Host " no respondió a tiempo" -ForegroundColor Red
            exit 1
        }
    }

    "db-init" {
        Titulo "Creando esquema y usuario read-only"
        $env:MSYS_NO_PATHCONV = "1"
        docker exec ami-sqlserver /opt/mssql-tools18/bin/sqlcmd `
            -S localhost -U sa -P 'Dev_Local_2026!' -C -i /scripts/01_schema.sql
        docker exec ami-sqlserver /opt/mssql-tools18/bin/sqlcmd `
            -S localhost -U sa -P 'Dev_Local_2026!' -C -i /scripts/02_readonly_user.sql
    }

    "db-down" {
        Titulo "Deteniendo SQL Server"
        docker compose down
    }

    "db-shell" {
        Titulo "Consola sqlcmd (escribí 'exit' para salir)"
        docker exec -it ami-sqlserver /opt/mssql-tools18/bin/sqlcmd `
            -S localhost -U sa -P 'Dev_Local_2026!' -C -d ami
    }

    "seed" {
        Titulo "Generando y cargando el dataset"
        Write-Host "  Sin ODBC Driver 18 esto tarda varios minutos." -ForegroundColor Yellow
        & $UV run python -m seeds.load
    }

    "api" {
        Titulo "API en http://localhost:8000"
        Write-Host "  Documentacion interactiva: http://localhost:8000/docs" -ForegroundColor Green
        Write-Host "  Ctrl+C para detener"
        Write-Host ""
        & $UV run uvicorn apps.api.main:app --reload --port 8000
    }

    "agente" {
        Titulo "Agente completo con el modelo real (lento: minutos en CPU)"
        & $UV run python -m agent.demo $args[1]
    }

    "eval" {
        Titulo "Evaluacion del router contra el golden set (modelo real, lento)"
        & $UV run pytest -m llm -v -s
    }

    "api-demo" {
        Titulo "Recorrido del flujo REST completo"
        & $UV run python -m apps.api.demo
    }

    "dataset" {
        Titulo "Resumen del dataset (generado en memoria, sin base)"
        & $UV run python -c @"
from seeds.generate import DatasetConfig, generar_dataset
ds = generar_dataset(DatasetConfig())
it = ds['order_items']
it2 = it.merge(ds['orders'][['id','created_at']], left_on='order_id', right_on='id')
it2['rev'] = it2['quantity'] * it2['unit_price']
for k, v in ds.items():
    print(f'  {k:<14} {len(v):>8,} filas')
print()
print(f\"  revenue          USD {it2['rev'].sum():>14,.2f}\")
print(f\"  ticket promedio  USD {it2.groupby('order_id')['rev'].sum().mean():>14,.2f}\")
print(f\"  tasa devolucion      {len(ds['returns'])/len(it):>14.2%}\")
print()
print('  Eventos sembrados (ground truth):')
print(ds['ground_truth'][['tipo','product_id','fecha']].to_string(index=False))
"@
    }

    "pdf" {
        Titulo "Generando informe PDF de ejemplo"
        & $UV run python -m core.demo_pdf
        $ruta = Join-Path $RAIZ "docs\ejemplos\informe_ejemplo.pdf"
        if (Test-Path $ruta) {
            Write-Host "  Abriendo $ruta" -ForegroundColor Green
            Invoke-Item $ruta
        }
    }

    "demo" {
        Titulo "Guardrails de seguridad en vivo"
        & $UV run python -m core.demo_guardrails
    }

    "estado" {
        Titulo "Estado de los componentes"
        Write-Host "  uv          " -NoNewline
        Write-Host (& $UV --version) -ForegroundColor Green

        Write-Host "  python      " -NoNewline
        Write-Host (& $UV run python --version) -ForegroundColor Green

        Write-Host "  docker      " -NoNewline
        $d = docker inspect --format '{{.State.Health.Status}}' ami-sqlserver 2>$null
        if ($d -eq "healthy") { Write-Host "SQL Server healthy" -ForegroundColor Green }
        else { Write-Host "SQL Server no disponible (.\tasks.ps1 db-up)" -ForegroundColor Yellow }

        Write-Host "  ollama      " -NoNewline
        try {
            $null = Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 3
            Write-Host "respondiendo" -ForegroundColor Green
        } catch { Write-Host "no responde (ollama serve)" -ForegroundColor Yellow }

        Write-Host "  odbc        " -NoNewline
        $drv = (Get-OdbcDriver -Platform 64-bit -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like "ODBC Driver 1*SQL Server" }).Name
        if ($drv) { Write-Host $drv -ForegroundColor Green }
        else { Write-Host "solo el driver legacy — la carga va a ser lenta" -ForegroundColor Yellow }
    }

    default {
        Write-Host "Tarea desconocida: $Tarea" -ForegroundColor Red
        Write-Host "Probá: .\tasks.ps1 help"
        exit 1
    }
}
