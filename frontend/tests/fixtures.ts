import type { TaskResult } from "../src/api";

export const ID = "00000000-0000-0000-0000-000000000001";
export const completed: TaskResult = {
  task_id: ID,
  status: "completed",
  evaluation: 1.23,
  explanation_version: 1,
  explanation:
    "White has one more pawn than Black. White's d4-pawn is isolated and passed. The a-, b-, c-, and e-files are open; the d-file is semi-open for Black.",
  facts: {
    material: {
      white: { queen: 0, rook: 0, bishop: 0, knight: 0, pawn: 4 },
      black: { queen: 0, rook: 0, bishop: 0, knight: 0, pawn: 3 },
      white_minus_black: { queen: 0, rook: 0, bishop: 0, knight: 0, pawn: 1 },
    },
    pawns: {
      white: { isolated: ["d4"], doubled_files: {}, passed: ["d4"] },
      black: { isolated: [], doubled_files: {}, passed: [] },
    },
    files: {
      open: ["a", "b", "c", "e"],
      semi_open: { white: [], black: ["d"] },
    },
  },
};
export const queued: TaskResult = {
  task_id: ID,
  status: "queued",
  evaluation: null,
  facts: null,
  explanation: null,
  explanation_version: null,
};
export const response = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
