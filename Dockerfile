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
# ml (forecast), report (el PDF de /analyses/{id}.pdf). `seed` queda AFUERA:
# genera datos sintéticos para desarrollo, la API en runtime no lo toca.
RUN uv sync --frozen --no-dev --no-install-project \
    --extra rag --extra ml --extra report

# ---- Etapa final: la que sirve tráfico --------------------------------------
FROM python:3.13-slim AS runtime

# ODBC Driver 18 para SQL Server. No es un paquete de pip: pyodbc lo busca
# como librería nativa del sistema operativo (ver env.example, MSSQL_DSN).
# Se instala y se limpia en el MISMO RUN: si quedara en una capa aparte,
# `apt-get clean` de una capa posterior no reduce el tamaño de la imagen —
# las capas de Docker son aditivas, no se puede "restar" espacio ya escrito.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg \
    && curl -sSL https://packages.microsoft.com/keys/microsoft.asc | apt-key add - \
    && curl -sSL https://packages.microsoft.com/config/debian/12/prod.list \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
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

# El propio /health reporta el estado real de sus dependencias (SQL Server,
# Ollama) — reusarlo acá es más honesto que un healthcheck que solo confirma
# que el proceso de Python sigue vivo.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" || exit 1

# --host 0.0.0.0: sin esto uvicorn solo escucha en el loopback DEL CONTAINER,
# que no es el loopback del host — el puerto publicado en compose nunca
# recibiría nada. Sin --reload: eso es para tasks.ps1 en desarrollo, acá
# recargar en caliente no tiene sentido y solo agrega un watcher de archivos
# de más.
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
