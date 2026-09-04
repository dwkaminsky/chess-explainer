# syntax=docker/dockerfile:1.7
# The Python base, PostgreSQL image (compose.yaml), and Stockfish Debian
# package are all intentionally version-pinned.  The test stage reuses the
# exact runtime filesystem and only adds test dependencies and sources.
FROM python:3.12.5-slim-bookworm@sha256:c24c34b502635f1f7c4e99dc09a2cbd85d480b7dcfd077198c6b5af138906390 AS runtime

ARG STOCKFISH_VERSION=15.1-4

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/usr/games:${PATH}" \
    STOCKFISH_PATH=/usr/games/stockfish

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        "stockfish=${STOCKFISH_VERSION}" \
    && test -x /usr/games/stockfish \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.runtime.lock /app/requirements.runtime.lock
RUN python -m pip install --no-cache-dir --require-hashes --requirement /app/requirements.runtime.lock

COPY pyproject.toml README.md /app/
COPY alembic.ini /app/alembic.ini
COPY app /app/app
COPY migrations /app/migrations

# Run directly as PID 1 so SIGTERM reaches the worker and Stockfish cleanup;
# the worker's bounded watchdog also guarantees shutdown within its grace.
STOPSIGNAL SIGTERM

FROM runtime AS test

COPY requirements.test.lock /app/requirements.test.lock
RUN python -m pip install --no-cache-dir --require-hashes --requirement /app/requirements.test.lock
COPY tests /app/tests

CMD ["pytest", "-q"]
