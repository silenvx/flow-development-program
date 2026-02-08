/**
 * Tests for review_contradiction_check module.
 *
 * Migrated from Python: .claude/scripts/tests/test_review_contradiction_check.py
 */

import { describe, expect, test } from "bun:test";
import {
  type ContradictionWarning,
  type ReviewComment,
  detectPotentialContradictions,
  formatContradictionWarnings,
} from "./review_contradiction_check";

const PROXIMITY_THRESHOLD = 10;

describe("detectPotentialContradictions", () => {
  test("detects same file close lines", () => {
    const prev: ReviewComment[] = [{ path: "src/app.py", line: 100, body: "Previous comment" }];
    const newComments: ReviewComment[] = [{ path: "src/app.py", line: 105, body: "New comment" }];

    const warnings = detectPotentialContradictions(newComments, prev);

    expect(warnings.length).toBe(1);
    expect(warnings[0].file).toBe("src/app.py");
    expect(warnings[0].prevLine).toBe(100);
    expect(warnings[0].newLine).toBe(105);
    expect(warnings[0].sameBatch).toBe(false);
  });

  test("ignores different files", () => {
    const prev: ReviewComment[] = [{ path: "src/app.py", line: 100, body: "Previous comment" }];
    const newComments: ReviewComment[] = [{ path: "src/other.py", line: 100, body: "New comment" }];

    const warnings = detectPotentialContradictions(newComments, prev);

    expect(warnings.length).toBe(0);
  });

  test("ignores distant lines", () => {
    const prev: ReviewComment[] = [{ path: "src/app.py", line: 100, body: "Previous comment" }];
    const newComments: ReviewComment[] = [
      { path: "src/app.py", line: 100 + PROXIMITY_THRESHOLD, body: "New comment" },
    ];

    const warnings = detectPotentialContradictions(newComments, prev);

    expect(warnings.length).toBe(0);
  });

  test("detects at threshold boundary", () => {
    const prev: ReviewComment[] = [{ path: "src/app.py", line: 100, body: "Previous comment" }];
    const newComments: ReviewComment[] = [
      { path: "src/app.py", line: 100 + PROXIMITY_THRESHOLD - 1, body: "New comment" },
    ];

    const warnings = detectPotentialContradictions(newComments, prev);

    expect(warnings.length).toBe(1);
  });

  test("handles multiple comments", () => {
    const prev: ReviewComment[] = [
      { path: "src/app.py", line: 100, body: "First previous" },
      { path: "src/app.py", line: 200, body: "Second previous" },
    ];
    const newComments: ReviewComment[] = [
      { path: "src/app.py", line: 102, body: "Close to first" },
      { path: "src/app.py", line: 205, body: "Close to second" },
    ];

    const warnings = detectPotentialContradictions(newComments, prev);

    expect(warnings.length).toBe(2);
  });

  test("truncates long body", () => {
    const longBody = "x".repeat(200);
    const prev: ReviewComment[] = [{ path: "src/app.py", line: 100, body: longBody }];
    const newComments: ReviewComment[] = [{ path: "src/app.py", line: 105, body: longBody }];

    const warnings = detectPotentialContradictions(newComments, prev);

    expect(warnings[0].prevBody.length).toBe(100);
    expect(warnings[0].newBody.length).toBe(100);
    expect(warnings[0].prevTruncated).toBe(true);
    expect(warnings[0].newTruncated).toBe(true);
  });

  test("handles missing path", () => {
    const prev: ReviewComment[] = [{ path: "src/app.py", line: 100, body: "Previous" }];
    const newComments: ReviewComment[] = [{ line: 100, body: "No path" }]; // Missing path

    const warnings = detectPotentialContradictions(newComments, prev);

    expect(warnings.length).toBe(0);
  });

  test("handles missing line", () => {
    const prev: ReviewComment[] = [{ path: "src/app.py", line: 100, body: "Previous" }];
    const newComments: ReviewComment[] = [{ path: "src/app.py", body: "No line" }]; // Missing line

    const warnings = detectPotentialContradictions(newComments, prev);

    expect(warnings.length).toBe(0);
  });

  test("handles empty lists", () => {
    expect(detectPotentialContradictions([], [])).toEqual([]);
    expect(detectPotentialContradictions([{ path: "a.py", line: 1, body: "x" }], [])).toEqual([]);
    expect(detectPotentialContradictions([], [{ path: "a.py", line: 1, body: "x" }])).toEqual([]);
  });

  test("handles missing body", () => {
    const prev: ReviewComment[] = [{ path: "src/app.py", line: 100 }]; // Missing body
    const newComments: ReviewComment[] = [{ path: "src/app.py", line: 105 }]; // Missing body

    const warnings = detectPotentialContradictions(newComments, prev);

    expect(warnings.length).toBe(1);
    expect(warnings[0].prevBody).toBe("");
    expect(warnings[0].newBody).toBe("");
  });

  test("detects same line", () => {
    const prev: ReviewComment[] = [{ path: "src/app.py", line: 100, body: "Previous" }];
    const newComments: ReviewComment[] = [
      { path: "src/app.py", line: 100, body: "New on same line" },
    ];

    const warnings = detectPotentialContradictions(newComments, prev);

    expect(warnings.length).toBe(1);
    expect(warnings[0].prevLine).toBe(100);
    expect(warnings[0].newLine).toBe(100);
  });

  test("detects within same batch (Issue #1596)", () => {
    // When previousComments is empty, should detect proximity within newComments
    const newComments: ReviewComment[] = [
      { path: "src/app.py", line: 100, body: "First comment" },
      { path: "src/app.py", line: 105, body: "Second comment" },
    ];

    const warnings = detectPotentialContradictions(newComments, []);

    expect(warnings.length).toBe(1);
    expect(warnings[0].sameBatch).toBe(true);
    expect(warnings[0].file).toBe("src/app.py");
  });
});

