/**
 * Tests for check_sentry_usage module.
 *
 * Changelog:
 *   - silenvx/dekita#3641: TypeScriptテスト追加
 */

import { afterEach, beforeEach, describe, expect, spyOn, test } from "bun:test";
import * as fs from "node:fs";
import { BANNED_PATTERNS, checkFile, isInComment } from "./check_sentry_usage";

describe("check_sentry_usage", () => {
  describe("BANNED_PATTERNS", () => {
    test("includes Sentry.setTag pattern", () => {
      const pattern = BANNED_PATTERNS.find(([, name]) => name === "Sentry.setTag()");
      expect(pattern).toBeDefined();
      expect(pattern![0].test("Sentry.setTag(")).toBe(true);
      expect(pattern![0].test("Sentry.setTag (")).toBe(true);
    });

    test("includes Sentry.setContext pattern", () => {
      const pattern = BANNED_PATTERNS.find(([, name]) => name === "Sentry.setContext()");
      expect(pattern).toBeDefined();
      expect(pattern![0].test("Sentry.setContext(")).toBe(true);
    });

    test("includes Sentry.setUser pattern", () => {
      const pattern = BANNED_PATTERNS.find(([, name]) => name === "Sentry.setUser()");
      expect(pattern).toBeDefined();
      expect(pattern![0].test("Sentry.setUser(")).toBe(true);
    });

    test("includes Sentry.setExtra pattern", () => {
      const pattern = BANNED_PATTERNS.find(([, name]) => name === "Sentry.setExtra()");
      expect(pattern).toBeDefined();
      expect(pattern![0].test("Sentry.setExtra(")).toBe(true);
    });

    test("does not match partial words", () => {
      const pattern = BANNED_PATTERNS.find(([, name]) => name === "Sentry.setTag()");
      expect(pattern![0].test("MySentry.setTag(")).toBe(false);
      expect(pattern![0].test("NotSentry.setTag(")).toBe(false);
    });
  });

  describe("isInComment", () => {
    test("returns false when no comment present", () => {
      expect(isInComment("Sentry.setTag(", 0)).toBe(false);
    });

    test("returns true when pattern is after comment", () => {
      expect(isInComment("// Sentry.setTag(", 3)).toBe(true);
    });

    test("returns false when pattern is before comment", () => {
      expect(isInComment("Sentry.setTag( // comment", 0)).toBe(false);
    });

    test("ignores URL-style // (https://)", () => {
      expect(isInComment("const url = https://example.com Sentry.setTag(", 35)).toBe(false);
    });

    test("handles line with multiple // occurrences", () => {
      // First // is in URL, second is actual comment
      expect(isInComment("https://url // Sentry.setTag(", 16)).toBe(true);
    });

    test("returns true when match is inside comment", () => {
      const line = "  // Sentry.setTag('key', 'value')";
      expect(isInComment(line, 5)).toBe(true);
    });

    test("returns false when // is inside double-quoted string", () => {
      const line = 'const path = "root//child"; Sentry.setTag("key", "value");';
      const matchStart = line.indexOf("Sentry.setTag");
      expect(isInComment(line, matchStart)).toBe(false);
    });

    test("returns false when // is inside single-quoted string", () => {
      const line = "const path = 'root//child'; Sentry.setTag('key', 'value');";
      const matchStart = line.indexOf("Sentry.setTag");
      expect(isInComment(line, matchStart)).toBe(false);
    });

    test("returns false when // is inside template literal", () => {
      const line = "const path = `root//child`; Sentry.setTag(`key`, `value`);";
      const matchStart = line.indexOf("Sentry.setTag");
      expect(isInComment(line, matchStart)).toBe(false);
    });

    test("returns true when comment follows multiple strings", () => {
      const line = '"str1" + "str2" // Sentry.setTag("key", "value")';
      const matchStart = line.indexOf("Sentry.setTag");
      expect(isInComment(line, matchStart)).toBe(true);
    });

    test("returns true when // comment follows regex with quote", () => {
      const line = "const re = /foo'bar/; // Sentry.setTag('key', 'value')";
      const matchStart = line.indexOf("Sentry.setTag");
      expect(isInComment(line, matchStart)).toBe(true);
    });

    test("returns false when Sentry call follows regex with quote (not in comment)", () => {
      const line = "const re = /foo'bar/; Sentry.setTag('key', 'value');";
      const matchStart = line.indexOf("Sentry.setTag");
      expect(isInComment(line, matchStart)).toBe(false);
    });

    test("handles regex with flags", () => {
      const line = "const re = /test/gi; // Sentry.setTag('key', 'value')";
      const matchStart = line.indexOf("Sentry.setTag");
      expect(isInComment(line, matchStart)).toBe(true);
    });

    test("handles regex with escaped slash", () => {
      const line = "const re = /foo\\/bar/; // Sentry.setTag('key', 'value')";
      const matchStart = line.indexOf("Sentry.setTag");
      expect(isInComment(line, matchStart)).toBe(true);
    });

    test("handles regex with double quote inside", () => {
      const line = 'const re = /foo"bar/; // Sentry.setTag("key", "value")';
      const matchStart = line.indexOf("Sentry.setTag");
      expect(isInComment(line, matchStart)).toBe(true);
    });

    test("ignores // inside regex character class", () => {
      const line = "const re = /[//]/; Sentry.setTag('key', 'value')";
      const matchStart = line.indexOf("Sentry.setTag");
      expect(isInComment(line, matchStart)).toBe(false);
    });

    test("handles regex in return statement", () => {
      const line = "return /[//]/; Sentry.setTag('key', 'value')";
      const matchStart = line.indexOf("Sentry.setTag");
      expect(isInComment(line, matchStart)).toBe(false);
    });

    test("handles regex after arrow function", () => {
      const line = "const fn = () => /test/; Sentry.setTag('key', 'value')";
      const matchStart = line.indexOf("Sentry.setTag");
      expect(isInComment(line, matchStart)).toBe(false);
    });

    test("handles regex after closing parenthesis", () => {
      const line = "if (cond) /foo'bar/.test(x); // Sentry.setTag('key', 'value')";
      const matchStart = line.indexOf("Sentry.setTag");
      expect(isInComment(line, matchStart)).toBe(true);
    });

    test("handles regex with d flag (hasIndices)", () => {
      const line = "/foo/d; // Sentry.setTag('key', 'value')";
      const matchStart = line.indexOf("Sentry.setTag");
      expect(isInComment(line, matchStart)).toBe(true);
    });

    test("handles division after closing parenthesis correctly", () => {
      const line = "const x = (a) / b; // Sentry.setTag('key', 'value')";
      const matchStart = line.indexOf("Sentry.setTag");
      expect(isInComment(line, matchStart)).toBe(true);
    });
  });

  describe("checkFile", () => {
    let readFileSyncSpy: ReturnType<typeof spyOn>;

    beforeEach(() => {
      readFileSyncSpy = spyOn(fs, "readFileSync");
    });

    afterEach(() => {
      readFileSyncSpy.mockRestore();
    });

    test("returns empty array for clean file", () => {
      readFileSyncSpy.mockReturnValue(`
        import * as Sentry from "@sentry/cloudflare";

        Sentry.withScope((scope) => {
          scope.setTag("key", "value");
          Sentry.captureException(error);
        });
      `);

      const violations = checkFile("/path/to/clean.ts");
      expect(violations).toEqual([]);
    });

    test("detects Sentry.setTag violation", () => {
      readFileSyncSpy.mockReturnValue(`
        import * as Sentry from "@sentry/cloudflare";
        Sentry.setTag("key", "value");
      `);

      const violations = checkFile("/path/to/bad.ts");
      expect(violations.length).toBe(1);
      expect(violations[0].patternName).toBe("Sentry.setTag()");
      expect(violations[0].lineNumber).toBe(3);
    });

    test("detects multiple violations", () => {
      readFileSyncSpy.mockReturnValue(`
        Sentry.setTag("key", "value");
        Sentry.setContext("context", {});
        Sentry.setUser({ id: "123" });
        Sentry.setExtra("extra", data);
      `);

      const violations = checkFile("/path/to/bad.ts");
      expect(violations.length).toBe(4);
    });

    test("skips commented lines", () => {
      readFileSyncSpy.mockReturnValue(`
        // Sentry.setTag("key", "value");
        // This is a comment with Sentry.setContext
      `);

      const violations = checkFile("/path/to/commented.ts");
      expect(violations).toEqual([]);
    });

    test("handles file read error gracefully", () => {
      readFileSyncSpy.mockImplementation(() => {
        throw new Error("File not found");
      });

      // Should not throw, returns empty array
      const violations = checkFile("/path/to/nonexistent.ts");
      expect(violations).toEqual([]);
    });

    test("returns correct line content in violation", () => {
      readFileSyncSpy.mockReturnValue(`line1
        Sentry.setTag("key", "value");
line3`);

      const violations = checkFile("/path/to/file.ts");
      expect(violations.length).toBe(1);
      expect(violations[0].lineContent).toBe('Sentry.setTag("key", "value");');
    });
  });
});
