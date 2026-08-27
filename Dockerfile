# syntax=docker/dockerfile:1
#
# Build multi-etapa. El motivo NO es el mismo que en el proyecto RAG hermano
# (rag-chatbot): ahí sacar torch del runtime achicaba la imagen a la mitad
# porque el encoder se migró a ONNX. Acá el grupo `rag` (ver pyproject.toml)
# usa `sentence-transformers` DIRECTO, sin esa migración — y el runtime SÍ
# necesita torch: cada consulta del usuario se embebe en caliente contra el
# índice FAISS, no solo la construcción del índice. No hay forma de sacar
# torch de esta imagen sin reescribir el motor de embeddings, y eso es un
# cambio de arquitectura que nadie pidió (ver ADR-010).
#
# El multi-stage acá sirve para otra cosa: separar la capa de dependencias
# (pesada, cambia poco) de la capa de código (liviana, cambia en cada commit)
# — y no instalar en la imagen final las herramientas de build.

# ---- Etapa builder: resuelve dependencias con uv ---------------------------
FROM python:3.13-slim AS builder

# La imagen oficial de uv, no pip install: es el mismo gestor que ya usa el
# proyecto en local (ver README, UV_PROJECT_ENVIRONMENT), y evita el drift de
# "en mi máquina resuelve distinto que en la imagen".
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Solo lo que resuelve dependencias. Esta capa se cachea y no se invalida
# hasta que cambie pyproject.toml o uv.lock — un commit que solo toca
# agent/*.py no vuelve a bajar ni un paquete.
COPY pyproject.toml uv.lock ./

# --frozen: falla si el lock no coincide con pyproject.toml, en vez de
# resolver de nuevo en silencio y servir versiones que el golden set nunca
# midió. --no-dev: pytest/ruff/mypy no viajan a la imagen que sirve tráfico.
# --no-install-project: todavía no hay código copiado, solo se resuelven
# dependencias — el proyecto en sí se instala solo, corriendo desde /app con
# los paquetes que ya traen __init__.py (mismo mecanismo que pythonpath=["."]
# en pytest).
#
# Extras runtime: rag (FAISS + embeddings, lo que necesita cada consulta),
# ml (forecast), report (el PDF de /analyses/{id}.pdf) y jobs (la cola y el
# almacén compartido — los usan tanto la API como el worker, que corren
# desde esta misma imagen). `seed` queda AFUERA: genera datos sintéticos
# para desarrollo, el runtime no lo toca.
RUN uv sync --frozen --no-dev --no-install-project \
    --extra rag --extra ml --extra report --extra jobs

# ---- Etapa final: la que sirve tráfico --------------------------------------
FROM python:3.13-slim AS runtime

# ODBC Driver 18 para SQL Server. No es un paquete de pip: pyodbc lo busca
# como librería nativa del sistema operativo (ver env.example, MSSQL_DSN).
#
# `apt-key` NO se usa: python:3.13-slim corre sobre Debian 13 (Trixie), que
# lo eliminó del todo (estaba deprecado desde Debian 11). Verificado con un
# build real: `apt-key add` tira "not found", exit 127 — no es hipotético.
# El método vigente es un keyring propio + `signed-by` en el .list.
#
# El repo de Microsoft se sirve para debian/12 (bookworm) y no debian/13
# (trixie) todavía — Microsoft no publica el mismo día que sale una versión
# nueva de Debian —, pero el .deb de msodbcsql18 es compatible: mismo
# formato, misma glibc. La distribución del .list dice "bookworm" a
# propósito, aunque la imagen base sea trixie: tiene que coincidir con lo
# que el repo de Microsoft publica, no con la imagen que lo instala.
#
# Se instala y se limpia en el MISMO RUN: si quedara en una capa aparte,
# `apt-get clean` de una capa posterior no reduce el tamaño de la imagen —
# las capas de Docker son aditivas, no se puede "restar" espacio ya escrito.
#
# `libgssapi-krb5-2` va EXPLÍCITA y no es redundante, aunque `curl` ya la
# arrastre: msodbcsql18 la carga sin declararla como dependencia dura, así
# que el `apt-get purge curl gnupg && autoremove` de abajo la borraba por
# huérfana y dejaba el driver registrado pero inutilizable. Nombrarla en el
# mismo `install` que el driver la marca como manual y autoremove la
# respeta.
#
# Este bug NO se ve desde afuera y por eso vale la pena el comentario:
# `pyodbc.drivers()` seguía listando "ODBC Driver 18 for SQL Server" (el
# registro de unixODBC estaba intacto) y el error de conexión decía
# "Can't open lib ... file not found" cuando el archivo SÍ existía — lo que
# faltaba era una dependencia suya. Se diagnostica con:
#   docker exec <container> ldd /opt/microsoft/msodbcsql18/lib64/libmsodbcsql-*.so* | grep 'not found'
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg \
    && curl -sSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64,arm64,armhf signed-by=/usr/share/keyrings/microsoft-prod.gpg] \
        https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends \
        msodbcsql18 libgssapi-krb5-2 \
    && apt-get purge -y curl gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# El entorno virtual ya resuelto, tal cual quedó en el builder. Nada de
# herramientas de build (uv, apt, curl, gnupg) llega a esta capa.
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# El código. Deliberadamente carpeta por carpeta y no `COPY . .`: así el
# .dockerignore no es la única defensa contra copiar basura (.venv local,
# data/, mlruns/) — si alguien la rompe, esta lista sigue siendo explícita
# sobre qué entra a la imagen y qué no.
COPY agent/ agent/
COPY apps/ apps/
COPY core/ core/
COPY eval/ eval/
COPY ml/ ml/
COPY rag/ rag/
COPY seeds/ seeds/

# HF_HUB_OFFLINE=1: mismo criterio que tasks.ps1 (ver rag/indice.py). El
# modelo de embeddings y el índice FAISS NO viajan en la imagen — son datos
# generados, no código — y llegan por los volúmenes `hf_cache` e
# `indice_data` de docker-compose.yml. Si no están montados, `cargar_indice()`
# devuelve None y el agente degrada solo a análisis sin evidencia documental
# (ya implementado en rag/build.py); no hace falta nada especial acá para
# sostener esa degradación.
ENV HF_HUB_OFFLINE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# El propio /health reporta el estado real de sus dependencias — reusarlo
# acá es más honesto que un healthcheck que solo confirma que el proceso de
# Python sigue vivo.
#
# Pero NO alcanza con que responda: `/health` devuelve 200 con
# `estado: "degradado"` cuando la base no está, porque la API sigue
# sirviendo (esa degradación es deliberada, ver README). Un HEALTHCHECK que
# solo mire el código HTTP marca "healthy" un container que no puede hacer
# un solo análisis real — y `docker-up` daría verde sobre un sistema roto.
# Medido, no supuesto: fue exactamente lo que pasó con el driver ODBC roto.
# Por eso se exige `estado == "ok"`.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import json,urllib.request,sys; d=json.load(urllib.request.urlopen('http://localhost:8000/health',timeout=3)); sys.exit(0 if d['estado']=='ok' else 1)" || exit 1

# --host 0.0.0.0: sin esto uvicorn solo escucha en el loopback DEL CONTAINER,
# que no es el loopback del host — el puerto publicado en compose nunca
# recibiría nada. Sin --reload: eso es para tasks.ps1 en desarrollo, acá
# recargar en caliente no tiene sentido y solo agrega un watcher de archivos
# de más.
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
