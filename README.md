# chess-explainer

Explore standard-chess positions with an interactive board, Stockfish evaluations,
and factual explanations of material, pawn structure, and files. The React frontend
uses the existing asynchronous `POST /tasks` and `GET /tasks/{task_id}` API.
PostgreSQL is the durable queue and stores results across ordinary restarts.

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
Open **http://localhost:8080** for the analysis workspace. The frontend container
serves its compiled assets and proxies `/tasks` to the API on the same origin.
The API is also published at `127.0.0.1:8000`; PostgreSQL has no host port.

To run the migration explicitly (for example, after changing the migration
image), use:

```sh
docker compose run --rm migrate
```

## Analysis workspace

- Start with an example position or paste a complete, six-field FEN and select
  **Load** (or press Enter). Editing the text does not change the board until loaded.
- Click a piece and a highlighted destination to explore a legal move. Castling,
  en passant, and a choice of all four promotion pieces are supported. The arrow
  controls navigate the moves explored in this session; playing from an earlier
  position replaces its continuation. Flip, reset, and copy-FEN controls sit below
  the board.
- Select **Analyze position** to submit the visible board. The workspace displays
  queued/running progress, numeric or mate evaluations, and the explanation saved
  by the worker. Scores are from White's perspective, regardless of orientation.
- Open **Material**, **Pawns**, or **Files** for structured details. Selecting a
  square or file highlights the corresponding board locations.
- Changing the position clears its old result and stops client polling. Cancel
  also stops polling; already submitted tasks may still finish on the server.
  Transient polling failures can resume the same task. Failed or missing tasks
  start fresh when retried. Polling is bounded to 90 seconds with a 10-second
  per-request timeout.

The board supports arrow-key navigation, Enter/Space selection, and Escape to
clear a selection. Promotion uses a native modal with keyboard focus handling.
The layout adapts to mobile, respects reduced-motion preferences, and bundles
its fonts and piece artwork locally. Refreshing the page starts a new workspace;
browser position history is not persisted. The API remains the final authority
on position legality. FEN-only inputs cannot establish earlier repetition history.

The current explanation contract covers facts, not move recommendations or
strategic coaching. Legacy results with no factual bundle remain usable and are
clearly labeled.

### Frontend development and checks

The normal runtime still requires only Docker. Run the frontend's isolated
regression tests in a container:

```sh
docker compose --profile test build frontend-test
docker compose --profile test run --rm frontend-test
```

For optional local UI development, use Node.js 22.16+ (or a compatible newer LTS):

```sh
cd frontend
npm ci
npm run dev
```

Vite serves http://127.0.0.1:5173 and proxies `/tasks` to http://127.0.0.1:8000.
Set `API_PROXY_TARGET` when the backend uses a different address. No frontend
environment variable or cross-origin configuration is needed in production.

```sh
cd frontend
npm test
npm run build
```

Unit and component tests cover API contracts, timeout/cancellation/retry behavior,
stale responses, FEN validation, special chess moves, keyboard navigation, material
and board highlights, and legacy results. See [frontend/TESTING.md](frontend/TESTING.md)
for repeatable browser acceptance checks and visual QA instructions.

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

## Factual response contract

Completed responses from the upgraded worker also include `facts`,
`explanation`, and `explanation_version`. Version `1` is the first factual
contract. Queued, running, failed, and older completed rows without a saved
bundle return those fields as `null`; the existing score or mate result stays
unchanged. The worker persists the full `factual_result` internally and the
API maps it onto the response fields when a bundle exists.

Worked example:

```json
{
  "fen": "6k1/5ppp/8/8/3P4/8/5PPP/6K1 w - - 0 1"
}
```

The corresponding factual payload is:

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

The version only changes when the fact definitions, selection rules, or
templates change. Polling never rewrites historical results with newer prose.

## Tests

Tests run in containers against a separate PostgreSQL service and named volume;
the normal `chess-data` volume is not used by the test stack. The in-process
worker uses the real Stockfish binary for the end-to-end path and the
integration checks avoid asserting a time-dependent exact centipawn score.
The `test` service runs `python -m pytest -q` inside the image so the app
package is on `sys.path` in that container image.

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
