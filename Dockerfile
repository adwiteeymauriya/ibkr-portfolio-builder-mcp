# syntax=docker/dockerfile:1.7

FROM python:3.13-slim AS base

# MCP Registry ownership verification: this label MUST match the `name`
# field in server.json so the registry can verify the image belongs to
# this server.
LABEL io.modelcontextprotocol.server.name="io.github.adwiteeymauriya/ibkr-portfolio-builder-mcp"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/root/.local/bin:${PATH}"

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/* \
 && curl -LsSf https://astral.sh/uv/install.sh | sh

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
COPY scan-parameters.xml ./scan-parameters.xml
RUN uv sync --frozen --no-dev

ENV HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

CMD ["uv", "run", "--no-dev", "python", "-m", "connector.server"]
