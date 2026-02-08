/**
 * Tests for check_locale_privacy_terms module.
 *
 * This script validates that privacy/terms sections only exist in ja.json.
 *
 * Changelog:
 *   - silenvx/dekita#3641: TypeScriptテスト追加
 */

import { afterAll, beforeEach, describe, expect, test } from "bun:test";
import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { validateLocales } from "./check_locale_privacy_terms";

const __dirname = dirname(fileURLToPath(import.meta.url));

describe("check_locale_privacy_terms", () => {
  const testDir = join(__dirname, "__test_locales__");

  afterAll(() => {
    // Clean up test directory
    if (existsSync(testDir)) {
      rmSync(testDir, { recursive: true });
    }
  });

  beforeEach(() => {
    // Clean up and recreate test directory before each test
    if (existsSync(testDir)) {
      rmSync(testDir, { recursive: true });
    }
    mkdirSync(testDir, { recursive: true });
  });

  describe("validateLocales", () => {
    test("returns error when directory not found", () => {
      const errors = validateLocales("/nonexistent/path");
      expect(errors.length).toBe(1);
      expect(errors[0]).toContain("Error: Locales directory not found");
    });

    test("returns empty array when ja.json has privacy section", () => {
      const jaJson = {
        privacy: {
          title: "Privacy Policy",
          content: "...",
        },
      };
      writeFileSync(join(testDir, "ja.json"), JSON.stringify(jaJson, null, 2));

      const errors = validateLocales(testDir);
      expect(errors).toEqual([]);
    });

    test("returns empty array when ja.json has terms section", () => {
      const jaJson = {
        terms: {
          title: "Terms of Service",
          content: "...",
        },
      };
      writeFileSync(join(testDir, "ja.json"), JSON.stringify(jaJson, null, 2));

      const errors = validateLocales(testDir);
      expect(errors).toEqual([]);
    });

    test("returns error when en.json has privacy section as object", () => {
      const enJson = {
        privacy: {
          title: "Privacy Policy",
        },
      };
      writeFileSync(join(testDir, "en.json"), JSON.stringify(enJson, null, 2));

      const errors = validateLocales(testDir);
      expect(errors.length).toBe(1);
      expect(errors[0]).toContain("en.json: has 'privacy' section");
    });

    test("returns no error when footer.privacy is a string", () => {
      const enJson = {
        footer: {
          privacy: "Privacy Policy", // This is a string, not an object
          terms: "Terms of Service",
        },
      };
      writeFileSync(join(testDir, "en.json"), JSON.stringify(enJson, null, 2));

      const errors = validateLocales(testDir);
      expect(errors).toEqual([]);
    });

    test("returns error when zh.json has terms section as object", () => {
      const zhJson = {
        terms: {
          title: "Terms",
        },
      };
      writeFileSync(join(testDir, "zh.json"), JSON.stringify(zhJson, null, 2));

      const errors = validateLocales(testDir);
      expect(errors.length).toBe(1);
      expect(errors[0]).toContain("zh.json: has 'terms' section");
    });

    test("returns error for invalid JSON", () => {
      writeFileSync(join(testDir, "broken.json"), "{ invalid json");

      const errors = validateLocales(testDir);
      expect(errors.length).toBe(1);
      expect(errors[0]).toContain("broken.json: JSON parse error");
    });

    test("returns empty array for empty locale file", () => {
      writeFileSync(join(testDir, "empty.json"), "{}");

      const errors = validateLocales(testDir);
      expect(errors).toEqual([]);
    });

    test("returns multiple errors for multiple violations", () => {
      const enJson = { privacy: { title: "..." } };
      const zhJson = { terms: { title: "..." } };
      writeFileSync(join(testDir, "en.json"), JSON.stringify(enJson));
      writeFileSync(join(testDir, "zh.json"), JSON.stringify(zhJson));

      const errors = validateLocales(testDir);
      expect(errors.length).toBe(2);
    });
  });
});
