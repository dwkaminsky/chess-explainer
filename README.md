# chess-explainer

This MVP accepts a standard-chess FEN, evaluates it asynchronously with the
Stockfish binary in the application image, and exposes only `POST /tasks` and
`GET /tasks/{task_id}`. PostgreSQL is the durable queue and stores results
across ordinary restarts.

## Requirements and configuration

The application runtime requires only Docker Engine/Desktop and the Docker
Compose plugin. Python, PostgreSQL, Stockfish, and application packages are
provided by the images; do not install them on the host. The image uses
digest-pinned Python `3.12.5-slim-bookworm` and PostgreSQL `16.4-alpine`
images, plus Debian's pinned Stockfish package `15.1-4`. Python dependencies,
including transitive dependencies, are resolved for Python 3.12 with universal
platform markers and SHA-256 hashes in `requirements.runtime.lock` and
`requirements.test.lock`.
The image installs them with pip's `--require-hashes` mode; the aggregate
`requirements.lock` contains the runtime plus test resolution.

Create the local configuration file once:

```sh
cp .env.example .env
```

The example credentials are for local use only. Change `POSTGRES_PASSWORD`
and the matching `DATABASE_URL` before exposing the API beyond localhost.

## Build and start

```sh
docker compose up --build -d
docker compose ps
```

Compose starts a healthy private PostgreSQL service, runs the one-shot
`migrate` service, and starts `api` and `worker` only after migration succeeds.
The API is published at `127.0.0.1:8000`; PostgreSQL has no host port.

To run the migration explicitly (for example, after changing the migration
image), use:

```sh
docker compose run --rm migrate
```

## Logs and API usage

```sh
docker compose logs -f api worker
```

Submit a position. The response is `202 Accepted` only after the task row has
been committed, and includes a `Location` header:

```sh
curl -i -X POST http://127.0.0.1:8000/tasks \
  -H 'content-type: application/json' \
  --data '{"fen":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"}'
```

Poll the returned UUID about once per second, replacing the placeholder:

```sh
curl -i http://127.0.0.1:8000/tasks/<task_id>
```

Stop polling when `status` is `completed` or `failed`. Ordinary results use
White-perspective pawn units (`34` centipawns is `0.34`); mate results use a
separate `mate` object and keep `evaluation` as `null`. Poll responses are
marked `Cache-Control: no-store`.

## Tests

Tests run in containers against a separate PostgreSQL service and named volume;
the normal `chess-data` volume is not used by the test stack. The in-process
worker uses the real Stockfish binary for the end-to-end path and the
integration checks avoid asserting a time-dependent exact centipawn score.

```sh
docker compose --profile test build test
docker compose --profile test run --rm test
```

The test profile brings up only `test-db` and `test-migrate` as dependencies.
Integration tests use the ASGI application and run one real worker attempt
in-process, avoiding a background worker racing the queue-isolation fixtures.
It sets `RUN_CONTAINER_INTEGRATION=1` and uses `chess-explainer-test-data`, so
tests cannot touch normal task data.

## Stop and restart

```sh
docker compose stop
docker compose start
docker compose restart api worker
```

`stop`, `start`, and `restart` preserve the named `chess-explainer-data`
volume and therefore preserve task IDs and results. API and worker have
restart policies, a 15-second shutdown grace period, and a one-shot migration
service that is never restarted after success.

To stop and remove containers without deleting task data:

```sh
docker compose down
```

Destructive cleanup is separate and deletes all normal task data (and cannot
be undone from this application):

```sh
docker compose down --volumes
```

The test volume is also disposable, but deleting it removes test data only.
This is destructive; verify the volume name before running it:

```sh
docker volume rm chess-explainer-test-data
```
