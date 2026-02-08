import { describe, expect, test } from "bun:test";
import { formatError } from "./format_error";

describe("formatError", () => {
  test("returns stack trace for Error with stack", () => {
    const err = new Error("boom");
    expect(formatError(err)).toContain("boom");
    expect(formatError(err)).toContain("format_error.test.ts");
  });

  test("returns message when stack is undefined", () => {
    const err = new Error("no stack");
    err.stack = undefined;
    expect(formatError(err)).toBe("no stack");
  });

  test("returns string for string error", () => {
    expect(formatError("something failed")).toBe("something failed");
  });

  test("returns string for number", () => {
    expect(formatError(42)).toBe("42");
  });

  test("returns string for null", () => {
    expect(formatError(null)).toBe("null");
  });

  test("returns string for undefined", () => {
    expect(formatError(undefined)).toBe("undefined");
  });

  test("returns string for plain object", () => {
    const result = formatError({ code: "ERR", msg: "fail" });
    expect(result).toBe("[object Object]");
  });

  test("handles TypeError subclass", () => {
    const err = new TypeError("not a function");
    expect(formatError(err)).toContain("not a function");
  });
});
