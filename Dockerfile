# NOT build-tested in the authoring environment (Docker was not installed there).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Non-root runtime user
RUN useradd --create-home --uid 10001 app

# Install dependencies + package first for better layer caching
COPY pyproject.toml ./
COPY src ./src
RUN pip install .

# Synthetic data, evals, demo script
COPY data ./data
COPY evals ./evals
COPY scripts ./scripts

# Writable state (SQLite DB, FAISS index) lives on a volume, not in the image
RUN mkdir -p /app/var && chown -R app:app /app/var
ENV AGENTOPS_DB_PATH=/app/var/agentops.db \
    AGENTOPS_INDEX_DIR=/app/var/index

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

# The API key is NEVER baked into the image: pass GOOGLE_API_KEY at runtime.
CMD ["uvicorn", "agentops.api.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]
