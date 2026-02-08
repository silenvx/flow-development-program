/**
 * Tests for validate_lefthook module.
 *
 * Changelog:
 *   - silenvx/dekita#3641: TypeScriptテスト追加
 */

import { afterEach, describe, expect, test } from "bun:test";
import {
  type LefthookConfig,
  checkStagedFilesInPrePush,
  findLineNumber,
  getParseYaml,
  resetParseYamlCache,
} from "./validate_lefthook";

describe("validate_lefthook", () => {
  describe("getParseYaml", () => {
    afterEach(() => {
      resetParseYamlCache();
    });

    test("does not throw and returns a function or null", () => {
      const parseYaml = getParseYaml();
      // yaml パッケージの有無にかかわらず、例外を投げずに
      // function または null のいずれかを返すことを確認する
      expect(parseYaml === null || typeof parseYaml === "function").toBe(true);
    });

    test("caches the result on subsequent calls", () => {
      const first = getParseYaml();
      const second = getParseYaml();
      expect(first).toBe(second);
    });

    test("resetParseYamlCache allows re-initialization", () => {
      const first = getParseYaml();
      resetParseYamlCache();
      const second = getParseYaml();
      // Both should be the same function (or both null)
      // The important thing is that reset allowed re-initialization
      expect(first === null || typeof first === "function").toBe(true);
      expect(second === null || typeof second === "function").toBe(true);
    });
  });

  describe("findLineNumber", () => {
    test("finds text on first line", () => {
      const content = "first line\nsecond line\nthird line";
      expect(findLineNumber(content, "first")).toBe(1);
    });

    test("finds text on middle line", () => {
      const content = "first line\nsecond line\nthird line";
      expect(findLineNumber(content, "second")).toBe(2);
    });

    test("finds text on last line", () => {
      const content = "first line\nsecond line\nthird line";
      expect(findLineNumber(content, "third")).toBe(3);
    });

    test("returns 0 when text not found", () => {
      const content = "first line\nsecond line\nthird line";
      expect(findLineNumber(content, "not found")).toBe(0);
    });

    test("handles empty content", () => {
      expect(findLineNumber("", "anything")).toBe(0);
    });

    test("handles partial match", () => {
      const content = "pre-push:\n  commands:\n    run: echo hello";
      expect(findLineNumber(content, "run: echo")).toBe(3);
    });
  });

  describe("checkStagedFilesInPrePush", () => {
    test("returns empty array when no pre-push section", () => {
      const config: LefthookConfig = {};
      const content = "";
      const errors = checkStagedFilesInPrePush(config, content, "lefthook.yml");
      expect(errors).toEqual([]);
    });

    test("returns empty array when pre-push has no commands", () => {
      const config: LefthookConfig = {
        "pre-push": {},
      };
      const content = "";
      const errors = checkStagedFilesInPrePush(config, content, "lefthook.yml");
      expect(errors).toEqual([]);
    });

    test("returns empty array when commands don't use staged_files", () => {
      const config: LefthookConfig = {
        "pre-push": {
          commands: {
            test: {
              run: "npm test",
            },
          },
        },
      };
      const content = "pre-push:\n  commands:\n    test:\n      run: npm test";
      const errors = checkStagedFilesInPrePush(config, content, "lefthook.yml");
      expect(errors).toEqual([]);
    });

    test("detects staged_files in pre-push command", () => {
      const config: LefthookConfig = {
        "pre-push": {
          commands: {
            lint: {
              run: "eslint {staged_files}",
            },
          },
        },
      };
      const content = "pre-push:\n  commands:\n    lint:\n      run: eslint {staged_files}";
      const errors = checkStagedFilesInPrePush(config, content, "lefthook.yml");

      expect(errors.length).toBe(1);
      expect(errors[0].code).toBe("LEFTHOOK001");
      expect(errors[0].file).toBe("lefthook.yml");
      expect(errors[0].message).toContain("lint");
      expect(errors[0].message).toContain("staged_files");
    });

    test("detects multiple commands with staged_files", () => {
      const config: LefthookConfig = {
        "pre-push": {
          commands: {
            lint: {
              run: "eslint {staged_files}",
            },
            format: {
              run: "prettier {staged_files}",
            },
            test: {
              run: "npm test",
            },
          },
        },
      };
      const content =
        "pre-push:\n  commands:\n    lint:\n      run: eslint {staged_files}\n    format:\n      run: prettier {staged_files}\n    test:\n      run: npm test";
      const errors = checkStagedFilesInPrePush(config, content, "lefthook.yml");

      expect(errors.length).toBe(2);
      expect(errors.some((e) => e.message.includes("lint"))).toBe(true);
      expect(errors.some((e) => e.message.includes("format"))).toBe(true);
    });

    test("skips non-object command configs", () => {
      const config: LefthookConfig = {
        "pre-push": {
          commands: {
            // Force a non-object value for testing
            invalid: null as unknown as { run?: string },
          },
        },
      };
      const content = "";
      const errors = checkStagedFilesInPrePush(config, content, "lefthook.yml");
      expect(errors).toEqual([]);
    });

    test("handles command without run property", () => {
      const config: LefthookConfig = {
        "pre-push": {
          commands: {
            noRun: {
              // No run property
            },
          },
        },
      };
      const content = "";
      const errors = checkStagedFilesInPrePush(config, content, "lefthook.yml");
      expect(errors).toEqual([]);
    });

    test("detects staged_files in array format run command", () => {
      const config: LefthookConfig = {
        "pre-push": {
          commands: {
            lint: {
              run: ["eslint {staged_files}", "prettier --check ."],
            },
          },
        },
      };
      const content =
        "pre-push:\n  commands:\n    lint:\n      run:\n        - eslint {staged_files}\n        - prettier --check .";
      const errors = checkStagedFilesInPrePush(config, content, "lefthook.yml");

      expect(errors.length).toBe(1);
      expect(errors[0].code).toBe("LEFTHOOK001");
      expect(errors[0].message).toContain("lint");
      expect(errors[0].message).toContain("staged_files");
    });

    test("detects multiple staged_files in array format run command", () => {
      const config: LefthookConfig = {
        "pre-push": {
          commands: {
            lint: {
              run: ["eslint {staged_files}", "prettier {staged_files}"],
            },
          },
        },
      };
      const content = "";
      const errors = checkStagedFilesInPrePush(config, content, "lefthook.yml");

      expect(errors.length).toBe(2);
    });

    test("handles empty array run command", () => {
      const config: LefthookConfig = {
        "pre-push": {
          commands: {
            empty: {
              run: [],
            },
          },
        },
      };
      const content = "";
      const errors = checkStagedFilesInPrePush(config, content, "lefthook.yml");
      expect(errors).toEqual([]);
    });
  });
});
