# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-dev --no-install-project

COPY . /app/
RUN uv sync --frozen --no-dev

# Runs as uid 10001, like the dashboard; the data folder (instance/, mounted
# from the Pi) must belong to it.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/instance && chown app:app /app/instance
USER app
ENV HOME=/tmp

EXPOSE 8000
# Unhealthy when no feed has answered for an hour (see /healthz).
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --start-interval=5s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
# Straight from the virtualenv: `uv run` would sync the dev tools on every start.
CMD ["/app/.venv/bin/gunicorn", "app:create_app()", "-c", "gunicorn.conf.py"]
