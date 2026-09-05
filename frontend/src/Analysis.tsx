import { useState } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  CircleHelp,
  Columns3,
  Layers3,
  Leaf,
  ScanLine,
  Sparkles,
} from "lucide-react";
import { evaluationView, type TaskResult, type PositionFacts } from "./api";
import { fileSquares } from "./chess";
import { Piece } from "./Board";

type Tab = "overview" | "material" | "pawns" | "files";
const tabs: Tab[] = ["overview", "material", "pawns", "files"];
const materialPieces = [
  ["queen", "q"],
  ["rook", "r"],
  ["bishop", "b"],
  ["knight", "n"],
  ["pawn", "p"],
] as const;

function SquareChips({
  values,
  onHighlight,
  highlighted,
  file = false,
}: {
  values: string[];
  onHighlight: (squares: string[]) => void;
  highlighted: string[];
  file?: boolean;
}) {
  if (!values.length) return <span className="muted">None</span>;
  return (
    <span className="square-chips">
      {values.map((value) => {
        const squares = file ? fileSquares(value) : [value];
        const active = squares.every((square) => highlighted.includes(square));
        return (
          <button
            type="button"
            className={`square-chip ${active ? "active" : ""}`}
            key={value}
            aria-label={`Highlight ${file ? `${value}-file` : value}`}
            aria-pressed={active}
            onClick={() => onHighlight(active ? [] : squares)}
          >
            {value}
            {file ? "-file" : ""}
            <ArrowUpRight size={12} aria-hidden="true" />
          </button>
        );
      })}
    </span>
  );
}

