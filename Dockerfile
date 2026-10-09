FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    TRUBBY_DATA_DIR=/data \
    TRUBBY_HOST=0.0.0.0 \
    TRUBBY_FORWARDED_ALLOW_IPS=*

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY server.py ./
COPY templates ./templates
COPY static ./static

RUN mkdir -p /data

EXPOSE 8000 443
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import os, ssl, urllib.request; tls = bool(os.environ.get('TRUBBY_SSL_CERTFILE')); port = os.environ.get('PORT') or (443 if tls else 8000); urllib.request.urlopen(f\"{'https' if tls else 'http'}://127.0.0.1:{port}/healthz\", timeout=2, context=ssl._create_unverified_context())"]

CMD ["uv", "run", "--locked", "--no-dev", "server.py"]

