/**
 * Tests for validate_hooks_settings module.
 *
 * Changelog:
 *   - silenvx/dekita#3641: TypeScriptテスト追加
 */

import { describe, expect, test } from "bun:test";
import { type Settings, extractHookPaths } from "./validate_hooks_settings";

describe("validate_hooks_settings", () => {
  describe("extractHookPaths", () => {
    const projectDir = "/project";

    test("returns empty array when no hooks defined", () => {
      const settings: Settings = {};
      const result = extractHookPaths(settings, projectDir);
      expect(result).toEqual([]);
    });

    test("returns empty array when hooks section is empty", () => {
      const settings: Settings = { hooks: {} };
      const result = extractHookPaths(settings, projectDir);
      expect(result).toEqual([]);
    });

    test("extracts Python hook path with $CLAUDE_PROJECT_DIR", () => {
      const settings: Settings = {
        hooks: {
          PreToolUse: [
            {
              hooks: [
                {
                  type: "command",
                  command: 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/my_hook.py',
                },
              ],
            },
          ],
        },
      };
      const result = extractHookPaths(settings, projectDir);

      expect(result.length).toBe(1);
      expect(result[0][0]).toContain("python3");
      expect(result[0][1]).toBe("/project/.claude/hooks/my_hook.py");
    });

    test("extracts Bun hook path with $CLAUDE_PROJECT_DIR", () => {
      const settings: Settings = {
        hooks: {
          PostToolUse: [
            {
              hooks: [
                {
                  type: "command",
                  command: 'bun run "$CLAUDE_PROJECT_DIR"/.claude/hooks/handlers/my_hook.ts',
                },
              ],
            },
          ],
        },
      };
      const result = extractHookPaths(settings, projectDir);

      expect(result.length).toBe(1);
      expect(result[0][0]).toContain("bun run");
      expect(result[0][1]).toBe("/project/.claude/hooks/handlers/my_hook.ts");
    });

    test("extracts paths without quotes around $CLAUDE_PROJECT_DIR", () => {
      const settings: Settings = {
        hooks: {
          Stop: [
            {
              hooks: [
                {
                  type: "command",
                  command: "python3 $CLAUDE_PROJECT_DIR/.claude/hooks/stop_hook.py",
                },
              ],
            },
          ],
        },
      };
      const result = extractHookPaths(settings, projectDir);

      expect(result.length).toBe(1);
      expect(result[0][1]).toBe("/project/.claude/hooks/stop_hook.py");
    });

    test("extracts paths from multiple hook types", () => {
      const settings: Settings = {
        hooks: {
          PreToolUse: [
            {
              hooks: [
                {
                  type: "command",
                  command: 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/pre_hook.py',
                },
              ],
            },
          ],
          PostToolUse: [
            {
              hooks: [
                {
                  type: "command",
                  command: 'bun run "$CLAUDE_PROJECT_DIR"/.claude/hooks/handlers/post_hook.ts',
                },
              ],
            },
          ],
          SessionStart: [
            {
              hooks: [
                {
                  type: "command",
                  command: 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/session_hook.py',
                },
              ],
            },
          ],
        },
      };
      const result = extractHookPaths(settings, projectDir);

      expect(result.length).toBe(3);
    });

    test("skips non-command hook types", () => {
      const settings: Settings = {
        hooks: {
          PreToolUse: [
            {
              hooks: [
                {
                  type: "script",
                  command: 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/hook.py',
                },
              ],
            },
          ],
        },
      };
      const result = extractHookPaths(settings, projectDir);
      expect(result).toEqual([]);
    });

    test("skips hooks without command property", () => {
      const settings: Settings = {
        hooks: {
          PreToolUse: [
            {
              hooks: [
                {
                  type: "command",
                  // No command property
                },
              ],
            },
          ],
        },
      };
      const result = extractHookPaths(settings, projectDir);
      expect(result).toEqual([]);
    });

    test("extracts multiple hooks from same group", () => {
      const settings: Settings = {
        hooks: {
          PreToolUse: [
            {
              hooks: [
                {
                  type: "command",
                  command: 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/hook1.py',
                },
                {
                  type: "command",
                  command: 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/hook2.py',
                },
              ],
            },
          ],
        },
      };
      const result = extractHookPaths(settings, projectDir);

      expect(result.length).toBe(2);
      expect(result[0][1]).toBe("/project/.claude/hooks/hook1.py");
      expect(result[1][1]).toBe("/project/.claude/hooks/hook2.py");
    });

    test("handles UserPromptSubmit hook type", () => {
      const settings: Settings = {
        hooks: {
          UserPromptSubmit: [
            {
              hooks: [
                {
                  type: "command",
                  command: 'bun run "$CLAUDE_PROJECT_DIR"/.claude/hooks/handlers/prompt_hook.ts',
                },
              ],
            },
          ],
        },
      };
      const result = extractHookPaths(settings, projectDir);

      expect(result.length).toBe(1);
      expect(result[0][1]).toBe("/project/.claude/hooks/handlers/prompt_hook.ts");
    });
  });
});
