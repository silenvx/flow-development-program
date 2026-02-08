/**
 * Tests for record_review_response.ts
 *
 * Issue #3625: Add tests for TypeScript review_respond and record_review_response
 */

import { afterEach, beforeEach, describe, expect, spyOn, test } from "bun:test";
import { inferValidity, recordResponse } from "../record_review_response";

describe("inferValidity", () => {
  test('returns "valid" for "accepted"', () => {
    expect(inferValidity("accepted")).toBe("valid");
  });

  test('returns "invalid" for "rejected"', () => {
    expect(inferValidity("rejected")).toBe("invalid");
  });

  test('returns "valid" for "issue_created"', () => {
    expect(inferValidity("issue_created")).toBe("valid");
  });
});

describe("recordResponse", () => {
  // Mock console.error to reduce test output noise
  let consoleErrorSpy: ReturnType<typeof spyOn>;

  beforeEach(() => {
    consoleErrorSpy = spyOn(console, "error").mockImplementation(() => {
      // suppress error output
    });
  });

  afterEach(() => {
    consoleErrorSpy.mockRestore();
  });

  describe("numeric validation", () => {
    test("throws error for non-numeric prNumber", async () => {
      await expect(
        recordResponse({
          prNumber: "abc",
          commentId: "123",
          resolution: "accepted",
        }),
      ).rejects.toThrow("Invalid numeric ID");
    });

    test("throws error for non-numeric commentId", async () => {
      await expect(
        recordResponse({
          prNumber: "123",
          commentId: "abc",
          resolution: "accepted",
        }),
      ).rejects.toThrow("Invalid numeric ID");
    });

    test("throws error for partial numeric prNumber like 123abc", async () => {
      await expect(
        recordResponse({
          prNumber: "123abc",
          commentId: "456",
          resolution: "accepted",
        }),
      ).rejects.toThrow("Invalid numeric ID");
    });

    test("throws error for negative prNumber", async () => {
      await expect(
        recordResponse({
          prNumber: "-123",
          commentId: "456",
          resolution: "accepted",
        }),
      ).rejects.toThrow("Invalid numeric ID");
    });

    test("throws error for decimal prNumber", async () => {
      await expect(
        recordResponse({
          prNumber: "123.45",
          commentId: "456",
          resolution: "accepted",
        }),
      ).rejects.toThrow("Invalid numeric ID");
    });

    test("throws error for prNumber with spaces", async () => {
      await expect(
        recordResponse({
          prNumber: " 123 ",
          commentId: "456",
          resolution: "accepted",
        }),
      ).rejects.toThrow("Invalid numeric ID");
    });

    test("throws error for empty prNumber", async () => {
      await expect(
        recordResponse({
          prNumber: "",
          commentId: "456",
          resolution: "accepted",
        }),
      ).rejects.toThrow("Invalid numeric ID");
    });
  });
});
