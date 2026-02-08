#!/usr/bin/env bun
/**
 * Python/TypeScriptフック出力の差分比較ツール。
 *
 * Why:
 *   TypeScript移行時に、Python版とTypeScript版のフックが
 *   同等の出力を生成するか検証する必要がある。
 *
 * What:
 *   - 同じ入力を両方のフックに渡して実行
 *   - stdout JSON（キー順序無視）、終了コード、stderrを比較
 *   - 差異があればレポート出力
 *
 * State:
 *   - reads: stdin (hook input JSON)
 *   - executes: Python hook, TypeScript hook
 *
 * Remarks:
 *   - stderrの差異は警告のみ（許容）
 *   - JSONキー順序は無視して比較（配列順序は保持）
 *
 * Changelog:
 *   - silenvx/dekita#2814: 初期実装
 */

import { existsSync } from "node:fs";
import { relative, resolve } from "node:path";
import { parseArgs } from "node:util";

const DEFAULT_TIMEOUT = 30000; // ミリ秒

interface HookResult {
  stdout: string;
  stderr: string;
  exitCode: number;
  parsedJson: unknown | null;
}

interface ComparisonResult {
  match: boolean;
  exitCodeMatch: boolean;
  jsonMatch: boolean;
  stderrDiff: boolean;
  pythonResult: HookResult;
  tsResult: HookResult;
  differences: string[];
}

async function runHook(
  command: string[],
  stdinData: string,
  timeout: number = DEFAULT_TIMEOUT,
): Promise<HookResult> {
  try {
    const proc = Bun.spawn(command, {
      stdin: new Blob([stdinData]),
      stdout: "pipe",
      stderr: "pipe",
    });

    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    const timeoutPromise = new Promise<never>((_, reject) => {
      timeoutId = setTimeout(() => {
        proc.kill();
        reject(new Error(`Timeout after ${timeout}ms`));
      }, timeout);
    });

    const resultPromise = (async () => {
      const stdout = await new Response(proc.stdout).text();
      const stderr = await new Response(proc.stderr).text();
      const exitCode = await proc.exited;

      // JSONパース試行
      let parsedJson: unknown | null = null;
      if (stdout.trim()) {
        try {
          parsedJson = JSON.parse(stdout);
        } catch {
          // 非JSON出力は想定内。生stdoutをそのまま比較に使用する。
        }
      }

      return {
        // stdout は Python 版との出力差分を「生のまま」比較したいため、末尾の空白や改行も含めてそのまま保持する。
        stdout,
        // stderr は人間向けのログ出力であり、末尾の改行などはノイズとして扱いたいので trim() してから比較する。
        stderr: stderr.trim(),
        exitCode,
        parsedJson,
      };
    })();

    const result = await Promise.race([resultPromise, timeoutPromise]);
    if (timeoutId) clearTimeout(timeoutId);
    return result;
  } catch (e) {
    const error = e as Error;
    if (error.message.includes("Timeout")) {
      return {
        stdout: "",
        stderr: error.message,
        exitCode: -1,
        parsedJson: null,
      };
    }
    return {
      stdout: "",
      stderr: `Command error: ${error.message}`,
      exitCode: -1,
      parsedJson: null,
    };
  }
}

function normalizeJson(obj: unknown): unknown {
  if (obj === null || obj === undefined) {
    return obj;
  }
  if (Array.isArray(obj)) {
    return obj.map(normalizeJson);
  }
  if (typeof obj === "object") {
    const sorted: Record<string, unknown> = {};
    for (const key of Object.keys(obj as Record<string, unknown>).sort()) {
      sorted[key] = normalizeJson((obj as Record<string, unknown>)[key]);
    }
    return sorted;
  }
  return obj;
}