describe("formatContradictionWarnings", () => {
  test("formats single warning", () => {
    const warnings: ContradictionWarning[] = [
      {
        file: "src/app.py",
        prevLine: 100,
        newLine: 105,
        prevBody: "Previous comment",
        newBody: "New comment",
        prevTruncated: false,
        newTruncated: false,
        sameBatch: false,
      },
    ];

    const result = formatContradictionWarnings(warnings);

    expect(result).toContain("⚠️");
    expect(result).toContain("src/app.py");
    expect(result).toContain("line 100");
    expect(result).toContain("line 105");
    expect(result).toContain("Previous comment");
    expect(result).toContain("New comment");
    expect(result).toContain("矛盾の可能性あり");
  });

  test("returns empty for no warnings", () => {
    const result = formatContradictionWarnings([]);

    expect(result).toBe("");
  });

  test("formats multiple warnings", () => {
    const warnings: ContradictionWarning[] = [
      {
        file: "src/app.py",
        prevLine: 100,
        newLine: 105,
        prevBody: "First prev",
        newBody: "First new",
        prevTruncated: false,
        newTruncated: false,
        sameBatch: false,
      },
      {
        file: "src/other.py",
        prevLine: 200,
        newLine: 205,
        prevBody: "Second prev",
        newBody: "Second new",
        prevTruncated: false,
        newTruncated: false,
        sameBatch: false,
      },
    ];

    const result = formatContradictionWarnings(warnings);

    expect(result).toContain("src/app.py");
    expect(result).toContain("src/other.py");
    // Count occurrences of "矛盾の可能性あり"
    const matches = result.match(/矛盾の可能性あり/g);
    expect(matches?.length).toBe(2);
  });

  test("adds ellipsis only for truncated body", () => {
    const shortBody = "Short comment";
    const truncatedBody = "x".repeat(100);
    const warnings: ContradictionWarning[] = [
      {
        file: "src/app.py",
        prevLine: 100,
        newLine: 105,
        prevBody: shortBody,
        newBody: truncatedBody,
        prevTruncated: false,
        newTruncated: true, // Was truncated
        sameBatch: false,
      },
    ];

    const result = formatContradictionWarnings(warnings);

    // Short body should NOT have ellipsis
    expect(result).toContain(`"${shortBody}"`);
    // Truncated body should have ellipsis
    expect(result).toContain(`"${truncatedBody}..."`);
  });

  test("formats same batch warning differently", () => {
    const warnings: ContradictionWarning[] = [
      {
        file: "src/app.py",
        prevLine: 100,
        newLine: 105,
        prevBody: "First",
        newBody: "Second",
        prevTruncated: false,
        newTruncated: false,
        sameBatch: true,
      },
    ];

    const result = formatContradictionWarnings(warnings);

    expect(result).toContain("同一バッチ内");
    expect(result).toContain("指摘1");
    expect(result).toContain("指摘2");
    expect(result).not.toContain("矛盾の可能性あり");
  });
});
