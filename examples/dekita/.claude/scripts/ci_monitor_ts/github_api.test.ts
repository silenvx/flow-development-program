/**
 * Tests for github_api module.
 *
 * Changelog:
 *   - silenvx/dekita#3748: Initial test for isRateLimitError
 */

import { describe, expect, test } from "bun:test";
import { isRateLimitError } from "./github_api";

describe("isRateLimitError", () => {
  test("detects rate limit in output", () => {
    expect(isRateLimitError("rate limit exceeded")).toBe(true);
    expect(isRateLimitError("Rate_Limited")).toBe(true);
    expect(isRateLimitError("secondary rate limit")).toBe(true);
    expect(isRateLimitError("too many requests")).toBe(true);
  });

  test("ignores URLs containing rate limit keywords", () => {
    // URLs should be stripped before checking
    expect(isRateLimitError("See https://docs.github.com/rate-limit-exceeded for info")).toBe(
      false,
    );
  });

  test("returns false for normal output", () => {
    expect(isRateLimitError("Success")).toBe(false);
    expect(isRateLimitError("")).toBe(false);
  });
});
