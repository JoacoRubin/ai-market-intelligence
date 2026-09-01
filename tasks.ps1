<#
.SYNOPSIS
    Runner de tareas del proyecto. Equivalente al Makefile del blueprint,
    en la herramienta nativa de la plataforma.

.DESCRIPTION
    Resuelve las herramientas desde PATH, carga la configuración local cuando
    una tarea la necesita y mantiene los preflight en un solo lugar.

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

$RAIZ = $PSScriptRoot

# Sin esto, Python en Windows escribe en la codificación de la consola (cp1252)
# y los acentos salen como basura.
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# El modelo de embeddings se lee del cache local y no se consulta el Hub de
# Hugging Face. Va acá y no en Python porque huggingface_hub lee esta variable
# AL IMPORTARSE: setearla después no tiene efecto. La tarea rag-descargar la
# apaga para poder bajar el modelo la primera vez.
if (-not $env:HF_HUB_OFFLINE) { $env:HF_HUB_OFFLINE = "1" }

Set-Location $RAIZ

function Titulo($texto) {
    Write-Host ""
    Write-Host "  $texto" -ForegroundColor Cyan
    Write-Host "  $('-' * $texto.Length)" -ForegroundColor DarkGray
}

function Invoke-Uv {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) {
        Write-Host "No se encontró uv en PATH." -ForegroundColor Red
        Write-Host "Instalalo desde https://docs.astral.sh/uv/ y abrí otra consola."
        exit 1
    }

    & $uv.Source @args
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Require-EnvFile {
    $rutaEnv = Join-Path $RAIZ ".env"
    if (-not (Test-Path -LiteralPath $rutaEnv -PathType Leaf)) {
        Write-Host "  Falta .env. Copiá env.example y cambiá las credenciales locales." `
            -ForegroundColor Red
        exit 1
    }
}

function Import-ProjectEnv {
    Require-EnvFile
    $rutaEnv = Join-Path $RAIZ ".env"

    foreach ($linea in Get-Content -LiteralPath $rutaEnv -Encoding UTF8) {
        $limpia = $linea.Trim()
        if (-not $limpia -or $limpia.StartsWith("#") -or -not $limpia.Contains("=")) {
            continue
        }

        $partes = $limpia.Split(@("="), 2, [System.StringSplitOptions]::None)
        $nombre = $partes[0].Trim()
        if ($nombre -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { continue }

        # Una variable explícita de la consola gana sobre .env. Esto permite
        # usar una credencial efímera sin editar archivos y evita imprimirla.
        if (-not (Test-Path "Env:$nombre")) {
            [Environment]::SetEnvironmentVariable($nombre, $partes[1], "Process")
        }
    }
}

function Require-SaPassword {
    if ([string]::IsNullOrWhiteSpace($env:MSSQL_SA_PASSWORD)) {
        Write-Host "  MSSQL_SA_PASSWORD no está definida en el proceso ni en .env." `
            -ForegroundColor Red
        exit 1
    }
}

