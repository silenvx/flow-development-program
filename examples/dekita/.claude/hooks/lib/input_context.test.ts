/**
 * input_context.ts のテスト
 *
 * Changelog:
 *   - silenvx/dekita#3879: 初期実装（カバレッジ改善）
 */

import { describe, expect, test } from "bun:test";
import {
  extractInputContext,
  getExitCode,
  getToolResult,
  mergeDetailsWithContext,
} from "./input_context";

describe("extractInputContext", () => {
  describe("truncateWithEllipsis edge cases", () => {
    test("should not add ellipsis when maxLen <= 3", () => {
      // Issue #3879: Cover line 42 - maxLen <= 3 case
      const input = {
        tool_name: "Bash",
        tool_input: { command: "echo hello" },
      };
      const result = extractInputContext(input, 3);
      // With maxLen=3, "ech" (first 3 chars) without ellipsis
      expect(result.input_preview).toBe("ech");
    });

    test("should truncate without ellipsis when maxLen is 2", () => {
      const input = {
        tool_name: "Bash",
        tool_input: { command: "test" },
      };
      const result = extractInputContext(input, 2);
      expect(result.input_preview).toBe("te");
    });

    test("should truncate without ellipsis when maxLen is 1", () => {
      const input = {
        tool_name: "Bash",
        tool_input: { command: "test" },
      };
      const result = extractInputContext(input, 1);
      expect(result.input_preview).toBe("t");
    });
  });

  describe("UserPromptSubmit hook type", () => {
    test("should detect UserPromptSubmit with user_prompt string", () => {
      // Issue #3879: Cover lines 122-125
      const input = {
        user_prompt: "Hello, Claude!",
      };
      const result = extractInputContext(input);
      expect(result.hook_type).toBe("UserPromptSubmit");
      expect(result.input_preview).toBe("Hello, Claude!");
    });

    test("should truncate long user_prompt", () => {
      const longPrompt = "a".repeat(100);
      const input = {
        user_prompt: longPrompt,
      };
      const result = extractInputContext(input, 10);
      expect(result.hook_type).toBe("UserPromptSubmit");
      expect(result.input_preview).toBe("aaaaaaa...");
      expect(result.input_preview?.length).toBe(10);
    });

    test("should handle non-string user_prompt", () => {
      const input = {
        user_prompt: 123, // non-string
      };
      const result = extractInputContext(
        input as unknown as Parameters<typeof extractInputContext>[0],
      );
      expect(result.hook_type).toBe("UserPromptSubmit");
      expect(result.input_preview).toBeUndefined();
    });
  });

  describe("other hook types", () => {
    test("should detect PreToolUse for tool with input", () => {
      const input = {
        tool_name: "Read",
        tool_input: { file_path: "/path/to/file.ts" },
      };
      const result = extractInputContext(input);
      expect(result.hook_type).toBe("PreToolUse");
      expect(result.tool_name).toBe("Read");
      expect(result.input_preview).toBe("/path/to/file.ts");
    });

    test("should detect PostToolUse when tool_output present", () => {
      const input = {
        tool_name: "Bash",
        tool_input: { command: "ls" },
        tool_output: { exit_code: 0, stdout: "file.txt" },
      };
      const result = extractInputContext(input);
      expect(result.hook_type).toBe("PostToolUse");
    });

    test("should detect Stop hook type", () => {
      const input = {
        stop_hook_active: true,
      };
      const result = extractInputContext(
        input as unknown as Parameters<typeof extractInputContext>[0],
      );
      expect(result.hook_type).toBe("Stop");
    });

    test("should detect Notification hook type", () => {
      const input = {
        notification: "some notification",
      };
      const result = extractInputContext(
        input as unknown as Parameters<typeof extractInputContext>[0],
      );
      expect(result.hook_type).toBe("Notification");
    });

    test("should return Unknown for empty input", () => {
      const input = {};
      const result = extractInputContext(input as Parameters<typeof extractInputContext>[0]);
      expect(result.hook_type).toBe("Unknown");
    });

    test("should detect SessionStart for input with data but no indicators", () => {
      const input = {
        session_id: "abc-123",
        some_other_field: "value",
      };
      const result = extractInputContext(
        input as unknown as Parameters<typeof extractInputContext>[0],
      );
      expect(result.hook_type).toBe("SessionStart");
    });
  });
});

