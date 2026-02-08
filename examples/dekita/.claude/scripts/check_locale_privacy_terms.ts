#!/usr/bin/env bun
/**
 * プライバシー/利用規約セクションがja.jsonのみに存在するか確認する。
 *
 * Why:
 *   プライバシーポリシーと利用規約は日本語のみ対応であり、
 *   他のロケールファイルに誤って追加されることを防ぐため。
 *
 * What:
 *   - main(): ロケールファイルをスキャンしprivacy/termsセクションを検出
 *
 * Remarks:
 *   - ja.json以外にprivacy/termsセクションがあればCI失敗
 *   - 他ロケールはfallbackLng: "ja"でフォールバック
 *
 * Changelog:
 *   - silenvx/dekita#1200: ロケールチェック機能を追加
 *   - silenvx/dekita#3636: TypeScriptに移植
 */

import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * Validate that privacy/terms sections only exist in ja.json.
 * Returns an array of error messages.
 */
export function validateLocales(localesDir: string): string[] {
  if (!existsSync(localesDir)) {
    return [`Error: Locales directory not found: ${localesDir}`];
  }

  const errors: string[] = [];
  const files = readdirSync(localesDir).filter((f) => f.endsWith(".json"));

  for (const filename of files) {
    // ja.json is allowed to have privacy/terms
    if (filename === "ja.json") {
      continue;
    }

    const filePath = join(localesDir, filename);

    try {
      const content = readFileSync(filePath, "utf-8");
      const data = JSON.parse(content) as Record<string, unknown>;

      // Check for privacy section (as object, not footer.privacy string)
      if ("privacy" in data && typeof data.privacy === "object" && data.privacy !== null) {
        errors.push(`${filename}: has 'privacy' section (should only be in ja.json)`);
      }

      // Check for terms section (as object, not footer.terms string)
      if ("terms" in data && typeof data.terms === "object" && data.terms !== null) {
        errors.push(`${filename}: has 'terms' section (should only be in ja.json)`);
      }
    } catch (e) {
      if (e instanceof SyntaxError) {
        errors.push(`${filename}: JSON parse error: ${e.message}`);
      } else {
        const message = e instanceof Error ? e.message : String(e);
        errors.push(`${filename}: Error: ${message}`);
      }
    }
  }

  return errors;
}

function main(): void {
  const localesDir = join(__dirname, "..", "..", "frontend/src/i18n/locales");
  const errors = validateLocales(localesDir);

  if (errors.length > 0) {
    if (errors[0].startsWith("Error:")) {
      console.error(errors[0]);
      process.exit(1);
    }
    console.log("❌ Locale privacy/terms check failed:");
    for (const error of errors) {
      console.log(`  - ${error}`);
    }
    console.log();
    console.log("Privacy policy and Terms of service should only be in ja.json.");
    console.log("Other locales use fallback to Japanese (fallbackLng: 'ja').");
    process.exit(1);
  }

  console.log("✅ Locale privacy/terms check passed");
  process.exit(0);
}

// Only run main when executed directly (not when imported for testing)
if (import.meta.main) {
  main();
}
