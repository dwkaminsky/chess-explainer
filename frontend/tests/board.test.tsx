import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import Board from "../src/Board";
import { boardSquares, EXAMPLES, parsePosition, START_FEN } from "../src/chess";

describe("position input and chess rules", () => {
  it.each(EXAMPLES)(
    "loads the $name example as a legal position",
    (example) => {
      expect(parsePosition(example.fen).fen()).toBe(example.fen);
    },
  );
  it("requires exactly six fields and rejects impossible board layouts", () => {
    expect(() => parsePosition("8/8/8/8/8/8/8/8")).toThrow("six fields");
    expect(() => parsePosition("8/8/8/8/8/8/8/8 w - - 0 1")).toThrow(
      "not valid",
    );
    expect(parsePosition(`  ${START_FEN.replaceAll(" ", "   ")}  `).fen()).toBe(
      START_FEN,
    );
  });
  it("orients all 64 squares correctly", () => {
    expect(boardSquares(false)).toHaveLength(64);
    expect(new Set(boardSquares(false)).size).toBe(64);
    expect(boardSquares(false)[0]).toBe("a8");
    expect(boardSquares(true)[0]).toBe("h1");
    expect(boardSquares(false)[63]).toBe("h1");
    expect(boardSquares(true)[63]).toBe("a8");
  });
});

function board(fen = START_FEN, onMove = vi.fn()) {
  render(<Board fen={fen} flipped={false} highlights={[]} onMove={onMove} />);
  return onMove;
}
describe("accessible board interaction", () => {
  it("shows only legal destinations and only allows the side to move", async () => {
    const user = userEvent.setup();
    const move = board();
    await user.click(screen.getByRole("button", { name: "e7, Black pawn" }));
    expect(
      screen.queryAllByRole("button", { name: /legal destination/ }),
    ).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "e2, White pawn" }));
    expect(
      screen.getAllByRole("button", { name: /legal destination/ }),
    ).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "e5, empty" }));
    expect(move).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "e2, White pawn" }));
    await user.click(screen.getByRole("button", { name: /e4, empty/ }));
    expect(move).toHaveBeenCalledWith("e2", "e4");
  });
  it("has one tab stop and supports arrow keys, selection, and Escape", async () => {
    const user = userEvent.setup();
    board();
    expect(document.querySelectorAll('.square[tabindex="0"]')).toHaveLength(1);
    screen.getByRole("button", { name: "e2, White pawn" }).focus();
    await user.keyboard("{Enter}{ArrowUp}");
    expect(screen.getByRole("button", { name: /e3, empty/ })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(
      screen.queryAllByRole("button", { name: /legal destination/ }),
    ).toHaveLength(0);
    screen.getByRole("button", { name: "a8, Black rook" }).focus();
    await user.keyboard("{ArrowLeft}{ArrowUp}");
    expect(
      screen.getByRole("button", { name: "a8, Black rook" }),
    ).toHaveFocus();
  });
  it("clears a selected piece when the position is replaced", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <Board
        fen={START_FEN}
        flipped={false}
        highlights={[]}
        onMove={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "e2, White pawn" }));
    rerender(
      <Board
        fen={EXAMPLES[2].fen}
        flipped={false}
        highlights={[]}
        onMove={vi.fn()}
      />,
    );
    expect(
      screen.queryAllByRole("button", { name: /legal destination/ }),
    ).toHaveLength(0);
  });
  it("offers all four promotion choices and preserves underpromotion", async () => {
    const user = userEvent.setup();
    const move = board("7k/P7/6K1/8/8/8/8/8 w - - 0 1");
    await user.click(screen.getByRole("button", { name: "a7, White pawn" }));
    await user.click(screen.getByRole("button", { name: /a8, empty/ }));
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(screen.getAllByRole("button", { name: /Promote to/ })).toHaveLength(
      4,
    );
    await user.click(screen.getByRole("button", { name: "Promote to knight" }));
    expect(move).toHaveBeenCalledWith("a7", "a8", "n");
  });
  it("cancels a promotion without moving the pawn", async () => {
    const user = userEvent.setup();
    const move = board("7k/P7/6K1/8/8/8/8/8 w - - 0 1");
    await user.click(screen.getByRole("button", { name: "a7, White pawn" }));
    await user.click(screen.getByRole("button", { name: /a8, empty/ }));
    fireEvent(
      screen.getByRole("dialog"),
      new Event("cancel", { bubbles: true }),
    );
    expect(move).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it.each([
    ["r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "e1", "g1", "O-O"],
    ["7k/8/8/3pP3/8/8/8/6K1 w - d6 0 1", "e5", "d6", "exd6"],
  ])("allows special moves in %s", async (fen, from, to, san) => {
    const user = userEvent.setup();
    const chess = new Chess(fen);
    const move = board(
      fen,
      vi.fn((a, b) => chess.move({ from: a, to: b })),
    );
    await user.click(
      screen.getByRole("button", { name: new RegExp(`^${from},`) }),
    );
    await user.click(
      screen.getByRole("button", { name: new RegExp(`^${to},`) }),
    );
    expect(move).toHaveBeenCalledWith(from, to);
    expect(chess.history()).toEqual([san]);
  });
});