describe("getToolResult", () => {
  // Issue #3879: Cover lines 175-183
  test("should return tool_result when present", () => {
    const input = {
      tool_result: { exit_code: 0, stdout: "success" },
    };
    const result = getToolResult(input);
    expect(result).toEqual({ exit_code: 0, stdout: "success" });
  });

  test("should return tool_response when tool_result not present", () => {
    const input = {
      tool_response: { exit_code: 1, stderr: "error" },
    };
    const result = getToolResult(input);
    expect(result).toEqual({ exit_code: 1, stderr: "error" });
  });

  test("should return tool_output as fallback", () => {
    const input = {
      tool_output: { exit_code: 2, stdout: "output" },
    };
    const result = getToolResult(input);
    expect(result).toEqual({ exit_code: 2, stdout: "output" });
  });

  test("should return undefined when no result fields present", () => {
    const input = {
      other_field: "value",
    };
    const result = getToolResult(input);
    expect(result).toBeUndefined();
  });

  test("should prioritize tool_result over tool_response and tool_output", () => {
    const input = {
      tool_result: { from: "result" },
      tool_response: { from: "response" },
      tool_output: { from: "output" },
    };
    const result = getToolResult(input);
    expect(result).toEqual({ from: "result" });
  });

  test("should handle string tool_result", () => {
    const input = {
      tool_result: "string result",
    };
    const result = getToolResult(input);
    expect(result).toBe("string result");
  });

  test("should handle null tool_result", () => {
    const input = {
      tool_result: null,
    };
    const result = getToolResult(input);
    expect(result).toBeNull();
  });
});

describe("getExitCode", () => {
  // Issue #3879: Cover lines 198-208
  test("should return exit_code from object", () => {
    const result = getExitCode({ exit_code: 42 });
    expect(result).toBe(42);
  });

  test("should return default value for null input", () => {
    const result = getExitCode(null);
    expect(result).toBe(0);
  });

  test("should return default value for undefined input", () => {
    const result = getExitCode(undefined);
    expect(result).toBe(0);
  });

  test("should return default value for string input", () => {
    const result = getExitCode("not an object");
    expect(result).toBe(0);
  });

  test("should return custom default value", () => {
    const result = getExitCode(null, 99);
    expect(result).toBe(99);
  });

  test("should return default when exit_code is not a number", () => {
    const result = getExitCode({ exit_code: "not a number" });
    expect(result).toBe(0);
  });

  test("should return 0 (default) when exit_code missing", () => {
    const result = getExitCode({ other_field: "value" });
    expect(result).toBe(0);
  });

  test("should handle exit_code of 0 correctly", () => {
    const result = getExitCode({ exit_code: 0 });
    expect(result).toBe(0);
  });
});

describe("mergeDetailsWithContext", () => {
  test("should merge details with context", () => {
    const details = { key1: "value1" };
    const context = {
      tool_name: "Bash",
      input_preview: "ls",
      hook_type: "PreToolUse" as const,
    };
    const result = mergeDetailsWithContext(details, context);
    expect(result).toEqual({
      tool_name: "Bash",
      input_preview: "ls",
      hook_type: "PreToolUse",
      key1: "value1",
    });
  });

  test("should handle null details", () => {
    const context = {
      tool_name: "Read",
      hook_type: "PreToolUse" as const,
    };
    const result = mergeDetailsWithContext(null, context);
    expect(result).toEqual({
      tool_name: "Read",
      hook_type: "PreToolUse",
    });
  });

  test("should handle undefined details", () => {
    const context = {
      hook_type: "Stop" as const,
    };
    const result = mergeDetailsWithContext(undefined, context);
    expect(result).toEqual({
      hook_type: "Stop",
    });
  });

  test("should prioritize details over context on key conflict", () => {
    const details = { tool_name: "FromDetails" };
    const context = {
      tool_name: "FromContext",
      hook_type: "PreToolUse" as const,
    };
    const result = mergeDetailsWithContext(details, context);
    expect(result.tool_name).toBe("FromDetails");
  });
});