function Require-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host "  No se encontró docker en PATH." -ForegroundColor Red
        exit 1
    }

    # Con $ErrorActionPreference = "Stop" (fijado arriba de este archivo),
    # CUALQUIER línea que un ejecutable nativo escriba a stderr se convierte en
    # excepción terminante — el redirect `*> $null` no lo evita, porque
    # PowerShell decide "terminar" en el momento en que el proceso escribe a
    # stderr, antes de que el redirect tenga chance de descartar el contenido.
    # Docker Desktop con el backend WSL2 imprime un WARNING benigno
    # (DOCKER_INSECURE_NO_IPTABLES_RAW) en cada `docker info`, así que esta
    # función fallaba siempre, con Docker sano y $LASTEXITCODE en 0. Se baja
    # la preferencia solo durante estas dos llamadas puntuales.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    docker compose version *> $null
    $composeExit = $LASTEXITCODE
    docker info *> $null
    $infoExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP

    if ($composeExit -ne 0) {
        Write-Host "  Docker Compose no está disponible." -ForegroundColor Red
        exit 1
    }

    if ($infoExit -ne 0) {
        Write-Host "  Docker no responde. Levantá Docker Desktop y reintentá." `
            -ForegroundColor Red
        exit 1
    }
}

switch ($Tarea.ToLower()) {

    "help" {
        Write-Host ""
        Write-Host "  Tareas disponibles" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  setup      Instala dependencias (uv sync)"
        Write-Host "  test       Corre toda la suite de tests"
        Write-Host "  test-fast  Tests sin SQL Server ni modelo de lenguaje real"
        Write-Host "  check      Linter (ruff)"
        Write-Host "  all        check + test. Lo que debe pasar antes de un commit"
        Write-Host ""
        Write-Host "  db-up      Levanta SQL Server y espera a que esté sano"
        Write-Host "  db-init    Crea el esquema y el usuario read-only"
        Write-Host "  db-down    Detiene SQL Server"
        Write-Host "  seed       Genera y carga el dataset sintético"
        Write-Host "  db-shell   Abre una consola sqlcmd contra la base"
        Write-Host ""
        Write-Host "  docker-build   Buildea la imagen de la API (Dockerfile)"
        Write-Host "  docker-up      Levanta SQL Server + Redis + API + worker"
        Write-Host "  docker-logs    Sigue los logs de la API containerizada"
        Write-Host "  docker-down    Detiene todo el stack de Docker"
        Write-Host ""
        Write-Host "  redis-up       Redis privado, accesible solo por la red Compose"
        Write-Host "  redis-test-up  Redis temporal en 127.0.0.1 para tests locales"
        Write-Host "  worker         Corre el worker de analisis en esta consola"
        Write-Host "  worker-logs    Sigue los logs del worker containerizado"
        Write-Host "  cola           Cuantos trabajos hay encolados y en curso"
        Write-Host ""
        Write-Host "  dataset    Muestra un resumen del dataset generado (sin base)"
        Write-Host "  api        Levanta la API local con recarga"
        Write-Host "  web-setup  Instala las dependencias del dashboard (npm install)"
        Write-Host "  web        Levanta el dashboard con recarga en localhost:5173"
        Write-Host "  agente     Ejecuta una consulta contra el agente real"
        Write-Host "  api-demo   Recorre el flujo REST completo"
        Write-Host "  pdf        Genera un informe PDF de ejemplo y lo abre"
        Write-Host "  demo       Demostración de los guardrails de seguridad"
        Write-Host "  ml-train   Entrena, backtestea y proyecta P001 desde SQL"
        Write-Host "  rag-build  Construye el índice documental local"
        Write-Host "  replay     Captura las ejecuciones del replay estático (lento)"
        Write-Host "  replay-servir  Sirve el sitio del replay en localhost:8080"
        Write-Host "  rag-descargar  Baja el modelo de embeddings (una vez por máquina)"
        Write-Host "  eval       Corre el golden set contra el modelo real (lento)"
        Write-Host "  estado     Estado de todos los componentes"
        Write-Host ""
    }

    "setup" {
        Titulo "Instalando dependencias"
        # --all-extras y no una lista a mano: la suite importa los seis
        # extras (rag y ml en los tests de esos modulos, anthropic en
        # test_agent_llm_anthropic, jobs en test_api_store_redis), asi que
        # una lista parcial deja el repo en un estado donde `test` ni
        # colecta. Es exactamente lo que corre el CI, y de eso se trata:
        # una sola fuente de verdad para lo que el proyecto necesita.
        Invoke-Uv sync --all-extras --group dev
    }

    "test" {
        Import-ProjectEnv
        Titulo "Suite completa"
        Invoke-Uv run pytest -v
    }

    "test-fast" {
        Import-ProjectEnv
        Titulo "Tests sin base de datos"
        Invoke-Uv run pytest -m "not db and not llm" -q
    }

    "check" {
        Titulo "Linter"
        Invoke-Uv run ruff check .
    }

    "all" {
        Import-ProjectEnv
        Titulo "Linter"
        Invoke-Uv run ruff check .
        Titulo "Suite completa"
        Invoke-Uv run pytest -q
    }

    "db-up" {
        Require-Docker
        Require-EnvFile
        Import-ProjectEnv
        Require-SaPassword
        Titulo "Levantando SQL Server"
        docker compose up -d sqlserver
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
        Require-Docker
        Import-ProjectEnv
        Require-SaPassword
        Titulo "Creando esquema y usuario read-only"
        $env:MSYS_NO_PATHCONV = "1"
        docker exec ami-sqlserver /opt/mssql-tools18/bin/sqlcmd `
            -S localhost -U sa -P $env:MSSQL_SA_PASSWORD -C -i /scripts/01_schema.sql
        docker exec ami-sqlserver /opt/mssql-tools18/bin/sqlcmd `
            -S localhost -U sa -P $env:MSSQL_SA_PASSWORD -C -i /scripts/02_readonly_user.sql
    }

    "db-down" {
        Require-Docker
        Titulo "Deteniendo SQL Server"
        docker compose stop sqlserver
    }

    "db-shell" {
        Require-Docker
        Import-ProjectEnv
        Require-SaPassword
        Titulo "Consola sqlcmd (escribí 'exit' para salir)"
        docker exec -it ami-sqlserver /opt/mssql-tools18/bin/sqlcmd `
            -S localhost -U sa -P $env:MSSQL_SA_PASSWORD -C -d ami
    }

    "docker-build" {
        Require-Docker
        Require-EnvFile
        Titulo "Buildeando la imagen de la API"
        docker compose build api
    }

    "docker-up" {
        Require-Docker
        Require-EnvFile
        Import-ProjectEnv
        Require-SaPassword
        Titulo "Levantando SQL Server + API containerizada"
        docker compose up -d --build
        Write-Host "  Esperando healthcheck de la API..." -NoNewline
        for ($i = 0; $i -lt 40; $i++) {
            $estado = docker inspect --format '{{.State.Health.Status}}' ami-api 2>$null
            if ($estado -eq "healthy") { Write-Host " listo" -ForegroundColor Green; break }
            Write-Host "." -NoNewline
            Start-Sleep -Seconds 3
        }
        if ($estado -ne "healthy") {
            Write-Host " no respondió a tiempo — mirá: .\tasks.ps1 docker-logs" -ForegroundColor Red
            exit 1
        }
        Write-Host "  http://localhost:8000/docs" -ForegroundColor Green
    }

    "docker-logs" {
        Require-Docker
        docker compose logs -f api
    }

    "redis-up" {
        Require-Docker
        Titulo "Levantando Redis dentro de la red privada de Compose"
        docker compose up -d redis
        Write-Host "  Redis NO está publicado al host." -ForegroundColor Green
        Write-Host "  Para tests locales usá: .\tasks.ps1 redis-test-up" -ForegroundColor Yellow
    }

    "redis-test-up" {
        Require-Docker
        Titulo "Levantando Redis para tests, limitado a 127.0.0.1"
        $override = Join-Path ([IO.Path]::GetTempPath()) `
            ("ami-redis-test-" + [guid]::NewGuid().ToString("N") + ".yml")
        $contenido = @"
services:
  redis:
    ports:
      - "127.0.0.1:6379:6379"
"@
        try {
            $utf8SinBom = New-Object System.Text.UTF8Encoding($false)
            [IO.File]::WriteAllText($override, $contenido, $utf8SinBom)
            docker compose -f docker-compose.yml -f $override up -d redis
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        } finally {
            Remove-Item -LiteralPath $override -Force -ErrorAction SilentlyContinue
        }
        Write-Host "  Redis escucha solo en 127.0.0.1:6379." -ForegroundColor Green
        Write-Host "  Al terminar: .\tasks.ps1 docker-down" -ForegroundColor Yellow
    }

    "worker" {
        Titulo "Worker de analisis (Ctrl+C para detener)"
        # JOBS_BACKEND=redis solo para ESTA consola: el worker no tiene
        # sentido en el modo memoria, donde el analisis corre dentro de la
        # API. Setearlo aca evita tener que tocar el .env para probarlo.
        $env:JOBS_BACKEND = "redis"
        Write-Host "  Requiere Redis levantado (.\tasks.ps1 redis-up)" -ForegroundColor Yellow
        Write-Host ""
        Invoke-Uv run python -m apps.jobs.worker
    }

    "worker-logs" {
        Require-Docker
        docker compose logs -f worker
    }

    "cola" {
        Titulo "Estado de la cola de analisis"
        $env:JOBS_BACKEND = "redis"
        Invoke-Uv run python -c @"
from apps.api.store_redis import hay_redis_disponible, REDIS_URL
if not hay_redis_disponible():
    print(f'  Redis no responde en {REDIS_URL}')
    raise SystemExit(1)
from apps.jobs.cola import obtener_cola
c = obtener_cola()
print(f'  cola          {c.name}')
print(f'  encolados     {len(c)}')
print(f'  en curso      {c.started_job_registry.count}')
print(f'  fallidos      {c.failed_job_registry.count}')
print(f'  terminados    {c.finished_job_registry.count}')
"@
    }

    "docker-down" {
        Require-Docker
        Titulo "Deteniendo el stack de Docker"
        docker compose down
    }

    "seed" {
        Import-ProjectEnv
        Require-SaPassword
        Titulo "Generando y cargando el dataset"
        Write-Host "  Sin ODBC Driver 18 esto tarda varios minutos." -ForegroundColor Yellow
        Invoke-Uv run python -m seeds.load
    }

    "api" {
        Titulo "API en http://localhost:8000"
        Write-Host "  Documentacion interactiva: http://localhost:8000/docs" -ForegroundColor Green
        Write-Host "  Ctrl+C para detener"
        Write-Host ""
        Invoke-Uv run uvicorn apps.api.main:app --reload --port 8000
    }

    "web-setup" {
        Titulo "Instalando dependencias del dashboard"
        Push-Location (Join-Path $RAIZ "apps\web")
        try { npm install } finally { Pop-Location }
    }

    "web" {
        Titulo "Dashboard en http://localhost:5173"
        Write-Host "  Requiere la API corriendo en :8000 (.\tasks.ps1 api en otra consola)" -ForegroundColor Yellow
        Write-Host "  Ctrl+C para detener"
        Write-Host ""
        Push-Location (Join-Path $RAIZ "apps\web")
        try { npm run dev } finally { Pop-Location }
    }

    "agente" {
        Titulo "Agente completo con el modelo real (lento: minutos en CPU)"
        Invoke-Uv run python -m agent.demo @args
    }

    "ml-train" {
        Import-ProjectEnv
        Titulo "Forecast de P001 con backtesting y registro en MLflow"
        Invoke-Uv run python -c @"
from datetime import date
from ml.forecast import pronosticar
from ml.series import serie_diaria

desde, hasta = date(2025, 7, 1), date(2026, 6, 30)
_, serie = serie_diaria('P001', desde, hasta)
resultado = pronosticar('P001', serie, horizonte=30, desde=desde, hasta=hasta)
print(f'  prediccion    {resultado.valor:,.1f} unidades / 30 dias')
print(f'  MAPE modelo   {resultado.backtest.mape_modelo}')
print(f'  MAPE baseline {resultado.backtest.mape_baseline}')
print(f'  uso baseline  {resultado.uso_baseline}')
print(f'  MLflow run    {resultado.run_id or "no disponible"}')
"@
    }

    "rag-build" {
        Titulo "Construyendo el indice documental (embeddings en CPU)"
        Invoke-Uv run python -m rag.build
    }

    "rag-descargar" {
        Titulo "Descargando el modelo de embeddings (una sola vez por maquina)"
        # La UNICA tarea que tiene permitido salir a la red. Todas las demas
        # corren offline contra el cache.
        $env:HF_HUB_OFFLINE = "0"
        Write-Host "  Modelo: intfloat/multilingual-e5-small" -ForegroundColor Yellow
        Write-Host ""
        Invoke-Uv run python -c "from rag.indice import obtener_modelo; obtener_modelo(); print('  Modelo en cache local.')"
    }

    "replay" {
        Titulo "Capturando ejecuciones para el replay estatico (lento: minutos)"
        Write-Host "  Requiere SQL Server levantado y Ollama respondiendo." -ForegroundColor Yellow
        Write-Host ""
        Invoke-Uv run python -m replay
    }

    "replay-servir" {
        Titulo "Sitio del replay en http://localhost:8080"
        # No alcanza con abrir el index.html: el navegador bloquea fetch() sobre
        # file:// y los JSON no cargan. Hace falta HTTP, aunque sea local.
        Write-Host "  Ctrl+C para detener"
        Write-Host ""
        Invoke-Uv run python -m http.server 8080 --directory (Join-Path $RAIZ "docs\replay")
    }

    "eval" {
        Titulo "Evaluacion del router contra el golden set (modelo real, lento)"
        Invoke-Uv run pytest -m llm -v -s
    }

    "api-demo" {
        Titulo "Recorrido del flujo REST completo"
        Invoke-Uv run python -m apps.api.demo
    }

    "dataset" {
        Titulo "Resumen del dataset (generado en memoria, sin base)"
        Invoke-Uv run python -c @"
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
        Invoke-Uv run python -m core.demo_pdf
        $ruta = Join-Path $RAIZ "docs\ejemplos\informe_ejemplo.pdf"
        if (Test-Path $ruta) {
            Write-Host "  Abriendo $ruta" -ForegroundColor Green
            Invoke-Item $ruta
        }
    }

    "demo" {
        Titulo "Guardrails de seguridad en vivo"
        Invoke-Uv run python -m core.demo_guardrails
    }

    "estado" {
        Titulo "Estado de los componentes"
        Write-Host "  uv          " -NoNewline
        Write-Host (Invoke-Uv --version) -ForegroundColor Green

        Write-Host "  python      " -NoNewline
        Write-Host (Invoke-Uv run python --version) -ForegroundColor Green

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