function compareJson(a: unknown | null, b: unknown | null, rawA: string, rawB: string): boolean {
  // 両方JSONの場合: キー順序を無視して比較
  if (a !== null && b !== null) {
    return JSON.stringify(normalizeJson(a)) === JSON.stringify(normalizeJson(b));
  }
  // 片方のみJSON: 不一致
  if ((a === null) !== (b === null)) {
    return false;
  }
  // 両方非JSON: 生文字列を比較
  return rawA === rawB;
}

function compareHooks(pythonResult: HookResult, tsResult: HookResult): ComparisonResult {
  const differences: string[] = [];

  // 終了コード比較
  const exitCodeMatch = pythonResult.exitCode === tsResult.exitCode;
  if (!exitCodeMatch) {
    differences.push(`Exit code: Python=${pythonResult.exitCode}, TS=${tsResult.exitCode}`);
  }

  // JSON比較（非JSONの場合は生文字列を比較）
  const jsonMatch = compareJson(
    pythonResult.parsedJson,
    tsResult.parsedJson,
    pythonResult.stdout,
    tsResult.stdout,
  );
  if (!jsonMatch) {
    differences.push("stdout JSON: 差異あり");
    if (pythonResult.parsedJson !== null) {
      differences.push(`  Python: ${JSON.stringify(pythonResult.parsedJson)}`);
    } else {
      differences.push(`  Python (raw): ${pythonResult.stdout}`);
    }
    if (tsResult.parsedJson !== null) {
      differences.push(`  TS: ${JSON.stringify(tsResult.parsedJson)}`);
    } else {
      differences.push(`  TS (raw): ${tsResult.stdout}`);
    }
  }

  // stderr比較（警告のみ）
  const stderrDiff = pythonResult.stderr !== tsResult.stderr;

  // 全体のマッチ判定（stderrは含まない）
  const match = exitCodeMatch && jsonMatch;

  return {
    match,
    exitCodeMatch,
    jsonMatch,
    stderrDiff,
    pythonResult,
    tsResult,
    differences,
  };
}

function printReport(result: ComparisonResult, verbose = false): void {
  const status = result.match ? "✅ MATCH" : "❌ DIFF";
  console.log(`\n${"=".repeat(60)}`);
  console.log(`比較結果: ${status}`);
  console.log("=".repeat(60));

  console.log(`\n終了コード: ${result.exitCodeMatch ? "✅" : "❌"}`);
  console.log(`  Python: ${result.pythonResult.exitCode}`);
  console.log(`  TS:     ${result.tsResult.exitCode}`);

  console.log(`\nstdout JSON: ${result.jsonMatch ? "✅" : "❌"}`);

  if (result.stderrDiff) {
    console.log("\n⚠️  stderr差異（警告）:");
    console.log(`  Python: ${result.pythonResult.stderr.slice(0, 100) || "(empty)"}`);
    console.log(`  TS:     ${result.tsResult.stderr.slice(0, 100) || "(empty)"}`);
  }

  if (result.differences.length > 0) {
    console.log("\n差異詳細:");
    for (const diff of result.differences) {
      console.log(`  ${diff}`);
    }
  }

  if (verbose) {
    console.log("\n--- Python stdout ---");
    console.log(result.pythonResult.stdout || "(empty)");
    console.log("\n--- TS stdout ---");
    console.log(result.tsResult.stdout || "(empty)");
  }
}

