export type Side = "white" | "black";
export type Material = Record<
  "queen" | "rook" | "bishop" | "knight" | "pawn",
  number
>;
export interface PawnFacts {
  isolated: string[];
  doubled_files: Record<string, string[]>;
  passed: string[];
}
export interface PositionFacts {
  material: Record<Side | "white_minus_black", Material>;
  pawns: Record<Side, PawnFacts>;
  files: { open: string[]; semi_open: Record<Side, string[]> };
}
export interface TaskResult {
  task_id: string;
  status: "queued" | "running" | "completed" | "failed";
  evaluation: number | null;
  mate?: { winner: Side; moves: number } | null;
  facts: PositionFacts | null;
  explanation: string | null;
  explanation_version: number | null;
  error?: { code: string; message: string } | null;
}
export class AnalysisError extends Error {
  constructor(
    message: string,
    public taskId?: string,
  ) {
    super(message);
  }
}
class RequestError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const strings = (value: unknown, pattern: RegExp) =>
  Array.isArray(value) &&
  value.every((item) => typeof item === "string" && pattern.test(item));
const sides = ["white", "black"] as const;
const pieces = ["queen", "rook", "bishop", "knight", "pawn"] as const;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function validFacts(value: unknown): value is PositionFacts {
  if (!isRecord(value)) return false;
  const { material, pawns, files } = value;
  if (
    !isRecord(material) ||
    !isRecord(pawns) ||
    !isRecord(files) ||
    !isRecord(files.semi_open)
  )
    return false;
  const semi = files.semi_open;
  return (
    [...sides, "white_minus_black"].every((side) => {
      const count = material[side];
      return (
        isRecord(count) &&
        pieces.every(
          (piece) =>
            Number.isInteger(count[piece]) &&
            (side === "white_minus_black" || Number(count[piece]) >= 0),
        )
      );
    }) &&
    sides.every((side) => {
      const pawn = pawns[side];
      return (
        isRecord(pawn) &&
        strings(pawn.isolated, /^[a-h][1-8]$/) &&
        strings(pawn.passed, /^[a-h][1-8]$/) &&
        isRecord(pawn.doubled_files) &&
        Object.entries(pawn.doubled_files).every(
          ([file, squares]) =>
            /^[a-h]$/.test(file) && strings(squares, /^[a-h][1-8]$/),
        ) &&
        strings(semi[side], /^[a-h]$/)
      );
    }) &&
    strings(files.open, /^[a-h]$/)
  );
}

export function parseTask(value: unknown): TaskResult {
  if (
    !isRecord(value) ||
    typeof value.task_id !== "string" ||
    !uuid.test(value.task_id) ||
    !["queued", "running", "completed", "failed"].includes(
      String(value.status),
    ) ||
    !(
      value.evaluation === null ||
      (typeof value.evaluation === "number" &&
        Number.isFinite(value.evaluation))
    ) ||
    !(value.facts == null || validFacts(value.facts)) ||
    !(value.explanation == null || typeof value.explanation === "string") ||
    !(
      value.explanation_version == null ||
      Number.isInteger(value.explanation_version)
    )
  ) {
    throw new Error(
      "The analysis service returned an unexpected response. Please try again.",
    );
  }
  if (
    value.mate != null &&
    (!isRecord(value.mate) ||
      !sides.includes(value.mate.winner as Side) ||
      !Number.isInteger(value.mate.moves) ||
      Number(value.mate.moves) < 0)
  ) {
    throw new Error(
      "The analysis service returned an invalid mate score. Please try again.",
    );
  }
  return value as unknown as TaskResult;
}

async function request(
  path: string,
  signal: AbortSignal,
  body?: { fen: string },
): Promise<unknown> {
  const response = await fetch(path, {
    method: body ? "POST" : "GET",
    ...(body
      ? {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : {}),
    signal: AbortSignal.any([signal, AbortSignal.timeout(10_000)]),
    cache: "no-store",
  });
  if (!response.ok) {
    if (response.status === 422)
      throw new RequestError(
        "This position cannot be analyzed. Check that both kings are legal and the FEN describes a valid position.",
        422,
      );
    if (response.status === 404)
      throw new RequestError(
        "This analysis could not be found. Try again to start a new analysis.",
        404,
      );
    throw new Error(
      "The analysis service is unavailable. Please try again in a moment.",
    );
  }
  return response.json();
}

function pause(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted();
    const abort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", abort, { once: true });
  });
}

export async function analyzePosition(
  fen: string,
  options: {
    signal: AbortSignal;
    onProgress: (status: "queued" | "running") => void;
    taskId?: string;
    pollInterval?: number;
    timeout?: number;
  },
): Promise<TaskResult> {
  let taskId = options.taskId;
  const deadline = AbortSignal.timeout(options.timeout ?? 90_000);
  const signal = AbortSignal.any([options.signal, deadline]);
  let failed = false;
  try {
    if (!taskId) {
      const created = await request("/tasks", signal, { fen });
      if (
        !isRecord(created) ||
        typeof created.task_id !== "string" ||
        !uuid.test(created.task_id)
      )
        throw new Error(
          "The analysis service did not return an analysis ID. Please try again.",
        );
      taskId = created.task_id;
    }
    while (true) {
      const result = parseTask(await request(`/tasks/${taskId}`, signal));
      if (result.task_id !== taskId)
        throw new Error(
          "The analysis service returned a different position. Please try again.",
        );
      if (result.status === "completed") return result;
      if (result.status === "failed") {
        failed = true;
        throw new Error(
          "The engine could not finish this analysis. Please try analyzing the position again.",
        );
      }
      options.onProgress(result.status);
      await pause(options.pollInterval ?? 1000, signal);
    }
  } catch (error) {
    if (options.signal.aborted) throw error;
    const message = deadline.aborted
      ? "This analysis is taking longer than expected. Try again to check its progress."
      : error instanceof SyntaxError
        ? "The analysis service returned an unreadable response. Please try again."
        : error instanceof TypeError
          ? "Could not reach the analysis service. Check your connection and try again."
          : error instanceof DOMException && error.name === "TimeoutError"
            ? "The connection timed out. Please try again."
            : error instanceof Error
              ? error.message
              : "Something went wrong. Please try again.";
    throw new AnalysisError(
      message,
      failed || (error instanceof RequestError && error.status === 404)
        ? undefined
        : taskId,
    );
  }
}

export function evaluationView(result: TaskResult | null): {
  score: string;
  label: string;
  whitePercent: number;
} {
  if (!result)
    return { score: "—", label: "Awaiting analysis", whitePercent: 50 };
  if (result.mate) {
    const winner = result.mate.winner === "white" ? "White" : "Black";
    return {
      score: result.mate.moves === 0 ? "#" : `M${result.mate.moves}`,
      label:
        result.mate.moves === 0
          ? `Checkmate · ${winner} wins`
          : `${winner} has mate in ${result.mate.moves}`,
      whitePercent: winner === "White" ? 100 : 0,
    };
  }
  const value = result.evaluation;
  if (value === null)
    return { score: "—", label: "Evaluation unavailable", whitePercent: 50 };
  return {
    score: `${value > 0 ? "+" : ""}${value.toFixed(2)}`,
    label:
      Math.abs(value) < 0.2
        ? "Approximately equal"
        : `Engine favors ${value > 0 ? "White" : "Black"}`,
    whitePercent: Math.max(5, Math.min(95, 50 + 45 * Math.tanh(value / 4))),
  };
}
