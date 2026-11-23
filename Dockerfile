# One image, two roles (see compose.yaml):
#   webhook - FastAPI inbound replies (default CMD)
#   worker  - poll -> dispatch -> ackloop every 5 minutes
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends make \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --home-dir /app abiso

WORKDIR /app

# Dependencies first so code edits don't bust the (slow, geopandas) layer.
# The editable install keeps config.ROOT at /app, and the venv path matches
# the Makefile, so `make poll` works the same here as on a laptop.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
 && python -m venv .venv \
 && .venv/bin/pip install -r /tmp/requirements.txt

COPY Makefile ./
COPY pipeline ./pipeline
RUN .venv/bin/pip install --no-deps -e .
COPY templates ./templates
COPY reference ./reference
COPY warehouse/schema.sql ./warehouse/schema.sql

# data/ and warehouse/ are volumes in compose: SQLite audit DB, raw CAP
# archive, and the dataprep parquet artifacts all survive redeploys.
RUN mkdir -p data warehouse && chown -R abiso:abiso data warehouse

USER abiso
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD .venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"

CMD [".venv/bin/uvicorn", "pipeline.deliver.webhook:app", "--host", "0.0.0.0", "--port", "8080"]
