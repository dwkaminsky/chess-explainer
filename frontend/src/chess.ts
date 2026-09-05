import { Chess, DEFAULT_POSITION, type Square } from "chess.js";

export const START_FEN = DEFAULT_POSITION;
export const PIECE_NAMES = {
  p: "pawn",
  n: "knight",
  b: "bishop",
  r: "rook",
  q: "queen",
  k: "king",
};
export const EXAMPLES = [
  {
    name: "Italian Game",
    category: "Opening",
    description: "Development & balance",
    fen: "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
    piece: "wn",
  },
  {
    name: "The isolated pawn",
    category: "Middlegame",
    description: "Pawn structure & open files",
    fen: "r2q1rk1/pp2bppp/2n1bn2/8/3P4/2N1BN2/PP2BPPP/R2Q1RK1 w - - 0 10",
    piece: "wp",
  },
  {
    name: "A pawn ahead",
    category: "Endgame",
    description: "Material & passed pawns",
    fen: "6k1/5ppp/8/8/3P4/8/5PPP/6K1 w - - 0 1",
    piece: "wk",
  },
];

export function parsePosition(input: string): Chess {
  const fen = input.trim().replace(/\s+/g, " ");
  if (fen.split(" ").length !== 6)
    throw new Error(
      "A FEN needs all six fields: pieces, turn, castling, en passant, halfmove clock, and move number.",
    );
  try {
    return new Chess(fen);
  } catch {
    throw new Error(
      "That FEN is not valid. Check the eight ranks, one king per side, and the remaining fields.",
    );
  }
}

export function boardSquares(flipped: boolean): Square[] {
  const squares = Array.from(
    { length: 64 },
    (_, index) =>
      `${"abcdefgh"[index % 8]}${8 - Math.floor(index / 8)}` as Square,
  );
  return flipped ? squares.reverse() : squares;
}

export function fileSquares(file: string): Square[] {
  return Array.from({ length: 8 }, (_, rank) => `${file}${rank + 1}` as Square);
}

export function sideName(side: "w" | "b"): string {
  return side === "w" ? "White" : "Black";
}
