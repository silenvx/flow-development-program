#!/usr/bin/env bun
/**
 * mainブランチとの型エラー数を比較する。
 *
 * **用途**: 手動分析用スクリプト（CI/pre-pushには未統合）
 *
 * Why:
 *   PRがtype errorの総数を増加させていないか確認し、
 *   技術的負債の蓄積を防ぐため。
 *
 * What:
 *   - countCurrentErrors(): 現在のブランチの型エラー数を取得
 *   - countMainErrors(): mainブランチの型エラー数を取得
 *   - compareErrors(): エラー数を比較して結果を報告
 *
 * Remarks:
 *   - エラー数が増加した場合は警告（ブロックはしない）
 *   - エラー数が減少した場合は称賛のメッセージを表示
 *   - 手動実行: `bun run .claude/scripts/compare_typecheck_errors.ts`
 *
 * Limitations:
 *   - ブランチ切り替え時にdependency syncを行わないため、
 *     依存関係が異なる場合は不正確な結果となる可能性がある
 *   - ローカル開発での参考情報として使用を推奨
 *
 * Changelog:
 *   - silenvx/dekita#3464: エラー数比較機能を追加
 */

import { execFileSync, spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";

/** Get project root directory from script location. */
export function getProjectRoot(): string {
  const scriptDir = dirname(import.meta.path);
  // .claude/scripts → project root
  return resolve(scriptDir, "..", "..");
}

/**
 * Typecheck commands to run independently.
 * Running them separately ensures all subprojects are counted even if one fails.
 */
const TYPECHECK_COMMANDS = ["pnpm typecheck:frontend", "pnpm typecheck:worker"];

/**
 * Count type errors in current branch.
 *
 * @returns Number of type errors, or -1 if counting failed
 */
export function countCurrentErrors(): number {
  const projectRoot = getProjectRoot();
  let totalErrors = 0;

  // Run each typecheck command independently to count all errors
  // (pnpm typecheck uses && which stops on first failure)
  for (const command of TYPECHECK_COMMANDS) {
    const result = spawnSync(command, {
      encoding: "utf-8",
      cwd: projectRoot,
      shell: true,
      env: { ...process.env, FORCE_COLOR: "0" },
    });

    // Handle spawn errors (e.g., shell not available)
    if (result.error) {
      console.error(`Failed to spawn ${command}: ${result.error.message}`);
      return -1;
    }

    // With shell: true, "command not found" results in non-zero status with stderr message.
    // Check if output contains TypeScript error patterns to distinguish from spawn failures.
    const output = result.stdout + result.stderr;

    // Skip if no errors (status 0)
    if (result.status === 0) {
      continue;
    }

    // Check for command not found patterns (shell-specific error messages)
    if (
      output.includes("command not found") ||
      output.includes("not recognized as") ||
      output.includes("is not recognized")
    ) {
      console.error(`Command failed: ${command}`);
      console.error(output);
      return -1;
    }

    totalErrors += countErrorsInOutput(output);
  }

  return totalErrors;
}

/**
 * Count type errors in main branch.
 *
 * @returns Number of type errors, or -1 if counting failed
 */
export function countMainErrors(): number {
  const projectRoot = getProjectRoot();

  try {
    // Stash any uncommitted changes
    // Use execFileSync to avoid command injection
    const hasChanges =
      execFileSync("git", ["status", "--porcelain"], { cwd: projectRoot, encoding: "utf-8" }).trim()
        .length > 0;

    // Track whether stash was actually created (git stash might skip if nothing to stash)
    let stashCreated = false;
    if (hasChanges) {
      // Include untracked files (-u) to ensure stash is always created when hasChanges is true
      const stashOutput = execFileSync(
        "git",
        ["stash", "push", "-u", "-m", "typecheck-compare-temp"],
        {
          cwd: projectRoot,
          encoding: "utf-8",
        },
      );
      // git stash outputs "Saved working directory..." when stash is created
      stashCreated = stashOutput.includes("Saved working directory");
    }

    // Save current branch (handle detached HEAD by saving commit hash)
    const branchName = execFileSync("git", ["rev-parse", "--abbrev-ref", "HEAD"], {
      cwd: projectRoot,
      encoding: "utf-8",
    }).trim();
    const currentBranch =
      branchName === "HEAD"
        ? execFileSync("git", ["rev-parse", "HEAD"], { cwd: projectRoot, encoding: "utf-8" }).trim()
        : branchName;

    try {
      // Attempt to fetch latest main branch, but proceed even if it fails (e.g., offline)
      try {
        execFileSync("git", ["fetch", "origin", "main"], {
          cwd: projectRoot,
          stdio: "ignore",
        });
      } catch (error) {
        console.warn(
          `\u26a0\ufe0f  Warning: Failed to fetch from origin. Using local origin/main. Error: ${JSON.stringify(error instanceof Error ? error.message : String(error))}`,
        );
      }

      // Checkout main
      execFileSync("git", ["checkout", "origin/main", "--detach"], {
        cwd: projectRoot,
        stdio: "ignore",
      });

      // Warn about potential dependency mismatch (see Limitations in file header)
      console.warn("\u26a0\ufe0f  Note: Running typecheck on main without updating node_modules.");
      console.warn("   If dependencies differ, results may be inaccurate.");

      // Count errors using independent subproject execution
      let errorCount = 0;
      for (const command of TYPECHECK_COMMANDS) {
        const result = spawnSync(command, {
          encoding: "utf-8",
          cwd: projectRoot,
          shell: true,
          env: { ...process.env, FORCE_COLOR: "0" },
        });

        // Handle spawn errors
        if (result.error) {
          console.error(`Failed to spawn ${command}: ${result.error.message}`);
          return -1;
        }

        if (result.status !== 0) {
          const output = result.stdout + result.stderr;
          // Check for command not found patterns
          if (
            output.includes("command not found") ||
            output.includes("not recognized as") ||
            output.includes("is not recognized")
          ) {
            console.error(`Command failed on main: ${command}`);
            return -1;
          }
          errorCount += countErrorsInOutput(output);
        }
      }

      return errorCount;
    } finally {
      // Restore original branch
      execFileSync("git", ["checkout", currentBranch], { cwd: projectRoot, stdio: "ignore" });

      // Restore stashed changes only if stash was actually created
      if (stashCreated) {
        try {
          execFileSync("git", ["stash", "pop"], { cwd: projectRoot, stdio: "ignore" });
        } catch {
          console.error(
            "\n\u26a0\ufe0f  WARNING: Failed to restore stashed changes (git stash pop).",
          );
          console.error(
            "Your changes are saved in the stash list. Please resolve conflicts manually.",
          );
        }
      }
    }
  } catch (error) {
    console.error(`\u26a0\ufe0f  Failed to count main branch errors: ${error}`);
    return -1;
  }
}

/**
 * Count error lines in tsc output.
 */
export function countErrorsInOutput(output: string): number {
  const lines = output.split(/\r?\n/);
  let count = 0;

  for (const line of lines) {
    // Match both formats:
    // file.ts(line,column): error TSxxxx: message
    // file.ts:line:column - error TSxxxx: message
    // Pattern: [cm]?[tj]sx? matches .ts, .tsx, .mts, .cts, .js, .jsx, .mjs, .cjs
    if (
      /\.[cm]?[tj]sx?\(\d+,\d+\):\s*error\s+TS\d+:/.test(line) ||
      /\.[cm]?[tj]sx?:\d+:\d+\s*-\s*error\s+TS\d+:/.test(line)
    ) {
      count++;
    }
  }

  return count;
}

/**
 * Compare result structure
 */
export interface CompareResult {
  currentCount: number;
  mainCount: number;
  diff: number;
  status: "improved" | "same" | "degraded" | "unknown";
}

/**
 * Compare error counts between current and main branch.
 */
export function compareErrors(): CompareResult {
  console.log("Counting type errors in current branch...");
  const currentCount = countCurrentErrors();

  // If current count failed, skip main branch check
  if (currentCount === -1) {
    return {
      currentCount: -1,
      mainCount: -1,
      diff: 0,
      status: "unknown",
    };
  }

  console.log("Counting type errors in main branch...");
  const mainCount = countMainErrors();

  if (mainCount === -1) {
    return {
      currentCount,
      mainCount: -1,
      diff: 0,
      status: "unknown",
    };
  }

  const diff = currentCount - mainCount;
  let status: CompareResult["status"];

  if (diff < 0) {
    status = "improved";
  } else if (diff > 0) {
    status = "degraded";
  } else {
    status = "same";
  }

  return { currentCount, mainCount, diff, status };
}

/**
 * Main entry point
 */
function main(): number {
  console.log("Comparing TypeScript type errors with main branch...\n");

  const result = compareErrors();

  console.log("\n--- Results ---");

  if (result.currentCount === -1) {
    console.log("Current branch: Unable to count (typecheck command failed)");
    console.log("Main branch: Skipped (comparison not possible)");
    return 0;
  }

  console.log(`Current branch: ${result.currentCount} error(s)`);

  if (result.status === "unknown") {
    console.log("Main branch: Unable to count (comparison skipped)");
    return 0;
  }

  console.log(`Main branch: ${result.mainCount} error(s)`);
  console.log(`Difference: ${result.diff > 0 ? "+" : ""}${result.diff}`);

  switch (result.status) {
    case "improved":
      console.log(`\n\u2705 Great! You reduced type errors by ${Math.abs(result.diff)}`);
      break;
    case "same":
      console.log("\n\u2139\ufe0f  No change in type error count");
      break;
    case "degraded":
      console.log(`\n\u26a0\ufe0f  Warning: Type errors increased by ${result.diff}`);
      console.log("Consider fixing these errors to avoid accumulating technical debt.");
      break;
  }

  // Always return 0 - this is informational only
  return 0;
}

// Execute
if (import.meta.main) {
  const exitCode = main();
  process.exit(exitCode);
}

// Export for testing
export { main };
