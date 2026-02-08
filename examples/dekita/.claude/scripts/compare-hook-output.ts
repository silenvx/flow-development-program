#!/usr/bin/env bun
/**
 * Python/TypeScriptフック出力比較ツール
 *
 * Why:
 *   Python → TypeScript移行時に、両言語のフックが同じ入力に対して
 *   同じ出力を生成することを検証する必要がある。
 *
 * What:
 *   - 同じJSON入力をPython/TypeScriptフックに渡す
 *   - JSON出力を比較
 *   - 差異があればレポート
 *
 * Usage:
 *   # 単一入力での比較
 *   echo '{"tool_name": "Bash", "tool_input": {...}}' | \
 *     bun run .claude/scripts/compare-hook-output.ts \
 *       .claude/hooks/uv_run_guard.py \
 *       .claude/hooks/handlers/uv_run_guard.ts
 *
 *   # ファイルからの入力
 *   bun run .claude/scripts/compare-hook-output.ts \
 *     .claude/hooks/uv_run_guard.py \
 *     .claude/hooks/handlers/uv_run_guard.ts \
 *     --input test-input.json
 *
 * Changelog:
 *   - silenvx/dekita#2852: 初期実装
 */

import { parseArgs } from "node:util";

interface HookOutput {
  decision?: string;
  reason?: string;
  systemMessage?: string;
  continue?: boolean;
  [key: string]: unknown;
}

interface ComparisonResult {
  match: boolean;
  pythonOutput: HookOutput | null;
  tsOutput: HookOutput | null;
  pythonExitCode: number;
  tsExitCode: number;
  pythonError?: string;
  tsError?: string;
  differences: string[];
}

/**
 * フックを実行して出力を取得
 */
async function runHook(
  hookPath: string,
  input: string,
): Promise<{ output: HookOutput | null; exitCode: number; error?: string; stderr: string }> {
  const isPython = hookPath.endsWith(".py");
  const command = isPython ? ["python3", hookPath] : ["bun", "run", hookPath];

  try {
    const proc = Bun.spawn(command, {
      stdin: new Blob([input]),
      stdout: "pipe",
      stderr: "pipe",
    });

    const [stdout, stderr] = await Promise.all([
      new Response(proc.stdout).text(),
      new Response(proc.stderr).text(),
    ]);
    const exitCode = await proc.exited;

    // JSON出力をパース
    let output: HookOutput | null = null;
    const trimmedStdout = stdout.trim();
    if (trimmedStdout) {
      try {
        output = JSON.parse(trimmedStdout);
      } catch {
        return {
          output: null,
          exitCode,
          error: `Invalid JSON: ${trimmedStdout.slice(0, 100)}`,
          stderr,
        };
      }
    }

    return { output, exitCode, stderr };
  } catch (error) {
    return {
      output: null,
      exitCode: -1,
      error: String(error),
      stderr: "",
    };
  }
}

/**
 * 出力を比較
 */
function compareOutputs(
  pythonResult: Awaited<ReturnType<typeof runHook>>,
  tsResult: Awaited<ReturnType<typeof runHook>>,
): ComparisonResult {
  const differences: string[] = [];

  // parseエラーがある場合はcomparison failure
  // 両方のフックが無効なJSONを出力した場合でも、エラーとして検出
  if (pythonResult.error) {
    differences.push(`Python error: ${pythonResult.error}`);
  }
  if (tsResult.error) {
    differences.push(`TypeScript error: ${tsResult.error}`);
  }

  // Exit code比較
  if (pythonResult.exitCode !== tsResult.exitCode) {
    differences.push(`Exit code: Python=${pythonResult.exitCode}, TS=${tsResult.exitCode}`);
  }

  // decision比較
  const pyDecision = pythonResult.output?.decision;
  const tsDecision = tsResult.output?.decision;
  if (pyDecision !== tsDecision) {
    differences.push(`decision: Python="${pyDecision}", TS="${tsDecision}"`);
  }

  // continue比較（早期リターン用）
  const pyContinue = pythonResult.output?.continue;
  const tsContinue = tsResult.output?.continue;
  if (pyContinue !== tsContinue) {
    differences.push(`continue: Python=${pyContinue}, TS=${tsContinue}`);
  }

  // reason比較（存在する場合）
  // Note: reasonの内容は多少異なっても問題なし（フック名プレフィックスなど）
  // ここでは存在の有無だけチェック
  const pyHasReason = !!pythonResult.output?.reason;
  const tsHasReason = !!tsResult.output?.reason;
  if (pyHasReason !== tsHasReason) {
    differences.push(`reason presence: Python=${pyHasReason}, TS=${tsHasReason}`);
  }

  // systemMessage比較（存在の有無）
  const pyHasMessage = !!pythonResult.output?.systemMessage;
  const tsHasMessage = !!tsResult.output?.systemMessage;
  if (pyHasMessage !== tsHasMessage) {
    differences.push(`systemMessage presence: Python=${pyHasMessage}, TS=${tsHasMessage}`);
  }

  return {
    match: differences.length === 0,
    pythonOutput: pythonResult.output,
    tsOutput: tsResult.output,
    pythonExitCode: pythonResult.exitCode,
    tsExitCode: tsResult.exitCode,
    pythonError: pythonResult.error,
    tsError: tsResult.error,
    differences,
  };
}

