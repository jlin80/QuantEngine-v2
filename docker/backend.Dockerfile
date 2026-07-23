# ============================================================================
# Quant Engine V2 — backend (Python 3.12, non-root, asyncio 24/7)
# ============================================================================
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencias primero para aprovechar la cache de capas.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código de la aplicación.
COPY app/ ./app/
COPY config/ ./config/
COPY alembic.ini .
COPY docs/ ./docs/

# Usuario sin privilegios.
RUN useradd --create-home --shell /usr/sbin/nologin quant \
    && mkdir -p /app/logs \
    && chown -R quant:quant /app
USER quant

EXPOSE 8000

CMD ["python", "-m", "app"]
