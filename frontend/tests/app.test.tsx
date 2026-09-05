import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import { analyzePosition, AnalysisError, type TaskResult } from "../src/api";
import { EXAMPLES, START_FEN } from "../src/chess";
import { completed } from "./fixtures";

vi.mock("../src/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api")>()),
  analyzePosition: vi.fn(),
}));
const analyze = vi.mocked(analyzePosition);
beforeEach(() => {
  analyze.mockReset();
});
const fenField = () => screen.getByRole("textbox", { name: "FEN position" });
const square = (name: string) =>
  screen.getByRole("button", { name: new RegExp(`^${name},`) });
async function showResult(result = completed) {
  analyze.mockResolvedValue(result);
  const user = userEvent.setup();
  render(<App />);
  await user.click(
    screen.getByRole("button", { name: /Endgame A pawn ahead/ }),
  );
  await user.click(screen.getByRole("button", { name: "Analyze position" }));
  await screen.findByText("The position, explained.");
  return user;
}

describe("position workspace", () => {
  it("starts with a real board and no fabricated score or explanation", () => {
    render(<App />);
    expect(fenField()).toHaveValue(EXAMPLES[0].fen);
    expect(
      screen.getByRole("img", { name: /Awaiting analysis/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("The position, explained."),
    ).not.toBeInTheDocument();
    expect(analyze).not.toHaveBeenCalled();
  });
  it("loads and resets positions, rejecting invalid FEN without changing the board", async () => {
    const user = userEvent.setup();
    render(<App />);
    fireEvent.change(fenField(), { target: { value: "bad fen" } });
    await user.click(screen.getByRole("button", { name: "Load" }));
    expect(fenField()).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("six fields");
    expect(square("c4")).toHaveAccessibleName("c4, White bishop");
    fireEvent.change(fenField(), { target: { value: EXAMPLES[2].fen } });
    await user.click(screen.getByRole("button", { name: "Load" }));
    expect(square("d4")).toHaveAccessibleName("d4, White pawn");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Reset to starting position" }),
    );
    expect(fenField()).toHaveValue(START_FEN);
  });
  it("navigates move history and discards a future branch after a different move", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(
      screen.getByRole("button", { name: "Reset to starting position" }),
    );
    await user.click(square("e2"));
    await user.click(square("e4"));
    const e4 = (fenField() as HTMLTextAreaElement).value;
    await user.click(square("e7"));
    await user.click(square("e5"));
    await user.click(screen.getByRole("button", { name: "Previous position" }));
    expect(fenField()).toHaveValue(e4);
    await user.click(screen.getByRole("button", { name: "First position" }));
    expect(fenField()).toHaveValue(START_FEN);
    await user.click(screen.getByRole("button", { name: "Next position" }));
    expect(fenField()).toHaveValue(e4);
    await user.click(square("c7"));
    await user.click(square("c5"));
    expect(
      screen.getByRole("button", { name: "Next position" }),
    ).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "First position" }));
    await user.click(screen.getByRole("button", { name: "Latest position" }));
    expect(square("c5")).toHaveAccessibleName("c5, Black pawn");
    expect(square("e7")).toHaveAccessibleName("e7, Black pawn");
  });
  it("flips the board without changing its position or resubmitting analysis", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Flip board" }));
    expect(
      within(screen.getByRole("group", { name: /Chessboard/ })).getAllByRole(
        "button",
      )[0],
    ).toHaveAccessibleName("h1, White rook");
    expect(fenField()).toHaveValue(EXAMPLES[0].fen);
    expect(analyze).not.toHaveBeenCalled();
  });
  it("copies the visible board FEN even if the input has uncommitted edits", async () => {
    const user = userEvent.setup();
    render(<App />);
    const write = vi
      .spyOn(navigator.clipboard, "writeText")
      .mockResolvedValue();
    fireEvent.change(fenField(), { target: { value: "unloaded draft" } });
    await user.click(screen.getByRole("button", { name: "Copy FEN" }));
    expect(write).toHaveBeenCalledWith(EXAMPLES[0].fen);
    expect(screen.getByText("FEN copied to clipboard.")).toBeInTheDocument();
  });
  it("shows and closes the explanatory help", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "How it works" }));
    expect(
      screen.getByText(/A FEN does not include earlier moves/),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Close help" }));
    expect(
      screen.queryByText(/A FEN does not include earlier moves/),
    ).not.toBeInTheDocument();
  });

  it("selects the visible board FEN when clipboard access is denied", async () => {
    const user = userEvent.setup();
    render(<App />);
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(
      new Error("Denied"),
    );
    fireEvent.change(fenField(), { target: { value: "unloaded draft" } });
    await user.click(screen.getByRole("button", { name: "Copy FEN" }));
    await waitFor(() => expect(fenField()).toHaveFocus());
    expect(fenField()).toHaveValue(EXAMPLES[0].fen);
    const field = fenField() as HTMLTextAreaElement;
    expect(field.selectionStart).toBe(0);
    expect(field.selectionEnd).toBe(EXAMPLES[0].fen.length);
  });
});