/**
 * メイン処理
 */
async function main(): Promise<void> {
  const { values, positionals } = parseArgs({
    args: Bun.argv.slice(2),
    options: {
      input: { type: "string", short: "i" },
      verbose: { type: "boolean", short: "v", default: false },
      help: { type: "boolean", short: "h", default: false },
    },
    allowPositionals: true,
  });

  if (values.help || positionals.length < 2) {
    console.log(`
Usage: compare-hook-output.ts <python-hook> <ts-hook> [options]

Arguments:
  python-hook   Path to Python hook (e.g., .claude/hooks/uv_run_guard.py)
  ts-hook       Path to TypeScript hook (e.g., .claude/hooks/handlers/uv_run_guard.ts)

Options:
  -i, --input   Path to JSON input file (default: stdin)
  -v, --verbose Show detailed output
  -h, --help    Show this help

Example:
  echo '{"tool_name": "Bash"}' | bun run compare-hook-output.ts hook.py hook.ts
    `);
    process.exit(values.help ? 0 : 1);
  }

  const pythonHook = positionals[0];
  const tsHook = positionals[1];

  // 入力を取得
  let input: string;
  if (values.input) {
    input = await Bun.file(values.input).text();
  } else {
    // Bun.stdin.text()を使用してUTF-8破損を防ぐ
    // TextDecoderでchunkごとにdecodeするとマルチバイト文字が分割された場合に破損する
    input = await Bun.stdin.text();
  }

  if (!input.trim()) {
    console.error("Error: No input provided. Use --input or pipe JSON to stdin.");
    process.exit(1);
  }

  console.log("=".repeat(60));
  console.log("Python/TypeScriptフック出力比較");
  console.log("=".repeat(60));
  console.log(`Python: ${pythonHook}`);
  console.log(`TypeScript: ${tsHook}`);
  console.log("");

  if (values.verbose) {
    console.log("Input:");
    console.log(input.trim().slice(0, 200) + (input.length > 200 ? "..." : ""));
    console.log("");
  }

  // フックを実行
  console.log("Executing hooks...");
  const pythonResult = await runHook(pythonHook, input);
  const tsResult = await runHook(tsHook, input);

  // 比較
  const comparison = compareOutputs(pythonResult, tsResult);

  // 結果表示
  console.log(`\n${"-".repeat(60)}`);
  console.log("Results:");
  console.log("-".repeat(60));

  console.log("\nPython:");
  console.log(`  Exit code: ${comparison.pythonExitCode}`);
  if (comparison.pythonError) {
    console.log(`  Error: ${comparison.pythonError}`);
  }
  if (comparison.pythonOutput) {
    console.log(
      `  Output: ${JSON.stringify(comparison.pythonOutput, null, 2).split("\n").join("\n  ")}`,
    );
  }

  console.log("\nTypeScript:");
  console.log(`  Exit code: ${comparison.tsExitCode}`);
  if (comparison.tsError) {
    console.log(`  Error: ${comparison.tsError}`);
  }
  if (comparison.tsOutput) {
    console.log(
      `  Output: ${JSON.stringify(comparison.tsOutput, null, 2).split("\n").join("\n  ")}`,
    );
  }

  console.log(`\n${"=".repeat(60)}`);
  if (comparison.match) {
    console.log("✅ 出力は互換性があります");
  } else {
    console.log("❌ 差異が検出されました:");
    for (const diff of comparison.differences) {
      console.log(`   - ${diff}`);
    }
  }

  process.exit(comparison.match ? 0 : 1);
}

main().catch((error) => {
  console.error(`Error: ${error}`);
  process.exit(1);
});
