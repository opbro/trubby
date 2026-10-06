FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    TRUBBY_DATA_DIR=/data \
    TRUBBY_HOST=0.0.0.0 \
    TRUBBY_FORWARDED_ALLOW_IPS=* \
    PORT=8000

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY server.py ./
COPY templates ./templates
COPY static ./static

RUN mkdir -p /data

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]

CMD ["uv", "run", "--locked", "--no-dev", "server.py"]

