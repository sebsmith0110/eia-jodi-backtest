FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY etl/ etl/
ENV PATH="/app/.venv/bin:$PATH"
CMD ["python", "-m", "etl.run"]