async function main(): Promise<number> {
  const { values } = parseArgs({
    args: Bun.argv.slice(2),
    options: {
      python: { type: "string" },
      typescript: { type: "string" },
      ts: { type: "string" },
      timeout: { type: "string" },
      verbose: { type: "boolean", short: "v" },
      json: { type: "boolean" },
      "allow-any-path": { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
    allowPositionals: true,
  });

  if (values.help) {
    console.log(`
使用法: bun run diff_hook_output.ts --python <path> --typescript <path> [options]

オプション:
  --python <path>      Python版フックのパス
  --typescript, --ts   TypeScript版フックのパス
  --timeout <ms>       実行タイムアウト（ミリ秒、デフォルト: ${DEFAULT_TIMEOUT}）
  -v, --verbose        詳細出力
  --json               JSON形式で結果を出力
  --allow-any-path     信頼ディレクトリ外のパスも許可
  -h, --help           ヘルプを表示

使用例:
  echo '{"session_id":"xxx"}' | bun run diff_hook_output.ts \\
    --python .claude/hooks/date_context_injector.py \\
    --typescript .claude/hooks/handlers/date_context_injector.ts
`);
    return 0;
  }

  const pythonPath = values.python;
  const tsPath = values.typescript || values.ts;

  if (!pythonPath || !tsPath) {
    console.error("Error: --python and --typescript are required");
    return 1;
  }

  // パス検証
  if (!existsSync(pythonPath)) {
    console.error(`Error: Python hook not found: ${pythonPath}`);
    return 1;
  }

  if (!existsSync(tsPath)) {
    console.error(`Error: TypeScript hook not found: ${tsPath}`);
    return 1;
  }

  // セキュリティ: 信頼ディレクトリ内のパスのみ許可
  if (!values["allow-any-path"]) {
    const trustedDir = resolve(".claude/hooks");
    const pythonResolved = resolve(pythonPath);
    const tsResolved = resolve(tsPath);

    // パストラバーサル（..）やシンボリックリンクによるバイパスを防ぐため、
    // startsWith() ではなく relative() を使用して検証する。
    // relative() が ".." で始まる場合、trustedDir の外に出ていることを意味する。
    const pythonRelative = relative(trustedDir, pythonResolved);
    if (pythonRelative.startsWith("..") || pythonRelative.startsWith("/")) {
      console.error(
        `Error: Python hook must be in ${trustedDir}/ ` +
          `(got: ${pythonResolved}). Use --allow-any-path to override.`,
      );
      return 1;
    }

    const tsRelative = relative(trustedDir, tsResolved);
    if (tsRelative.startsWith("..") || tsRelative.startsWith("/")) {
      console.error(
        `Error: TypeScript hook must be in ${trustedDir}/ ` +
          `(got: ${tsResolved}). Use --allow-any-path to override.`,
      );
      return 1;
    }
  }

  // stdin読み取り
  const stdinData = await Bun.stdin.text();
  if (!stdinData) {
    console.error("Error: stdin expected (pipe JSON input)");
    return 1;
  }

  const timeout = values.timeout ? Number.parseInt(values.timeout, 10) : DEFAULT_TIMEOUT;

  // フック実行
  const pythonCmd = ["python3", pythonPath];
  const tsCmd = ["bun", "run", tsPath];

  if (values.verbose) {
    console.log(`Python command: ${pythonCmd.join(" ")}`);
    console.log(`TS command: ${tsCmd.join(" ")}`);
    const inputPreview = stdinData.slice(0, 100);
    const suffix = stdinData.length > 100 ? "..." : "";
    console.log(`Input: ${inputPreview}${suffix}`);
  }

  const pythonResult = await runHook(pythonCmd, stdinData, timeout);
  const tsResult = await runHook(tsCmd, stdinData, timeout);

  // 比較
  const comparison = compareHooks(pythonResult, tsResult);

  // 出力
  if (values.json) {
    const output = {
      match: comparison.match,
      exit_code_match: comparison.exitCodeMatch,
      json_match: comparison.jsonMatch,
      stderr_diff: comparison.stderrDiff,
      python: {
        exit_code: pythonResult.exitCode,
        stdout: pythonResult.stdout,
        stderr: pythonResult.stderr,
        parsed_json: pythonResult.parsedJson,
      },
      typescript: {
        exit_code: tsResult.exitCode,
        stdout: tsResult.stdout,
        stderr: tsResult.stderr,
        parsed_json: tsResult.parsedJson,
      },
      differences: comparison.differences,
    };
    console.log(JSON.stringify(output, null, 2));
  } else {
    printReport(comparison, values.verbose);
  }

  return comparison.match ? 0 : 1;
}

process.exit(await main());
