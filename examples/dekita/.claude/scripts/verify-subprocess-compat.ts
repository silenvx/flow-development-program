#!/usr/bin/env bun

interface TestCase {
  name: string;
  command: string[];
  expectedExitCode: number;
  description: string;
}

interface TestResult {
  name: string;
  bunResult: {
    stdout: string;
    stderr: string;
    exitCode: number;
    error?: string;
  };
  pythonResult: {
    stdout: string;
    stderr: string;
    exitCode: number;
    error?: string;
  };
  match: boolean;
  differences: string[];
}

const TEST_CASES: TestCase[] = [
  {
    name: "git-rev-parse",
    command: ["git", "rev-parse", "--abbrev-ref", "HEAD"],
    expectedExitCode: 0,
    description: "ブランチ名取得（多くのフックで使用）",
  },
  {
    name: "git-status",
    command: ["git", "status", "--porcelain"],
    expectedExitCode: 0,
    description: "変更ファイル一覧（差分チェックで使用）",
  },
  {
    name: "echo-unicode",
    command: ["echo", "日本語テスト🎉"],
    expectedExitCode: 0,
    description: "Unicode/絵文字出力",
  },
  {
    name: "nonexistent-command",
    command: ["nonexistent_command_12345"],
    expectedExitCode: -1, // will fail
    description: "存在しないコマンド実行時のエラー処理",
  },
  {
    name: "git-log-format",
    command: ["git", "log", "-1", "--format=%H\t%an\t%s"],
    expectedExitCode: 0,
    description: "タブ区切り出力のパース",
  },
];

/**
 * Bunでコマンドを実行
 */
async function runWithBun(
  command: string[],
): Promise<{ stdout: string; stderr: string; exitCode: number; error?: string }> {
  try {
    const proc = Bun.spawn(command, {
      stdout: "pipe",
      stderr: "pipe",
    });

    const [stdout, stderr] = await Promise.all([
      new Response(proc.stdout).text(),
      new Response(proc.stderr).text(),
    ]);
    const exitCode = await proc.exited;

    return { stdout, stderr, exitCode };
  } catch (error) {
    return {
      stdout: "",
      stderr: "",
      exitCode: -1,
      error: String(error),
    };
  }
}

/**
 * Pythonでコマンドを実行
 */
async function runWithPython(
  command: string[],
): Promise<{ stdout: string; stderr: string; exitCode: number; error?: string }> {
  // Python script to run subprocess
  const pythonScript = `
import subprocess
import sys
import json

command = ${JSON.stringify(command)}
try:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )
    print(json.dumps({
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exitCode": result.returncode,
    }))
except FileNotFoundError as e:
    print(json.dumps({
        "stdout": "",
        "stderr": str(e),
        "exitCode": -1,
        "error": "FileNotFoundError",
    }))
except Exception as e:
    print(json.dumps({
        "stdout": "",
        "stderr": str(e),
        "exitCode": -1,
        "error": str(type(e).__name__),
    }))
`;

  try {
    const proc = Bun.spawn(["python3", "-c", pythonScript], {
      stdout: "pipe",
      stderr: "pipe",
    });

    const stdout = await new Response(proc.stdout).text();
    await proc.exited;

    const result = JSON.parse(stdout.trim());
    return result;
  } catch (error) {
    return {
      stdout: "",
      stderr: "",
      exitCode: -1,
      error: `Python execution failed: ${error}`,
    };
  }
}

/**
 * テスト結果を比較
 */
function compareResults(
  bunResult: TestResult["bunResult"],
  pythonResult: TestResult["pythonResult"],
): { match: boolean; differences: string[] } {
  const differences: string[] = [];

  // Exit code comparison
  // Note: Bun returns 1 for FileNotFoundError, Python returns -1
  const bunExitNormalized = bunResult.error?.includes("ENOENT") ? -1 : bunResult.exitCode;
  const pythonExitNormalized =
    pythonResult.error === "FileNotFoundError" ? -1 : pythonResult.exitCode;

  if (bunExitNormalized !== pythonExitNormalized) {
    differences.push(`Exit code: Bun=${bunResult.exitCode}, Python=${pythonResult.exitCode}`);
  }

  // Stdout comparison (trim for comparison)
  if (bunResult.stdout.trim() !== pythonResult.stdout.trim()) {
    differences.push(
      `Stdout differs:\n  Bun: "${bunResult.stdout.trim().slice(0, 100)}"\n  Python: "${pythonResult.stdout.trim().slice(0, 100)}"`,
    );
  }

  // Stderr comparison is more lenient (error messages vary)
  // Just check if both have errors or both are empty
  const bunHasError = bunResult.stderr.length > 0 || !!bunResult.error;
  const pythonHasError = pythonResult.stderr.length > 0 || !!pythonResult.error;
  if (bunHasError !== pythonHasError) {
    differences.push(
      `Stderr presence differs: Bun has error=${bunHasError}, Python has error=${pythonHasError}`,
    );
  }

  return {
    match: differences.length === 0,
    differences,
  };
}

/**
 * メイン処理
 */
async function main(): Promise<void> {
  console.log("=".repeat(60));
  console.log("Python/Bun subprocess互換性検証");
  console.log("=".repeat(60));
  console.log("");

  const results: TestResult[] = [];

  for (const testCase of TEST_CASES) {
    console.log(`\n🔍 Testing: ${testCase.name}`);
    console.log(`   Command: ${testCase.command.join(" ")}`);
    console.log(`   ${testCase.description}`);

    const bunResult = await runWithBun(testCase.command);
    const pythonResult = await runWithPython(testCase.command);

    const { match, differences } = compareResults(bunResult, pythonResult);

    results.push({
      name: testCase.name,
      bunResult,
      pythonResult,
      match,
      differences,
    });

    if (match) {
      console.log("   ✅ Match");
    } else {
      console.log("   ❌ Differences found:");
      for (const diff of differences) {
        console.log(`      - ${diff}`);
      }
    }
  }

  // Summary
  console.log(`\n${"=".repeat(60)}`);
  console.log("Summary");
  console.log("=".repeat(60));

  const passed = results.filter((r) => r.match).length;
  const failed = results.filter((r) => !r.match).length;

  console.log(`\nTotal: ${results.length} tests`);
  console.log(`  ✅ Passed: ${passed}`);
  console.log(`  ❌ Failed: ${failed}`);

  // Recommendations
  console.log(`\n${"=".repeat(60)}`);
  console.log("Recommendations");
  console.log("=".repeat(60));

  if (failed === 0) {
    console.log("\n✅ Python/Bun subprocess呼び出しは互換性があります。");
    console.log("   TypeScript移行時のsubprocess使用に問題はありません。");
  } else {
    console.log("\n⚠️ 一部の差異が検出されました。");
    console.log("   以下の点に注意してTypeScript移行を進めてください:\n");

    for (const result of results.filter((r) => !r.match)) {
      console.log(`   - ${result.name}:`);
      for (const diff of result.differences) {
        console.log(`     ${diff}`);
      }
    }
  }

  // Exit with error if any test failed
  process.exit(failed > 0 ? 1 : 0);
}

main().catch((error) => {
  console.error(`Error: ${error}`);
  process.exit(1);
});
