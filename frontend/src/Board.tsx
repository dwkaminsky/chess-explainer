import { useEffect, useRef, useState } from "react";
import { Chess, type Square, type PieceSymbol } from "chess.js";
import { boardSquares, PIECE_NAMES, sideName } from "./chess";

export function Piece({
  code,
  className = "",
}: {
  code: string;
  className?: string;
}) {
  return (
    <img
      className={`piece ${className}`}
      src={`/pieces/${code}.svg`}
      alt=""
      draggable={false}
    />
  );
}

export default function Board({
  fen,
  flipped,
  highlights,
  lastMove,
  onMove,
}: {
  fen: string;
  flipped: boolean;
  highlights: string[];
  lastMove?: { from: string; to: string };
  onMove: (from: Square, to: Square, promotion?: PieceSymbol) => void;
}) {
  const chess = new Chess(fen);
  const squares = boardSquares(flipped);
  const [selected, setSelected] = useState<Square | null>(null);
  const [focusSquare, setFocusSquare] = useState<Square>("e2");
  const [promotion, setPromotion] = useState<{
    from: Square;
    to: Square;
  } | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const destinations = selected
    ? chess.moves({ square: selected, verbose: true })
    : [];

  useEffect(() => {
    setSelected(null);
    setPromotion(null);
  }, [fen]);
  useEffect(() => {
    if (promotion) dialog.current?.showModal();
    else dialog.current?.close();
  }, [promotion]);

  function choose(square: Square) {
    setFocusSquare(square);
    if (square === selected) {
      setSelected(null);
      return;
    }
    const move = destinations.find((candidate) => candidate.to === square);
    if (selected && move) {
      if (move.isPromotion()) setPromotion({ from: selected, to: square });
      else onMove(selected, square);
      return;
    }
    setSelected(chess.get(square)?.color === chess.turn() ? square : null);
  }

  function moveFocus(index: number, key: string) {
    const offsets: Record<string, number> = {
      ArrowLeft: -1,
      ArrowRight: 1,
      ArrowUp: -8,
      ArrowDown: 8,
    };
    const next = index + offsets[key];
    if (
      next < 0 ||
      next >= 64 ||
      ((key === "ArrowLeft" || key === "ArrowRight") &&
        Math.floor(next / 8) !== Math.floor(index / 8))
    )
      return;
    setFocusSquare(squares[next]);
    document.getElementById(`square-${squares[next]}`)?.focus();
  }

  return (
    <>
      <div
        className="chessboard"
        role="group"
        aria-label={`Chessboard, ${sideName(chess.turn())} to move. Use arrow keys to navigate and Enter to select a piece and destination.`}
      >
        {squares.map((square, index) => {
          const piece = chess.get(square);
          const legal = destinations.some((move) => move.to === square);
          const checked =
            piece?.type === "k" &&
            piece.color === chess.turn() &&
            chess.isCheck();
          const description = piece
            ? `${sideName(piece.color)} ${PIECE_NAMES[piece.type]}`
            : "empty";
          return (
            <button
              type="button"
              id={`square-${square}`}
              key={square}
              tabIndex={focusSquare === square ? 0 : -1}
              className={`square ${(Math.floor(index / 8) + (index % 8)) % 2 ? "dark" : "light"} ${selected === square ? "selected" : ""} ${highlights.includes(square) ? "highlighted" : ""} ${lastMove && (lastMove.from === square || lastMove.to === square) ? "last-move" : ""} ${checked ? "in-check" : ""}`}
              aria-label={`${square}, ${description}${legal ? ", legal destination" : ""}${checked ? ", in check" : ""}`}
              aria-pressed={selected === square}
              onClick={() => choose(square)}
              onFocus={() => setFocusSquare(square)}
              onKeyDown={(event) => {
                if (event.key.startsWith("Arrow")) {
                  event.preventDefault();
                  moveFocus(index, event.key);
                }
                if (event.key === "Escape") setSelected(null);
              }}
            >
              {index % 8 === 0 && (
                <span className="rank" aria-hidden="true">
                  {square[1]}
                </span>
              )}
              {piece && <Piece code={`${piece.color}${piece.type}`} />}
              {legal && (
                <span
                  aria-hidden="true"
                  className={`legal-marker ${piece ? "capture" : ""}`}
                />
              )}
              {index >= 56 && (
                <span className="file" aria-hidden="true">
                  {square[0]}
                </span>
              )}
            </button>
          );
        })}
      </div>
      <dialog
        ref={dialog}
        className="promotion-dialog"
        aria-labelledby="promotion-title"
        onCancel={() => setPromotion(null)}
        onClose={() => setPromotion(null)}
      >
        <h2 id="promotion-title">Choose your promotion</h2>
        <p>Your pawn has reached the last rank.</p>
        <div className="promotion-options">
          {(["q", "r", "b", "n"] as const).map((type) => (
            <button
              key={type}
              autoFocus={type === "q"}
              aria-label={`Promote to ${PIECE_NAMES[type]}`}
              onClick={() => {
                if (promotion) onMove(promotion.from, promotion.to, type);
                setPromotion(null);
              }}
            >
              <Piece code={`${chess.turn()}${type}`} />
              <span>{PIECE_NAMES[type]}</span>
            </button>
          ))}
        </div>
        <button className="text-button" onClick={() => setPromotion(null)}>
          Cancel
        </button>
      </dialog>
    </>
  );
}