describe("analysis integration and result freshness", () => {
  it("submits the loaded position and renders the real evaluation and explanation", async () => {
    await showResult();
    expect(analyze.mock.calls[0][0]).toBe(EXAMPLES[2].fen);
    expect(
      within(
        screen.getByRole("region", { name: "Engine evaluation" }),
      ).getByText("+1.23"),
    ).toBeVisible();
    expect(screen.getByText(completed.explanation!)).toBeVisible();
    expect(
      screen.getByRole("img", { name: /Engine favors White/ }),
    ).toBeInTheDocument();
  });
  it("shows material, highlights pawn squares and full files, and clears highlights across tabs", async () => {
    const user = await showResult();
    await user.click(screen.getByRole("tab", { name: "Material" }));
    expect(screen.getByRole("table")).toBeVisible();
    expect(screen.getByText("White +1")).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "Pawns" }));
    await user.click(
      screen.getAllByRole("button", { name: "Highlight d4" })[0],
    );
    expect(square("d4")).toHaveClass("highlighted");
    await user.click(screen.getByRole("tab", { name: "Files" }));
    expect(square("d4")).not.toHaveClass("highlighted");
    await user.click(screen.getByRole("button", { name: "Highlight a-file" }));
    expect(document.querySelectorAll(".square.highlighted")).toHaveLength(8);
    await user.click(screen.getByRole("button", { name: "Highlight a-file" }));
    expect(document.querySelectorAll(".square.highlighted")).toHaveLength(0);
  });
  it("supports keyboard tab navigation", async () => {
    const user = await showResult();
    screen.getByRole("tab", { name: "Overview" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Material" })).toHaveFocus();
    expect(screen.getByRole("table")).toBeVisible();
    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Files" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await user.keyboard("{Home}");
    expect(screen.getByText(completed.explanation!)).toBeVisible();
  });
  it("clears a completed result immediately after making a move", async () => {
    const user = await showResult();
    await user.click(square("d4"));
    await user.click(square("d5"));
    expect(screen.queryByText(completed.explanation!)).not.toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /Awaiting analysis/ }),
    ).toBeInTheDocument();
  });
  it("ignores a late response after another position is loaded", async () => {
    let finish!: (result: TaskResult) => void;
    analyze.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Analyze position" }));
    const signal = analyze.mock.calls[0][1].signal;
    await user.click(
      screen.getByRole("button", { name: /Endgame A pawn ahead/ }),
    );
    expect(signal.aborted).toBe(true);
    await act(async () => finish(completed));
    expect(screen.queryByText(completed.explanation!)).not.toBeInTheDocument();
    expect(fenField()).toHaveValue(EXAMPLES[2].fen);
  });
  it("prevents duplicate submission and cancels pending work", async () => {
    analyze.mockImplementation(() => new Promise(() => {}));
    const user = userEvent.setup();
    render(<App />);
    await user.dblClick(
      screen.getByRole("button", { name: "Analyze position" }),
    );
    expect(analyze).toHaveBeenCalledTimes(1);
    expect(
      screen.getByRole("button", { name: /Connecting to the engine/ }),
    ).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Cancel analysis" }));
    expect(analyze.mock.calls[0][1].signal.aborted).toBe(true);
    expect(
      screen.getByRole("button", { name: "Analyze position" }),
    ).toBeEnabled();
  });
  it("retries a recoverable polling error using the existing task ID", async () => {
    analyze
      .mockRejectedValueOnce(
        new AnalysisError("Connection interrupted", completed.task_id),
      )
      .mockResolvedValueOnce(completed);
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Analyze position" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Connection interrupted",
    );
    await user.click(
      screen.getByRole("button", { name: "Try analysis again" }),
    );
    await waitFor(() =>
      expect(screen.getByText(completed.explanation!)).toBeVisible(),
    );
    expect(analyze.mock.calls[1][1].taskId).toBe(completed.task_id);
  });
  it("renders legacy results without crashing and keeps missing facts explicit", async () => {
    const user = await showResult({
      ...completed,
      facts: null,
      explanation: null,
      explanation_version: null,
    });
    expect(screen.getByText(/no factual explanation/)).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "Pawns" }));
    expect(screen.getByText(/No factual details were saved/)).toBeVisible();
  });
  it("renders explanation text literally, including untrusted markup", async () => {
    const explanation = "<img src=x onerror=alert(1)> pawn";
    await showResult({ ...completed, explanation });
    expect(screen.getByText(explanation)).toBeVisible();
    expect(document.querySelector(".explanation-prose img")).toBeNull();
  });
});
