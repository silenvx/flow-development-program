#!/usr/bin/env bun
/**
 * AGENTS.mdの強制ルール数と実際のhook/CIチェック数の比率を算出する。
 *
 * Why:
 *   「仕組み化 = ドキュメント + 強制機構」原則の網羅性をメタ的に監視する。
 *   ルール数に対してhook/CIが少なすぎる場合、形骸化リスクがある。
 *
 * What:
 *   - AGENTS.mdから「禁止」「必須」「ブロック」ルールを抽出
 *   - .claude/hooks/handlers/ のhookファイル数を集計
 *   - .claude/scripts/ のCIスクリプト数を集計
 *   - カバレッジ率を算出して表示
 *
 * Remarks:
 *   - CIスクリプトとして実行（PRで.claude/またはAGENTS.mdが変更された場合）
 *   - 警告のみ（exit 0で情報表示）
 *   - ヒューリスティックな集計のため、正確なカバレッジではない
 *
 * Changelog:
 *   - silenvx/dekita#3976: 初期実装
 */

import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { ENFORCEMENT_KEYWORDS } from "../hooks/lib/constants";

/**
 * Extract enforcement rule lines from AGENTS.md.
 */
export function extractEnforcementRules(content: string): string[] {
  const lines = content.split("\n");
  const pattern = new RegExp(ENFORCEMENT_KEYWORDS.join("|"), "i");

  return lines.filter((line) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || /^[-|:\s]+$/.test(trimmed)) return false;
    return pattern.test(trimmed);
  });
}

/**
 * Count hook files in .claude/hooks/handlers/.
 */
export function countHookFiles(projectRoot: string): number {
  try {
    const hooksDir = resolve(projectRoot, ".claude/hooks/handlers");
    return readdirSync(hooksDir).filter((f) => f.endsWith(".ts")).length;
  } catch {
    return 0;
  }
}

/**
 * Count CI script files in .claude/scripts/.
 */
export function countCIScripts(projectRoot: string): number {
  try {
    const scriptsDir = resolve(projectRoot, ".claude/scripts");
    return readdirSync(scriptsDir).filter(
      (f) => (f.endsWith(".ts") && !f.endsWith(".test.ts")) || f.endsWith(".sh"),
    ).length;
  } catch {
    return 0;
  }
}

function main(): void {
  const projectRoot = process.env.CLAUDE_PROJECT_DIR || process.cwd();
  const agentsMdPath = resolve(projectRoot, "AGENTS.md");

  let content: string;
  try {
    content = readFileSync(agentsMdPath, "utf-8");
  } catch {
    console.log("ℹ️ AGENTS.md not found. Skipping enforcement coverage audit.");
    process.exit(0);
  }

  const enforcementRules = extractEnforcementRules(content);
  const hookCount = countHookFiles(projectRoot);
  const ciScriptCount = countCIScripts(projectRoot);
  const totalEnforcement = hookCount + ciScriptCount;

  const ratio =
    enforcementRules.length > 0 ? (totalEnforcement / enforcementRules.length).toFixed(2) : "N/A";

  console.log("📊 Enforcement Coverage Audit");
  console.log("============================");
  console.log(`AGENTS.md 強制ルール数: ${enforcementRules.length}`);
  console.log(`Hook ファイル数: ${hookCount}`);
  console.log(`CI スクリプト数: ${ciScriptCount}`);
  console.log(`強制機構合計: ${totalEnforcement}`);
  console.log(`比率 (機構/ルール): ${ratio}`);
  console.log("");

  if (enforcementRules.length > 0 && totalEnforcement < enforcementRules.length) {
    console.log("⚠️ 強制ルール数に対して強制機構が少ない可能性があります。");
    console.log("   一部のルールが形骸化していないか確認してください。");
    console.log("");
    console.log("サンプルルール (最大5件):");
    for (const rule of enforcementRules.slice(0, 5)) {
      console.log(`  - ${rule.trim().substring(0, 100)}`);
    }
  } else {
    console.log("✅ 強制機構の数はルール数を上回っています。");
  }
}

if (import.meta.main) {
  main();
}
