// Run using playwright-cli run-code --filename frontend/tests/browser-failures.js.
async function browserFailures(page) {
  const checks = [];
  const assert = (value, message) => {
    if (!value) throw new Error(message);
    checks.push(message);
  };
  const button = (name) => page.getByRole("button", { name, exact: true });
  const id = "00000000-0000-0000-0000-000000000001";
  const legacy = {
    task_id: id,
    status: "completed",
    evaluation: -1.25,
    facts: null,
    explanation: null,
    explanation_version: null,
  };
  let mode = "queued";
  let posts = 0;
  let gets = 0;
  let delayed = false;
  let release;
  const routePattern = /\/tasks(?:\/|$)/;
  const handler = async (route) => {
    const post = route.request().method() === "POST";
    if (post) posts++;
    else gets++;
    if (mode === "network") {
      await route.abort("failed");
      return;
    }
    if (
      mode === "submit503" ||
      mode === "invalid422" ||
      (mode === "poll503" && !post)
    ) {
      await route.fulfill({
        status: mode === "invalid422" ? 422 : 503,
        json: { detail: "fixture error" },
      });
      return;
    }
    if (post) {
      await route.fulfill({ status: 202, json: { task_id: id } });
      return;
    }
    let result = { ...legacy };
    if (mode === "queued" || mode === "running")
      result = { ...legacy, status: mode, evaluation: null };
    if (mode === "failed")
      result = {
        ...legacy,
        status: "failed",
        evaluation: null,
        error: { code: "ENGINE_TIMEOUT", message: "timed out" },
      };
    if (mode === "mate")
      result = {
        ...legacy,
        evaluation: null,
        mate: { winner: "black", moves: 2 },
      };
    if (mode === "malformed")
      result = { ...legacy, facts: { material: "invalid" } };
    if (mode === "delay") {
      delayed = true;
      await new Promise((resolve) => {
        release = resolve;
      });
    }
    try {
      await route.fulfill({ status: 200, json: result });
    } catch (error) {
      if (mode !== "delay") throw error;
    }
  };
  const reset = async (nextMode) => {
    mode = nextMode;
    await button("Reset to starting position").click();
  };
  const start = () => button("Analyze position").click();
  const waitForResult = () =>
    page.getByRole("heading", { name: "The position, explained." }).waitFor();
  await page.reload();
  await page.route(routePattern, handler);
  try {
    await start();
    await button("Position queued…").waitFor();
    assert(
      await button("Position queued…").isDisabled(),
      "Queued analysis disables duplicate submission",
    );
    mode = "running";
    await button("Analyzing your position…").waitFor();
    assert(
      await button("Cancel analysis").isVisible(),
      "Running analysis can be cancelled",
    );
    mode = "legacy";
    await waitForResult();
    assert(
      await page.getByText("Engine favors Black", { exact: true }).isVisible(),
      "Negative scores favor Black",
    );
    assert(
      await page.getByText(/no factual explanation/).isVisible(),
      "Legacy results explain the missing factual bundle",
    );
    await page.getByRole("tab", { name: "Files", exact: true }).click();
    assert(
      await page.getByText(/No factual details were saved/).isVisible(),
      "Legacy detail views remain usable",
    );

    await reset("queued");
    await start();
    await button("Position queued…").waitFor();
    await button("Cancel analysis").click();
    const cancelledGets = gets;
    await page.waitForTimeout(1200);
    assert(gets === cancelledGets, "Cancel stops subsequent network polling");
    assert(
      await button("Analyze position").isEnabled(),
      "Cancel restores the analyze control",
    );

    await reset("delay");
    await start();
    for (let attempt = 0; attempt < 20 && !delayed; attempt++)
      await page.waitForTimeout(50);
    if (!delayed) throw new Error("Expected a delayed poll request");
    await page.getByRole("button", { name: /Endgame A pawn ahead/ }).click();
    release();
    await page.waitForTimeout(200);
    assert(
      (await page.locator(".explanation-prose").count()) === 0,
      "A late response cannot replace a newly loaded position",
    );

    await reset("submit503");
    await start();
    await page.getByRole("alert").waitFor();
    assert(
      (await page.getByRole("alert").innerText()).includes("unavailable"),
      "Service errors show a helpful retry message",
    );
    mode = "legacy";
    await button("Try analysis again").click();
    await waitForResult();
    assert(
      await page.getByText("Engine favors Black", { exact: true }).isVisible(),
      "Submission recovers after service availability returns",
    );

    await reset("poll503");
    await start();
    await page.getByRole("alert").waitFor();
    const beforeRetry = posts;
    mode = "legacy";
    await button("Try analysis again").click();
    await waitForResult();
    assert(
      posts === beforeRetry,
      "Polling retries resume the same task without duplicate POSTs",
    );

    await reset("failed");
    await start();
    await page.getByRole("alert").waitFor();
    const beforeFailedRetry = posts;
    mode = "legacy";
    await button("Try analysis again").click();
    await waitForResult();
    assert(
      posts === beforeFailedRetry + 1,
      "A failed worker is retried with a new task",
    );

    await reset("invalid422");
    await start();
    await page.getByRole("alert").waitFor();
    assert(
      (await page.getByRole("alert").innerText()).includes("valid position"),
      "Server legality errors remain understandable",
    );
    await reset("network");
    await start();
    await page.getByRole("alert").waitFor();
    assert(
      (await page.getByRole("alert").innerText()).includes("connection"),
      "Network loss gives a recoverable connection error",
    );
    await reset("malformed");
    await start();
    await page.getByRole("alert").waitFor();
    assert(
      (await page.getByRole("alert").innerText()).includes(
        "unexpected response",
      ),
      "Malformed facts cannot crash the page",
    );
    await reset("mate");
    await start();
    await waitForResult();
    assert(
      await page.getByText("Black has mate in 2", { exact: true }).isVisible(),
      "Black mate scores identify the winning side",
    );
    assert(
      await page
        .getByText("Forced mate · distance measured in moves", { exact: true })
        .isVisible(),
      "Mate distances use moves rather than pawn units",
    );
    return { passed: checks.length, checks };
  } finally {
    if (release) release();
    await page.unroute(routePattern, handler);
    await page.reload();
  }
}
