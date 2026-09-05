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
The default runtime configuration is `SEARCH_TIME_SECONDS=3.0`,
`CANDIDATE_TARGET=3`, `MAX_CONTINUATION_PLIES=6`,
`HARD_ATTEMPT_TIMEOUT_SECONDS=10.0`, and `LEASE_SECONDS=30.0`.

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
The API intentionally exposes only `POST /tasks` and `GET /tasks/{task_id}`;
Swagger, ReDoc, and OpenAPI routes are disabled.

## Factual response contract

Completed responses from the upgraded worker also include `facts`,
`explanation`, and `explanation_version`. Version `1` is the first factual
contract. Queued, running, failed, and older completed rows without a saved
bundle return those fields as `null`; the existing score or mate result stays
unchanged. The worker persists the full `factual_result` internally and the
API maps it onto the response fields when a bundle exists.

The upgraded worker also persists and serves candidate search data. Completed
responses include `candidate_moves` and `candidate_analysis`; queued, running,
failed, and legacy completed rows return both as `null`.

`candidate_analysis` is the public analysis metadata plus a `version`. The
result is produced from one candidate snapshot per task, with Stockfish `MultiPV`
set from `CANDIDATE_TARGET`, and the factual explanation does not trigger any
extra engine request.

Worked example:

```json
{
  "fen": "6k1/5ppp/8/8/3P4/8/5PPP/6K1 w - - 0 1"
}
```

The corresponding factual payload excerpt is:

```json
{
  "task_id": "3a2676b5-b76d-4fdb-a58f-e2ddb692ff60",
  "status": "completed",
  "evaluation": 1.23,
  "facts": {
    "material": {
      "white": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 4},
      "black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 3},
      "white_minus_black": {"queen": 0, "rook": 0, "bishop": 0, "knight": 0, "pawn": 1}
    },
    "pawns": {
      "white": {
        "isolated": ["d4"],
        "doubled_files": {},
        "passed": ["d4"]
      },
      "black": {
        "isolated": [],
        "doubled_files": {},
        "passed": []
      }
    },
    "files": {
      "open": ["a", "b", "c", "e"],
      "semi_open": {"white": [], "black": ["d"]}
    }
  },
  "explanation": "White has one more pawn than Black. White's d4-pawn is isolated and passed. The a-, b-, c-, and e-files are open; the d-file is semi-open for Black.",
  "explanation_version": 1
}
```

The canonical candidate example is checked in at
[tests/fixtures/candidate_public_example.json](tests/fixtures/candidate_public_example.json).

The version only changes when the fact definitions, selection rules, or
templates change. Polling never rewrites historical results with newer prose.
The factual renderer is deterministic, uses the saved board only, and stays
within a short prose budget. It may describe material, pawn structure, file
status, and terminal state, but it does not recommend moves or explain Stockfish
scores causally.

The candidate collector also has a checked-in incremental transcript captured
from the pinned Docker runtime (`stockfish=15.1-4`, `MultiPV=3`, one thread,
64 MiB hash). `tests/fixtures/pinned_stockfish_15_1_candidate_transcript.json`
contains only JSON-safe report fields and metadata; its scores are a parser and
checkpoint fixture, not a promise about future live search rankings.

Candidate result details:

- `evaluation` stays in White's pawn-unit perspective.
- `mate` is present only for mate results; `evaluation` is `null` in that case.
- `candidate_moves` is a list of candidate lines; the rank-1 candidate matches
  the top-line result for the task.
- `candidate_analysis.returned_count` matches the number of candidate moves,
  while `requested_count` and `max_continuation_plies` show the search request.
- Public move records use the JSON aliases `from` and `to`.
- Terminal tasks keep `candidate_moves: []` and a terminal-position analysis;
  failed and legacy completed rows keep both candidate fields `null`.

## Tests

Tests run in containers against a separate PostgreSQL service and named volume;
the normal `chess-data` volume is not used by the test stack. The in-process
worker uses the real Stockfish binary for the end-to-end path and the
integration checks avoid asserting a time-dependent exact centipawn score.
The `test` service runs `python -m pytest -q` inside the image so the app
package is on `sys.path` in that container image.

```sh
docker compose --profile test build test-migrate test
docker compose --profile test run --rm test
```

The test profile brings up only `test-db` and `test-migrate` as dependencies.
Integration tests use the ASGI application and run one real worker attempt
in-process, avoiding a background worker racing the queue-isolation fixtures.
It sets `RUN_CONTAINER_INTEGRATION=1` and uses `chess-explainer-test-data`, so
tests cannot touch normal task data.
The release checks also cover a fresh database migrated all the way to `head`,
an existing database upgraded from `0002` to `0003` without losing old
completed rows, and a restart-style persistence round trip for completed task
rows.
The automated restart-style check is limited to database/session persistence;
for a full container restart verification, run `docker compose restart api
worker` during release validation and re-poll a completed task.

Useful release commands:

```sh
docker compose logs -f api worker migrate
docker compose run --rm migrate
docker compose run --rm migrate alembic downgrade -1
docker compose restart api worker
```

The rollback example is only for a disposable database copy; the normal data
volume is preserved by `stop`, `start`, and `restart`.

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
