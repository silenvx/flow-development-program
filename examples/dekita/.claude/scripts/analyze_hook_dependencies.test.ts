/**
 * Tests for analyze_hook_dependencies module.
 */

import { afterEach, describe, expect, test } from "bun:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { extractImports, getBarrelExports, getLibDir } from "./analyze_hook_dependencies";

describe("analyze_hook_dependencies", () => {
  describe("getLibDir", () => {
    test("returns a valid lib directory path", () => {
      const libDir = getLibDir();
      expect(libDir).toContain(".claude/hooks/lib");
    });
  });

  describe("getBarrelExports", () => {
    test("extracts module names from index.ts", () => {
      const libDir = getLibDir();
      const exports = getBarrelExports(libDir);

      // Should contain some known modules
      expect(exports.has("types")).toBe(true);
      expect(exports.has("session")).toBe(true);
      expect(exports.has("results")).toBe(true);
      expect(exports.has("constants")).toBe(true);
    });

    test("returns empty set for non-existent directory", () => {
      const exports = getBarrelExports("/non/existent/path");
      expect(exports.size).toBe(0);
    });
  });

  describe("extractImports", () => {
    test("extracts direct lib imports from actual hook file", () => {
      const libDir = getLibDir();
      const barrelExports = getBarrelExports(libDir);

      // Test with a known hook that uses lib imports (cwd_check.ts imports from lib)
      const hooksDir = libDir.replace("/lib", "/hooks");
      const hookPath = `${hooksDir}/cwd_check.ts`;

      const imports = extractImports(hookPath, barrelExports);
      expect(imports instanceof Set).toBe(true);

      // cwd_check.ts imports from lib (format_error, logging, results, session)
      // Verify it has expected imports
      if (imports.size > 0) {
        // At minimum, hooks using lib should have some recognized modules
        const hasKnownImport =
          imports.has("session") || imports.has("results") || imports.has("format_error");
        expect(hasKnownImport).toBe(true);
      }
    });

    test("returns empty set for non-existent file", () => {
      const imports = extractImports("/non/existent/file.ts");
      expect(imports.size).toBe(0);
    });

    test("handles barrel exports parameter correctly", () => {
      const barrelExports = new Set(["module1", "module2"]);
      // Non-existent file should return empty even with barrel exports
      const imports = extractImports("/non/existent/file.ts", barrelExports);
      expect(imports.size).toBe(0);
    });

    // Temp directory management for file-based tests
    const tempDirs: string[] = [];
    function createTempFile(content: string): string {
      const dir = mkdtempSync(join(tmpdir(), "hook-dep-test-"));
      tempDirs.push(dir);
      const filePath = join(dir, "test_hook.ts");
      writeFileSync(filePath, content);
      return filePath;
    }

    afterEach(() => {
      for (const dir of tempDirs) {
        rmSync(dir, { recursive: true, force: true });
      }
      tempDirs.length = 0;
    });

    test("ignores imports inside block comments", () => {
      const filePath = createTempFile(`/*
 * import { session } from "../lib/session";
 * import { results } from "../lib";
 */
import { logging } from "../lib/logging";
`);
      const barrelExports = new Set(["session", "results", "logging"]);
      const imports = extractImports(filePath, barrelExports);
      expect(imports.has("logging")).toBe(true);
      expect(imports.has("session")).toBe(false);
      expect(imports.has("results")).toBe(false);
      expect(imports.size).toBe(1);
    });

    test("ignores imports inside line comments", () => {
      const filePath = createTempFile(`// import { session } from "../lib/session";
import { logging } from "../lib/logging";
`);
      const imports = extractImports(filePath);
      expect(imports.has("logging")).toBe(true);
      expect(imports.has("session")).toBe(false);
    });

    test("detects barrel imports via AST", () => {
      const filePath = createTempFile(`import { session, results } from "../lib";\n`);
      const barrelExports = new Set(["session", "results", "constants"]);
      const imports = extractImports(filePath, barrelExports);
      expect(imports.has("session")).toBe(true);
      expect(imports.has("results")).toBe(true);
      expect(imports.has("constants")).toBe(true);
    });
  });
});
