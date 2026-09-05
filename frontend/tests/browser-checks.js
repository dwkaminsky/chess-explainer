// Run from the repository root after opening the app with playwright-cli:
// playwright-cli run-code --filename frontend/tests/browser-checks.js
async function browserChecks(page) {
  const checks = [];
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const assert = (condition, message) => {
    if (!condition) throw new Error(message);
    checks.push(message);
  };
  const button = (name) => page.getByRole("button", { name, exact: true });
  const square = (name) =>
    page.getByRole("button", { name: new RegExp(`^${name},`) });
  const input = page.getByRole("textbox", { name: "FEN position" });
  const load = async (fen) => {
    await input.fill(fen);
    await button("Load").click();
  };
  const complete = async () => {
    await page
      .getByRole("button", { name: /^Analyze (position|again)$/ })
      .click();
    await page
      .getByRole("heading", { name: "The position, explained." })
      .waitFor({ timeout: 20000 });
  };
  const audit = async (name) => {
    await page.addScriptTag({
      path: "frontend/node_modules/axe-core/axe.min.js",
    });
    const violations = await page.evaluate(async () =>
      (
        await axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21aa"] })
      ).violations.map((v) => ({
        id: v.id,
        targets: v.nodes.map((n) => n.target),
      })),
    );
    assert(
      violations.length === 0,
      `${name}: no WCAG A/AA violations ${JSON.stringify(violations)}`,
    );
  };
  await page.reload();
  await page.setViewportSize({ width: 1440, height: 900 });
  await page
    .getByRole("heading", { name: "Italian Game", exact: true })
    .waitFor();
  await page.evaluate(() => document.fonts.ready);
  assert(
    (await page.locator(".square").count()) === 64,
    "All 64 board squares render",
  );
  const cta = await button("Analyze position").boundingBox();
  assert(
    cta.y + cta.height <= 900,
    "Desktop analyze control fits in first viewport",
  );
  await audit("Initial desktop");
  await complete();
  assert(
    (await page.locator(".explanation-prose").innerText()).includes(
      "same material",
    ),
    "Opening receives factual explanation from real backend",
  );
  await page.getByRole("button", { name: /Endgame A pawn ahead/ }).click();
  await complete();
  assert(
    (await page.locator(".explanation-prose").innerText()).includes(
      "d4-pawn is isolated and passed",
    ),
    "Endgame explanation matches the real board",
  );
  await audit("Completed overview");
  await page.getByRole("tab", { name: "Material", exact: true }).click();
  assert(
    await page.getByText("White +1", { exact: true }).isVisible(),
    "Material comparison shows White’s extra pawn",
  );
  await audit("Material details");
  await page.getByRole("tab", { name: "Pawns", exact: true }).click();
  await page
    .getByRole("button", { name: "Highlight d4", exact: true })
    .first()
    .click();
  assert(
    (await page.locator("#square-d4.highlighted").count()) === 1,
    "Pawn fact highlights its square",
  );
  await audit("Pawn details");
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await button("Highlight a-file").click();
  assert(
    (await page.locator(".square.highlighted").count()) === 8,
    "File fact highlights all eight squares",
  );
  await audit("File details");
  await button("Flip board").click();
  assert(
    (await page.locator(".square").first().getAttribute("id")) === "square-h1",
    "Flip places h1 at the top left",
  );
  assert(
    (await page.locator(".square.highlighted").count()) === 8,
    "Flipping preserves fact highlights",
  );
  await button("Flip board").click();
  await page.getByRole("tab", { name: "Overview" }).click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.evaluate(() => {
    document.activeElement?.blur();
    window.scrollTo(0, 0);
  });
  await page.screenshot({
    path: "output/playwright/desktop-analysis.png",
    fullPage: true,
  });
  await square("d4").click();
  await square("d5").click();
  assert(
    (await page.locator(".explanation-prose").count()) === 0,
    "Moving immediately clears stale explanations",
  );
  await button("Previous position").click();
  assert(
    (await input.inputValue()).startsWith("6k1/5ppp/8/8/3P4"),
    "Previous position restores the board",
  );
  await input.fill("not a position");
  await button("Load").click();
  assert(
    await page.getByRole("alert").isVisible(),
    "Invalid FEN displays an inline error",
  );
  assert(
    (await square("d4").getAttribute("aria-label")).includes("White pawn"),
    "Invalid FEN preserves the loaded board",
  );
  await load("7k/P7/6K1/8/8/8/8/8 w - - 0 1");
  await square("a7").click();
  await square("a8").click();
  assert(
    await page.getByRole("dialog").isVisible(),
    "Promotion opens a native modal",
  );
  assert(
    await button("Promote to queen").evaluate(
      (element) => document.activeElement === element,
    ),
    "Promotion dialog focuses its first choice",
  );
  await page.keyboard.press("Escape");
  assert(
    (await page.getByRole("dialog").count()) === 0,
    "Escape dismisses promotion without a move",
  );
  await square("a8").click();
  await button("Promote to knight").click();
  assert(
    (await square("a8").getAttribute("aria-label")).includes("White knight"),
    "Underpromotion updates the board",
  );
  await load("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1");
  await square("e1").click();
  await square("g1").click();
  assert(
    (await square("f1").getAttribute("aria-label")).includes("White rook"),
    "Castling moves the rook as well as the king",
  );
  await load("7k/8/8/3pP3/8/8/8/6K1 w - d6 0 1");
  await square("e5").click();
  await square("d6").click();
  assert(
    (await square("d5").getAttribute("aria-label")).includes("empty"),
    "En passant removes the captured pawn",
  );
  await load("7k/6Q1/5K2/8/8/8/8/8 b - - 0 1");
  await complete();
  assert(
    await page.getByText("Checkmate · White wins", { exact: true }).isVisible(),
    "Terminal checkmate renders correctly from the backend",
  );
  await load("7k/5K2/6Q1/8/8/8/8/8 b - - 0 1");
  await complete();
  assert(
    (await page.locator(".explanation-prose").innerText())
      .toLowerCase()
      .includes("stalemate"),
    "Terminal stalemate renders correctly from the backend",
  );
  await page.getByRole("button", { name: /Endgame A pawn ahead/ }).click();
  await complete();
  for (const width of [320, 390, 768, 1024]) {
    await page.setViewportSize({ width, height: 900 });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
      `No horizontal overflow at ${width}px`,
    );
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await audit("Mobile overview");
  await page.evaluate(() => {
    document.activeElement?.blur();
    window.scrollTo(0, 0);
  });
  await page.screenshot({
    path: "output/playwright/mobile-analysis.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.evaluate(() => {
    document.documentElement.style.fontSize = "200%";
  });
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
    "No horizontal overflow at 200% text size",
  );
  await page.evaluate(() => {
    document.activeElement?.blur();
    window.scrollTo(0, 0);
  });
  await page.screenshot({
    path: "output/playwright/text-200-percent.png",
    fullPage: true,
  });
  await page.evaluate(() => {
    document.documentElement.style.fontSize = "";
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.evaluate(() => window.scrollTo(0, 0));
  assert(
    errors.length === 0,
    `No JavaScript errors: ${JSON.stringify(errors)}`,
  );
  return { passed: checks.length, checks };
}
