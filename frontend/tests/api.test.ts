import { describe, expect, it, vi } from "vitest";
import {
  AnalysisError,
  analyzePosition,
  evaluationView,
  parseTask,
} from "../src/api";
import { START_FEN } from "../src/chess";
import { completed, ID, queued, response } from "./fixtures";

const options = () => ({
  signal: new AbortController().signal,
  onProgress: vi.fn(),
  pollInterval: 1,
});

describe("asynchronous analysis lifecycle", () => {
  it("submits the exact board FEN and polls queued → running → completed without caching", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(response({ task_id: ID }, 202))
      .mockResolvedValueOnce(response(queued))
      .mockResolvedValueOnce(response({ ...queued, status: "running" }))
      .mockResolvedValueOnce(response(completed));
    vi.stubGlobal("fetch", fetch);
    const config = options();
    expect(await analyzePosition(START_FEN, config)).toEqual(completed);
    expect(config.onProgress.mock.calls).toEqual([["queued"], ["running"]]);
    expect(fetch).toHaveBeenCalledTimes(4);
    expect(fetch.mock.calls[0][0]).toBe("/tasks");
    expect(fetch.mock.calls[0][1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ fen: START_FEN }),
      cache: "no-store",
    });
    expect(fetch.mock.calls[1][0]).toBe(`/tasks/${ID}`);
    expect(fetch.mock.calls[1][1]).toMatchObject({
      method: "GET",
      cache: "no-store",
    });
  });
  it("resumes polling the same task instead of submitting duplicate work", async () => {
    const fetch = vi.fn().mockResolvedValue(response(completed));
    vi.stubGlobal("fetch", fetch);
    await analyzePosition(START_FEN, { ...options(), taskId: ID });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][1].method).toBe("GET");
  });
  it("retains the task ID on a polling connection error", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(response({ task_id: ID }, 202))
        .mockRejectedValueOnce(new TypeError("Failed to fetch")),
    );
    await expect(analyzePosition(START_FEN, options())).rejects.toMatchObject({
      taskId: ID,
      message: expect.stringContaining("connection"),
    });
  });
  it("lets a failed worker start a new task on retry", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response({
          ...queued,
          status: "failed",
          error: { code: "ENGINE_TIMEOUT", message: "timed out" },
        }),
      ),
    );
    await expect(
      analyzePosition(START_FEN, { ...options(), taskId: ID }),
    ).rejects.toMatchObject({
      taskId: undefined,
      message: expect.stringContaining("engine could not finish"),
    });
  });
  it.each([
    [422, "valid position"],
    [503, "unavailable"],
  ])("handles submit HTTP %s", async (status, message) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(response({}, Number(status))),
    );
    await expect(analyzePosition(START_FEN, options())).rejects.toThrow(
      String(message),
    );
  });
  it("handles a missing saved task without interpreting HTML as a result", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response("<html>Not found</html>", { status: 404 }),
        ),
    );
    await expect(
      analyzePosition(START_FEN, { ...options(), taskId: ID }),
    ).rejects.toThrow("could not be found");
  });
  it("rejects a success response without a valid task ID", async () => {
    const fetch = vi.fn().mockResolvedValue(response({ task_id: "../../etc" }));
    vi.stubGlobal("fetch", fetch);
    await expect(analyzePosition(START_FEN, options())).rejects.toThrow(
      "analysis ID",
    );
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("rejects a response for a different task", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response({
          ...completed,
          task_id: "00000000-0000-0000-0000-000000000002",
        }),
      ),
    );
    await expect(
      analyzePosition(START_FEN, { ...options(), taskId: ID }),
    ).rejects.toThrow("different position");
  });
  it("stops polling immediately when the position is cancelled", async () => {
    const controller = new AbortController();
    const fetch = vi.fn().mockResolvedValue(response(queued));
    vi.stubGlobal("fetch", fetch);
    const promise = analyzePosition(START_FEN, {
      ...options(),
      signal: controller.signal,
      taskId: ID,
      onProgress: () => controller.abort(),
    });
    await expect(promise).rejects.toMatchObject({ name: "AbortError" });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("bounds total polling duration and preserves the task for retry", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(queued)));
    // Return fresh responses because Response.json() consumes each body.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async () => response(queued)),
    );
    await expect(
      analyzePosition(START_FEN, {
        ...options(),
        taskId: ID,
        timeout: 20,
        pollInterval: 100,
      }),
    ).rejects.toMatchObject({
      taskId: ID,
      message: expect.stringContaining("longer than expected"),
    });
  });
  it("reports an invalid JSON response as a recoverable error", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(new Response("<html>upstream unavailable</html>")),
    );
    await expect(analyzePosition(START_FEN, options())).rejects.toBeInstanceOf(
      AnalysisError,
    );
  });
});

describe("response compatibility and validation", () => {
  it("accepts the factual v1 response and legacy completed rows", () => {
    expect(parseTask(completed)).toEqual(completed);
    expect(
      parseTask({ ...queued, status: "completed", evaluation: 0.34 }),
    ).toMatchObject({ facts: null, evaluation: 0.34 });
  });
  it.each([NaN, Infinity, "1.2", undefined])(
    "rejects invalid evaluation %s",
    (evaluation) => {
      expect(() => parseTask({ ...completed, evaluation })).toThrow(
        "unexpected response",
      );
    },
  );
  it.each([
    {},
    "invalid",
    { ...completed.facts, pawns: {} },
    {
      ...completed.facts,
      files: { open: ["z"], semi_open: { white: [], black: [] } },
    },
  ])("rejects malformed facts safely", (facts) => {
    expect(() => parseTask({ ...completed, facts })).toThrow(
      "unexpected response",
    );
  });
  it.each([
    { winner: "green", moves: 1 },
    { winner: "white", moves: -1 },
    { winner: "black", moves: "2" },
  ])("rejects an invalid mate object", (mate) => {
    expect(() => parseTask({ ...completed, mate })).toThrow("invalid mate");
  });
});

describe("evaluation presentation", () => {
  it.each([
    [1.23, "+1.23", "White"],
    [-2.5, "-2.50", "Black"],
    [0, "0.00", "equal"],
    [0.1, "+0.10", "equal"],
  ])("formats a White-perspective score %s", (evaluation, score, label) => {
    const view = evaluationView({
      ...completed,
      evaluation: Number(evaluation),
    });
    expect(view.score).toBe(score);
    expect(view.label).toContain(label);
  });
  it.each(["white", "black"] as const)(
    "uses an explicit mate winner for %s",
    (winner) => {
      const view = evaluationView({
        ...completed,
        evaluation: null,
        mate: { winner, moves: 3 },
      });
      expect(view.score).toBe("M3");
      expect(view.label).toBe(
        `${winner === "white" ? "White" : "Black"} has mate in 3`,
      );
      expect(view.whitePercent).toBe(winner === "white" ? 100 : 0);
    },
  );
  it("distinguishes checkmate, unavailable evaluation, and awaiting analysis", () => {
    expect(
      evaluationView({ ...completed, mate: { winner: "black", moves: 0 } }),
    ).toMatchObject({ score: "#", label: "Checkmate · Black wins" });
    expect(evaluationView({ ...queued, status: "completed" }).label).toBe(
      "Evaluation unavailable",
    );
    expect(evaluationView(null).label).toBe("Awaiting analysis");
  });
});
