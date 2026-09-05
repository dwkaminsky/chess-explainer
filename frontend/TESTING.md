# Frontend verification

Run `npm ci`, `npm test`, and `npm run build` from this directory. The same
tests run with `docker compose --profile test run --rm --build frontend-test`
from the repository root. Tests do not require a live backend or touch saved tasks.

## Browser acceptance checks

Start the complete application with `docker compose up --build -d`. From the
repository root, open the production UI with Playwright CLI and run the checked-in
acceptance script:

```sh
mkdir -p output/playwright
npx --yes --package @playwright/cli playwright-cli -s=chess open http://127.0.0.1:8080
npx --yes --package @playwright/cli playwright-cli -s=chess run-code --filename frontend/tests/browser-checks.js
npx --yes --package @playwright/cli playwright-cli -s=chess run-code --filename frontend/tests/browser-failures.js
npx --yes --package @playwright/cli playwright-cli -s=chess close
```

The CLI requires a supported installed browser. Add `--headed` to `open` for a
visible session. Each script returns its passed assertions and throws on a failure.
Run against a stable production build to avoid development hot reload interrupting
an active test. The scripts also work against an already-open Vite development URL.

`browser-checks.js` uses the real API and Stockfish. It submits a small number of
ordinary analysis tasks. For complete isolation, use a separate Compose project
**and override both named volume names and published ports**; the base Compose file
intentionally uses fixed persistent volume names, so `-p` alone does not isolate data.

It verifies real opening/endgame explanations, material counts, square/file
highlights, board orientation, move history, invalid FEN, castling, en passant,
underpromotion and native-dialog keyboard behavior, terminal mate/stalemate,
320/390/768/1024px layouts, 200% text enlargement, and browser errors. Axe audits
the initial and completed states and all four detail views against WCAG 2 A/AA
and WCAG 2.1 AA rules. Automated audits supplement manual visual and keyboard checks.

`browser-failures.js` intercepts task requests in the browser and restores the
routes afterward. It verifies queued/running progress, cancellation, late results,
HTTP errors, retries, failed workers, mate scores, legacy responses, and malformed
responses without relying on a broken live backend.

Screenshots are written under `output/playwright/` (gitignored). Inspect the
desktop, mobile, and enlarged-text screenshots for clipped content, overlapping
controls, and readable explanations. Browser acceptance checks are manual; CI
runs the deterministic regression suite and production build.

## Observed backend limitation

Live QA can encounter `ENGINE_SCORE_MISSING` when the existing Stockfish adapter
receives no exact, usable score within its timed search (bounded-only scores are
intentionally rejected). The worker retries according to the parent PR's policy
and can still return a failed task. The frontend reports this failure and
**Try analysis again** submits a new task. This PR preserves that engine policy;
a real-engine acceptance run can therefore fail even while deterministic UI
checks pass. Do not replace a missing evaluation with a fabricated zero.

## Verification recorded for this PR

- 59 Vitest unit/component checks passed locally and in the frontend test image.
- All 112 parent-backend tests passed in the isolated PostgreSQL/Stockfish stack.
- TypeScript checking, Vite production compilation, formatting, and Docker builds passed.
- 33 browser acceptance assertions passed against Vite with the real backend;
  17 additional browser assertions passed with controlled error responses.
- Axe reported no WCAG A/AA violations in the initial view, completed overview,
  material, pawn, and file views, or mobile overview. Desktop, mobile, and 200%
  text screenshots were inspected manually.
- Production nginx served the compiled app, local fonts, and piece SVGs correctly;
  hashed assets used immutable caching and API responses retained status codes,
  JSON bodies, and `Cache-Control: no-store`.
- The complete browser script encountered the parent adapter's bounded-score
  failure during two production runs. The failed task was confirmed through API
  and worker logs, then reproduced directly through the unchanged adapter.
  Retrying through the production UI succeeded. Remaining production responsive
  and mobile accessibility checks passed after recovery. These runs are recorded
  as recovered failures, rather than clean full-suite passes.

Review captures: [desktop](docs/desktop.png) and [mobile](docs/mobile.png).
