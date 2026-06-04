# lina-gemini-multimodal — Dockerfile
#
# Usa el multi-stage build compartido del monorepo.
# Build:
#   docker build --build-arg MCP_DIR=mcps/gemini-multimodal \
#                --build-arg MCP_CMD=lina-gemini-multimodal \
#                -t lina-mcp-gemini-multimodal \
#                -f deploy/docker/Dockerfile.mcp .
#
# También se puede buildear standalone:
#   docker build -t lina-mcp-gemini-multimodal .
#
# Runtime:
#   docker run -e GEMINI_API_KEY=<key> -e MCP_TRANSPORT=streamable-http \
#              -p 8112:8000 lina-mcp-gemini-multimodal

# ─── Stage 1: builder ────────────────────────────────────────────────────────
FROM python:3.13-slim-bookworm AS builder

WORKDIR /app
RUN pip install --no-cache-dir "uv>=0.5"

COPY pyproject.toml .
RUN uv sync --no-dev --frozen --quiet

# Copy source after deps for layer caching
COPY src/ src/

# ─── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.13-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home --shell /bin/sh mcp

WORKDIR /app

COPY --from=builder /app /app

ENV MCP_TRANSPORT=streamable-http \
    MCP_PORT=8000 \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1

USER mcp
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=15s \
    CMD python3 -c \
        "import socket,os; p=int(os.environ.get('MCP_PORT','8000')); s=socket.socket(); r=s.connect_ex(('127.0.0.1',p)); s.close(); exit(r)"

CMD ["lina-gemini-multimodal"]
