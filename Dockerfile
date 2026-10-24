# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-dev --no-install-project

COPY . /app/
RUN uv sync --frozen --no-dev

# The small embedding model (embed.py): potion-base-8M (MIT), pinned to a
# revision and checked against its hashes.
ARG EMBED_REVISION=bf8b056651a2c21b8d2565580b8569da283cab23
RUN mkdir -p /app/models/potion-base-8M && cd /app/models/potion-base-8M \
    && /app/.venv/bin/python -c "import sys, urllib.request; [urllib.request.urlretrieve(f'https://huggingface.co/minishlab/potion-base-8M/resolve/{sys.argv[1]}/{f}', f) for f in ('model.safetensors', 'tokenizer.json')]" "$EMBED_REVISION" \
    && echo "f65d0f325faadc1e121c319e2faa41170d3fa07d8c89abd48ca5358d9a223de2  model.safetensors" | sha256sum -c - \
    && echo "e67e803f624fb4d67dea1c730d06e1067e1b14d830e2c2202569e3ef0f70bb50  tokenizer.json" | sha256sum -c -

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
