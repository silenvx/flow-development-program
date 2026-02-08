import { describe, expect, test } from "bun:test";
import {
  countCIScripts,
  countHookFiles,
  extractEnforcementRules,
} from "./enforcement_coverage_audit";

describe("enforcement_coverage_audit", () => {
  describe("extractEnforcementRules", () => {
    test("extracts table rows with enforcement keywords", () => {
      const content = `# Rules

| ❌ 禁止 | 理由 |
| ---- | ---- |
| 必須 | 常に実行 |
`;
      const rules = extractEnforcementRules(content);
      expect(rules).toHaveLength(2);
    });

    test("extracts bullet points with enforcement keywords", () => {
      const content = `# Rules

- **禁止**: やらないこと
- **推奨**: やること
- ブロックされる操作
`;
      const rules = extractEnforcementRules(content);
      // 禁止 and ブロック match, 推奨 does not match enforcement keywords
      expect(rules).toHaveLength(2);
    });

    test("ignores headers and separators", () => {
      const content = `# 禁止事項

| --- | --- |
`;
      const rules = extractEnforcementRules(content);
      expect(rules).toHaveLength(0);
    });

    test("matches plain text paragraphs with keywords", () => {
      const content = "禁止事項についての説明文です。";
      const rules = extractEnforcementRules(content);
      expect(rules).toHaveLength(1);
    });

    test("returns empty for empty content", () => {
      expect(extractEnforcementRules("")).toHaveLength(0);
    });

    test("detects してはならない keyword", () => {
      const content = "- コードをしてはならない操作";
      const rules = extractEnforcementRules(content);
      expect(rules).toHaveLength(1);
    });
  });

  describe("countHookFiles", () => {
    test("counts .ts files in hooks directory", () => {
      // resolve to project root (two levels up from .claude/scripts/)
      const projectRoot =
        process.env.CLAUDE_PROJECT_DIR || new URL("../../", import.meta.url).pathname;
      const count = countHookFiles(projectRoot);
      // Should find at least some hooks
      expect(count).toBeGreaterThan(0);
    });

    test("returns 0 for non-existent directory", () => {
      const count = countHookFiles("/non/existent/path");
      expect(count).toBe(0);
    });
  });

  describe("countCIScripts", () => {
    test("counts .ts and .sh files in scripts directory", () => {
      const projectRoot =
        process.env.CLAUDE_PROJECT_DIR || new URL("../../", import.meta.url).pathname;
      const count = countCIScripts(projectRoot);
      expect(count).toBeGreaterThan(0);
    });

    test("returns 0 for non-existent directory", () => {
      const count = countCIScripts("/non/existent/path");
      expect(count).toBe(0);
    });
  });
});
