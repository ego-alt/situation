# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-dev --no-install-project

COPY . /app/
RUN uv sync --frozen --no-dev

EXPOSE 5004
CMD ["sh", "-c", "exec uv run gunicorn 'app:create_app()' -c gunicorn.conf.py"]