function MaterialDetail({ facts }: { facts: PositionFacts }) {
  return (
    <div className="detail-content">
      <h3>What’s on the board</h3>
      <p className="muted">Compare the pieces each side has remaining.</p>
      <table className="material-table">
        <caption className="sr-only">
          Material comparison for White and Black
        </caption>
        <thead>
          <tr>
            <th scope="col">Piece</th>
            <th scope="col">White</th>
            <th scope="col">Black</th>
            <th scope="col">Difference</th>
          </tr>
        </thead>
        <tbody>
          {materialPieces.map(([name, code]) => {
            const difference = facts.material.white_minus_black[name];
            return (
              <tr key={name}>
                <th scope="row">
                  <Piece code={`w${code}`} />
                  {name[0].toUpperCase() + name.slice(1)}
                </th>
                <td>{facts.material.white[name]}</td>
                <td>{facts.material.black[name]}</td>
                <td>
                  <span
                    className={difference ? "material-difference" : "muted"}
                  >
                    {difference === 0
                      ? "—"
                      : `${difference > 0 ? "White" : "Black"} +${Math.abs(difference)}`}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="detail-note">
        <CircleHelp size={15} />
        Piece counts are separate from the engine’s position evaluation.
      </p>
    </div>
  );
}

export default function Analysis({
  result,
  highlights,
  onHighlight,
}: {
  result: TaskResult;
  highlights: string[];
  onHighlight: (squares: string[]) => void;
}) {
  const [tab, setTab] = useState<Tab>("overview");
  const evaluation = evaluationView(result);
  const facts = result.facts;
  return (
    <>
      <section className="evaluation-card" aria-label="Engine evaluation">
        <div>
          <span className="eyebrow">STOCKFISH EVALUATION</span>
          <div className="evaluation-value">
            {evaluation.score}
            <span>{evaluation.label}</span>
          </div>
        </div>
        <span className="evaluation-orbit" aria-hidden="true">
          <ScanLine size={26} />
        </span>
        <div className="horizontal-evaluation" aria-hidden="true">
          <span style={{ width: `${evaluation.whitePercent}%` }} />
        </div>
        <p>
          {result.mate
            ? "Forced mate · distance measured in moves"
            : "White’s perspective · measured in pawns"}
        </p>
      </section>
      <section className="explanation-card">
        <div className="card-heading">
          <span className="icon-tile">
            <Sparkles size={19} />
          </span>
          <div>
            <h2>The position, explained.</h2>
            <p>Facts you can see on the board.</p>
          </div>
          <span
            className="complete-indicator"
            role="img"
            aria-label="Analysis complete"
          >
            <Check size={16} />
          </span>
        </div>
        <div
          className="analysis-tabs"
          role="tablist"
          aria-label="Analysis details"
        >
          {tabs.map((value) => (
            <button
              id={`tab-${value}`}
              role="tab"
              aria-selected={tab === value}
              aria-controls="analysis-panel"
              tabIndex={tab === value ? 0 : -1}
              key={value}
              onClick={() => {
                setTab(value);
                onHighlight([]);
              }}
              onKeyDown={(event) => {
                if (
                  ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)
                ) {
                  event.preventDefault();
                  const index =
                    event.key === "Home"
                      ? 0
                      : event.key === "End"
                        ? 3
                        : (tabs.indexOf(value) +
                            (event.key === "ArrowRight" ? 1 : 3)) %
                          4;
                  setTab(tabs[index]);
                  onHighlight([]);
                  document.getElementById(`tab-${tabs[index]}`)?.focus();
                }
              }}
            >
              {value[0].toUpperCase() + value.slice(1)}
            </button>
          ))}
        </div>
        <div
          id="analysis-panel"
          role="tabpanel"
          aria-labelledby={`tab-${tab}`}
          tabIndex={0}
        >
          {tab === "overview" ? (
            <div className="overview-content">
              <p className="explanation-prose">
                {result.explanation ||
                  "This saved analysis includes an engine evaluation, but no factual explanation. Analyze the position again to request a new explanation."}
              </p>
              {facts && (
                <div className="fact-shortcuts">
                  <button onClick={() => setTab("material")}>
                    <span className="shortcut-icon">
                      <Layers3 size={18} />
                    </span>
                    <span>
                      <strong>Material balance</strong>
                      <span>
                        {materialPieces.every(
                          ([piece]) =>
                            facts.material.white_minus_black[piece] === 0,
                        )
                          ? "Both sides have equal material"
                          : "See the piece-by-piece comparison"}
                      </span>
                    </span>
                    <ArrowRight size={16} />
                  </button>
                  <button onClick={() => setTab("pawns")}>
                    <span className="shortcut-icon">
                      <Leaf size={18} />
                    </span>
                    <span>
                      <strong>Pawn structure</strong>
                      <span>
                        {facts.pawns.white.passed.length +
                          facts.pawns.black.passed.length}{" "}
                        passed ·{" "}
                        {facts.pawns.white.isolated.length +
                          facts.pawns.black.isolated.length}{" "}
                        isolated
                      </span>
                    </span>
                    <ArrowRight size={16} />
                  </button>
                  <button onClick={() => setTab("files")}>
                    <span className="shortcut-icon">
                      <Columns3 size={18} />
                    </span>
                    <span>
                      <strong>Open & semi-open files</strong>
                      <span>
                        {facts.files.open.length} open ·{" "}
                        {facts.files.semi_open.white.length +
                          facts.files.semi_open.black.length}{" "}
                        semi-open
                      </span>
                    </span>
                    <ArrowRight size={16} />
                  </button>
                </div>
              )}
            </div>
          ) : !facts ? (
            <div className="detail-content">
              <p>
                No factual details were saved with this result. Analyze this
                position again to request them.
              </p>
            </div>
          ) : tab === "material" ? (
            <MaterialDetail facts={facts} />
          ) : tab === "pawns" ? (
            <div className="detail-content">
              <h3>The shape of the pawns</h3>
              <p className="muted">Select a square to find it on the board.</p>
              {(["white", "black"] as const).map((side) => (
                <div className="fact-side" key={side}>
                  <h4>
                    <span className={`side-dot ${side}`} />
                    {side === "white" ? "White" : "Black"}
                  </h4>
                  <dl>
                    <div>
                      <dt>
                        Passed{" "}
                        <span title="No opposing pawn ahead on the same or an adjacent file.">
                          <CircleHelp size={13} />
                        </span>
                      </dt>
                      <dd>
                        <SquareChips
                          values={facts.pawns[side].passed}
                          onHighlight={onHighlight}
                          highlighted={highlights}
                        />
                      </dd>
                    </div>
                    <div>
                      <dt>
                        Isolated{" "}
                        <span title="No friendly pawn on either adjacent file.">
                          <CircleHelp size={13} />
                        </span>
                      </dt>
                      <dd>
                        <SquareChips
                          values={facts.pawns[side].isolated}
                          onHighlight={onHighlight}
                          highlighted={highlights}
                        />
                      </dd>
                    </div>
                    <div>
                      <dt>
                        Doubled{" "}
                        <span title="Two or more friendly pawns on the same file.">
                          <CircleHelp size={13} />
                        </span>
                      </dt>
                      <dd>
                        <SquareChips
                          values={Object.values(
                            facts.pawns[side].doubled_files,
                          ).flat()}
                          onHighlight={onHighlight}
                          highlighted={highlights}
                        />
                      </dd>
                    </div>
                  </dl>
                </div>
              ))}
              <p className="detail-note">
                Passed: no opposing pawn ahead nearby. Isolated: no friendly
                pawn on an adjacent file. Doubled: friendly pawns share a file.
              </p>
            </div>
          ) : (
            <div className="detail-content">
              <h3>Room along the files</h3>
              <p className="muted">
                Select a file to highlight it on the board.
              </p>
              <div className="file-fact">
                <h4>Open files</h4>
                <p>No pawns from either side.</p>
                <SquareChips
                  values={facts.files.open}
                  onHighlight={onHighlight}
                  highlighted={highlights}
                  file
                />
              </div>
              {(["white", "black"] as const).map((side) => (
                <div key={side} className="file-fact">
                  <h4>Semi-open for {side === "white" ? "White" : "Black"}</h4>
                  <p>No {side} pawn; at least one opposing pawn.</p>
                  <SquareChips
                    values={facts.files.semi_open[side]}
                    onHighlight={onHighlight}
                    highlighted={highlights}
                    file
                  />
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="analysis-footnote">
          <span className="small-dot" />
          {result.explanation_version
            ? `Factual explanation · v${result.explanation_version}`
            : "Engine evaluation"}
          <span>No move recommendations</span>
        </div>
      </section>
    </>
  );
}
