import { useEffect, useRef, useState } from "react";
import { Chess, type Square, type PieceSymbol } from "chess.js";
import {
  ArrowDownUp,
  ArrowRight,
  Check,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  CircleHelp,
  Copy,
  CornerDownLeft,
  Layers3,
  Leaf,
  LoaderCircle,
  RotateCcw,
  ScanLine,
  Sparkles,
  X,
  AlertCircle,
  Columns3,
} from "lucide-react";
import Board, { Piece } from "./Board";
import Analysis from "./Analysis";
import {
  analyzePosition,
  AnalysisError,
  evaluationView,
  type TaskResult,
} from "./api";
import { EXAMPLES, START_FEN, parsePosition, sideName } from "./chess";

interface Position {
  fen: string;
  san?: string;
  from?: Square;
  to?: Square;
}
type Status =
  "idle" | "submitting" | "queued" | "running" | "completed" | "error";

export default function App() {
  const [history, setHistory] = useState<Position[]>([
    { fen: EXAMPLES[0].fen },
  ]);
  const [cursor, setCursor] = useState(0);
  const [name, setName] = useState(EXAMPLES[0].name);
  const [flipped, setFlipped] = useState(false);
  const [draft, setDraft] = useState(EXAMPLES[0].fen);
  const [fenError, setFenError] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [result, setResult] = useState<TaskResult | null>(null);
  const [error, setError] = useState("");
  const [resumeTaskId, setResumeTaskId] = useState<string>();
  const [highlights, setHighlights] = useState<string[]>([]);
  const [notice, setNotice] = useState("");
  const [help, setHelp] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const position = history[cursor];
  const chess = new Chess(position.fen);
  const busy = ["submitting", "queued", "running"].includes(status);
  const evaluation = evaluationView(result);
  const lastMove =
    position.from && position.to
      ? { from: position.from, to: position.to }
      : undefined;

  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 3500);
    return () => clearTimeout(timer);
  }, [notice]);

  function clearAnalysis() {
    controller.current?.abort();
    controller.current = null;
    setResult(null);
    setStatus("idle");
    setError("");
    setResumeTaskId(undefined);
    setHighlights([]);
  }

  function loadPosition(fen: string, title = "Custom position") {
    try {
      const loaded = parsePosition(fen);
      clearAnalysis();
      setHistory([{ fen: loaded.fen() }]);
      setCursor(0);
      setName(title);
      setDraft(loaded.fen());
      setFenError("");
    } catch (cause) {
      setFenError(
        cause instanceof Error ? cause.message : "Please enter a valid FEN.",
      );
    }
  }

  function makeMove(from: Square, to: Square, promotion?: PieceSymbol) {
    try {
      const move = chess.move({
        from,
        to,
        ...(promotion ? { promotion } : {}),
      });
      clearAnalysis();
      const next = [
        ...history.slice(0, cursor + 1),
        { fen: chess.fen(), san: move.san, from, to },
      ];
      setHistory(next);
      setCursor(next.length - 1);
      setDraft(chess.fen());
      setFenError("");
    } catch {
      setNotice("Choose a highlighted legal destination.");
    }
  }

  function navigate(next: number) {
    if (next === cursor || next < 0 || next >= history.length) return;
    clearAnalysis();
    setCursor(next);
    setDraft(history[next].fen);
    setFenError("");
  }

  async function analyze() {
    if (busy) return;
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setStatus("submitting");
    setError("");
    setResult(null);
    setHighlights([]);
    try {
      const completed = await analyzePosition(position.fen, {
        signal: current.signal,
        taskId: resumeTaskId,
        onProgress: (next) => {
          if (!current.signal.aborted) setStatus(next);
        },
      });
      if (!current.signal.aborted) {
        setResult(completed);
        setStatus("completed");
        setResumeTaskId(undefined);
      }
    } catch (cause) {
      if (current.signal.aborted) return;
      setError(
        cause instanceof Error
          ? cause.message
          : "Could not analyze this position.",
      );
      setResumeTaskId(
        cause instanceof AnalysisError ? cause.taskId : undefined,
      );
      setStatus("error");
    }
  }

  async function copyFen() {
    try {
      await navigator.clipboard.writeText(position.fen);
      setNotice("FEN copied to clipboard.");
    } catch {
      setDraft(position.fen);
      requestAnimationFrame(() => {
        input.current?.focus();
        input.current?.select();
      });
      setNotice("Select and copy the FEN from the position field.");
    }
  }

  function player(side: "w" | "b") {
    const count = chess
      .board()
      .flat()
      .filter((piece) => piece?.color === side).length;
    const active = chess.turn() === side;
    return (
      <div className="player">
        <span className={`player-avatar ${side === "w" ? "white-player" : ""}`}>
          <Piece code={`${side}k`} />
        </span>
        <span>
          <strong>{sideName(side)}</strong>
          <span>{count} pieces on the board</span>
        </span>
        {active && (
          <span className="turn-pill">
            <span />
            {chess.isCheckmate()
              ? "Checkmate"
              : chess.isStalemate()
                ? "Stalemate"
                : chess.isCheck()
                  ? "In check"
                  : "To move"}
          </span>
        )}
      </div>
    );
  }

  return (
    <>
      <a className="skip-link" href="#workspace">
        Skip to analysis workspace
      </a>
      <header className="site-header">
        <div className="header-inner">
          <a href="/" className="brand" aria-label="Chess Explainer home">
            <img src="/favicon.svg" alt="" />
            <span>
              chess<span className="brand-light">explainer</span>
              <span className="brand-period">.</span>
            </span>
          </a>
          <span className="workspace-label">
            <span />
            Analysis workspace
          </span>
          <button
            className="help-button"
            aria-expanded={help}
            aria-controls="help-panel"
            onClick={() => setHelp(!help)}
          >
            <CircleHelp size={17} />
            <span>How it works</span>
          </button>
        </div>
      </header>
      {help && (
        <aside className="help-panel" id="help-panel">
          <div>
            <strong>A clearer view of the board.</strong>
            <p>
              Load a FEN or play legal moves, then select Analyze position.
              Stockfish evaluates the position; the explanation describes
              material, pawns, and files. Select a square or file in the details
              to highlight it. Scores are always from White’s perspective. A FEN
              does not include earlier moves or repetition history.
            </p>
          </div>
          <button
            className="icon-button"
            aria-label="Close help"
            onClick={() => setHelp(false)}
          >
            <X size={18} />
          </button>
        </aside>
      )}
      <main id="workspace">
        <div className="page-heading">
          <div>
            <p className="eyebrow">LESS GUESSWORK. MORE UNDERSTANDING.</p>
            <h1>
              See the board.
              <br className="mobile-break" /> Understand the position.
            </h1>
            <p>Explore a position and discover the facts behind it.</p>
          </div>
          <span className="engine-label">
            <span />
            Powered by Stockfish
          </span>
        </div>
        <div className="workspace-grid">
          <section className="board-section" aria-label="Position workspace">
            <div className="section-title">
              <h2>{cursor === 0 ? name : "Position explorer"}</h2>
              <span className="position-tag">
                {cursor === 0
                  ? "Position study"
                  : `${cursor} ${cursor === 1 ? "move" : "moves"} explored`}
              </span>
            </div>
            <div className="board-card">
              {player(flipped ? "w" : "b")}
              <div className="board-and-eval">
                <div
                  className={`evaluation-rail ${flipped ? "flipped" : ""}`}
                  role="img"
                  aria-label={`Evaluation: ${evaluation.score}, ${evaluation.label}`}
                >
                  <span
                    className="white-eval"
                    style={{ height: `${evaluation.whitePercent}%` }}
                  />
                  <span className="rail-score" aria-hidden="true">
                    {evaluation.score}
                  </span>
                </div>
                <Board
                  fen={position.fen}
                  flipped={flipped}
                  highlights={highlights}
                  lastMove={lastMove}
                  onMove={makeMove}
                />
              </div>
              {player(flipped ? "b" : "w")}
              <div className="board-toolbar">
                <div className="toolbar-group">
                  <button
                    className="icon-button"
                    aria-label="Flip board"
                    title="Flip board"
                    onClick={() => setFlipped(!flipped)}
                  >
                    <ArrowDownUp size={17} />
                  </button>
                  <button
                    className="icon-button"
                    aria-label="Reset to starting position"
                    title="Reset to starting position"
                    onClick={() => loadPosition(START_FEN, "Starting position")}
                  >
                    <RotateCcw size={17} />
                  </button>
                  <button
                    className="icon-button"
                    aria-label="Copy FEN"
                    title="Copy FEN"
                    onClick={copyFen}
                  >
                    <Copy size={17} />
                  </button>
                </div>
                <span className="move-label">
                  {position.san || `${sideName(chess.turn())} to move`}
                </span>
                <div className="toolbar-group">
                  <button
                    className="icon-button"
                    aria-label="First position"
                    title="First position"
                    disabled={cursor === 0}
                    onClick={() => navigate(0)}
                  >
                    <ChevronsLeft size={18} />
                  </button>
                  <button
                    className="icon-button"
                    aria-label="Previous position"
                    title="Previous position"
                    disabled={cursor === 0}
                    onClick={() => navigate(cursor - 1)}
                  >
                    <ChevronLeft size={18} />
                  </button>
                  <button
                    className="icon-button"
                    aria-label="Next position"
                    title="Next position"
                    disabled={cursor === history.length - 1}
                    onClick={() => navigate(cursor + 1)}
                  >
                    <ChevronRight size={18} />
                  </button>
                  <button
                    className="icon-button"
                    aria-label="Latest position"
                    title="Latest position"
                    disabled={cursor === history.length - 1}
                    onClick={() => navigate(history.length - 1)}
                  >
                    <ChevronsRight size={18} />
                  </button>
                </div>
              </div>
            </div>
            <p className="board-hint">
              <span className="small-dot" />
              Click a piece, then a highlighted square to explore a move.
            </p>
            <form
              className="fen-card"
              onSubmit={(event) => {
                event.preventDefault();
                loadPosition(draft);
              }}
            >
              <div className="fen-heading">
                <label htmlFor="fen-input">Have a position in mind?</label>
                <span>LOAD A FEN</span>
              </div>
              <div className="fen-input-row">
                <textarea
                  ref={input}
                  id="fen-input"
                  aria-label="FEN position"
                  aria-invalid={!!fenError}
                  aria-describedby={fenError ? "fen-error" : "fen-hint"}
                  spellCheck={false}
                  rows={2}
                  maxLength={512}
                  value={draft}
                  onChange={(event) => {
                    setDraft(event.target.value);
                    setFenError("");
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      loadPosition(draft);
                    }
                  }}
                />
                <button
                  className="load-button"
                  type="submit"
                  title="Load position"
                >
                  <CornerDownLeft size={17} />
                  <span>Load</span>
                </button>
              </div>
              {fenError ? (
                <p className="field-error" id="fen-error" role="alert">
                  {fenError}
                </p>
              ) : (
                <p id="fen-hint">
                  Paste all six FEN fields, then press Enter to load.
                </p>
              )}
            </form>
          </section>
          <section
            className="analysis-section"
            aria-label="Position analysis"
            aria-busy={busy}
          >
            <div className="section-title">
              <h2>Your analysis</h2>
              <span
                className={`status-pill ${status === "completed" ? "done" : ""}`}
              >
                <span />
                {status === "completed"
                  ? "Complete"
                  : busy
                    ? "In progress"
                    : status === "error"
                      ? "Needs attention"
                      : "Ready when you are"}
              </span>
            </div>
            {result ? (
              <Analysis
                key={result.task_id}
                result={result}
                highlights={highlights}
                onHighlight={setHighlights}
              />
            ) : (
              <div className="analysis-empty">
                <div className="empty-illustration" aria-hidden="true">
                  <span className="orbit orbit-one" />
                  <span className="orbit orbit-two" />
                  <span className="empty-piece">
                    <Piece code="wn" />
                  </span>
                  <span className="floating-spark">
                    <Sparkles size={20} />
                  </span>
                </div>
                <span className="eyebrow">
                  A LITTLE CLARITY GOES A LONG WAY
                </span>
                <h2>
                  There’s more to
                  <br />a position than a score.
                </h2>
                <p>
                  Understand what’s on the board,
                  <br />
                  one clear explanation at a time.
                </p>
                <div className="empty-features">
                  <div>
                    <span>
                      <ScanLine size={18} />
                    </span>
                    <p>
                      <strong>See the evaluation</strong>Find out which side the
                      engine favors.
                    </p>
                  </div>
                  <div>
                    <span>
                      <Layers3 size={18} />
                    </span>
                    <p>
                      <strong>Understand the material</strong>Compare each
                      side’s remaining pieces.
                    </p>
                  </div>
                  <div>
                    <span>
                      <Leaf size={18} />
                    </span>
                    <p>
                      <strong>Read the pawn structure</strong>Spot passed,
                      isolated, and doubled pawns.
                    </p>
                  </div>
                  <div>
                    <span>
                      <Columns3 size={18} />
                    </span>
                    <p>
                      <strong>Explore the files</strong>See where the open and
                      semi-open files are.
                    </p>
                  </div>
                </div>
              </div>
            )}
            <div className="analysis-actions">
              {error && (
                <div className="analysis-error" role="alert">
                  <AlertCircle size={19} />
                  <div>
                    <strong>Analysis interrupted</strong>
                    <p>{error}</p>
                  </div>
                </div>
              )}
              <button
                className={`analyze-button ${busy ? "is-loading" : ""}`}
                onClick={analyze}
                disabled={busy}
              >
                {busy ? (
                  <LoaderCircle className="spin" size={20} />
                ) : (
                  <Sparkles size={19} />
                )}
                <span>
                  {status === "submitting"
                    ? "Connecting to the engine…"
                    : status === "queued"
                      ? "Position queued…"
                      : status === "running"
                        ? "Analyzing your position…"
                        : status === "error"
                          ? "Try analysis again"
                          : result
                            ? "Analyze again"
                            : "Analyze position"}
                </span>
                {!busy && <ArrowRight size={19} />}
              </button>
              {busy ? (
                <button className="cancel-button" onClick={clearAnalysis}>
                  Cancel analysis
                </button>
              ) : (
                <p className="analysis-caption">
                  <Check size={14} />
                  Grounded in the position. Explained in plain language.
                </p>
              )}
            </div>
            <p className="sr-only" role="status">
              {busy
                ? status === "running"
                  ? "The engine is analyzing your position."
                  : "Your analysis is being prepared."
                : status === "completed"
                  ? `Analysis complete. ${evaluation.score}. ${evaluation.label}.`
                  : ""}
            </p>
          </section>
        </div>
        <section className="examples-section" aria-labelledby="examples-title">
          <div className="examples-heading">
            <div>
              <span className="eyebrow">A GOOD PLACE TO START</span>
              <h2 id="examples-title">A few positions worth exploring.</h2>
            </div>
            <p>Pick a position. Find something new.</p>
          </div>
          <div className="example-grid">
            {EXAMPLES.map((example) => (
              <button
                className={`example-card ${history[0].fen === example.fen ? "current" : ""}`}
                aria-pressed={history[0].fen === example.fen}
                key={example.name}
                onClick={() => loadPosition(example.fen, example.name)}
              >
                <span className="example-piece">
                  <Piece code={example.piece} />
                </span>
                <span className="example-copy">
                  <span>{example.category}</span>
                  <strong>{example.name}</strong>
                  <span>{example.description}</span>
                </span>
                <ArrowUpRightIcon />
              </button>
            ))}
          </div>
        </section>
      </main>
      <footer>
        <span className="footer-brand">
          A better understanding, one position at a time.
        </span>
        <span>
          Standard chess <span>·</span> Stockfish analysis <span>·</span>{" "}
          Factual explanations
        </span>
      </footer>
      <div className={`toast ${notice ? "visible" : ""}`} role="status">
        {notice && (
          <>
            <Check size={16} />
            {notice}
          </>
        )}
      </div>
    </>
  );
}

function ArrowUpRightIcon() {
  return <ArrowRight className="example-arrow" size={18} aria-hidden="true" />;
}
