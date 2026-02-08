#!/usr/bin/env bun
/**
 * セッション開始時にローカルmainブランチの同期状態を確認する。
 *
 * Why:
 *   ローカルmainがリモートより遅れていると、新しいフック/修正が
 *   適用されず問題が発生する。同期状態を確認し早期に警告する。
 *
 * What:
 *   - git fetchでリモート情報を更新
 *   - ローカルmainとorigin/mainのコミット差分を確認
 *   - 大きく遅れている場合は警告を出力
 *   - 不審なコミットパターン（同一メッセージ連続）を検出
 *
 * Remarks:
 *   - 警告型フック（ブロックしない、stderrで警告）
 *   - SessionStartで発火（セッション毎に1回）
 *   - 閾値: 5コミット以上遅れで警告、3回以上同一メッセージで不審判定
 *   - ネットワークエラー時はサイレント（フェッチ失敗は警告しない）
 *
 * Changelog:
 *   - silenvx/dekita#2874: TypeScriptに移植
 */

import { spawn } from "node:child_process";
import { TIMEOUT_LIGHT, TIMEOUT_MEDIUM } from "../lib/constants";
import { getDefaultBranch } from "../lib/git";
import { logHookExecution } from "../lib/logging";
import { checkAndUpdateSessionMarker, parseHookInput } from "../lib/session";

const HOOK_NAME = "main-sync-check";

// 警告を出すコミット数の閾値
export const BEHIND_THRESHOLD = 5;

// 不審なコミットパターンの閾値（同じメッセージの連続）
export const SUSPICIOUS_COMMIT_THRESHOLD = 3;

/**
 * Find the maximum consecutive duplicates in an array of strings.
 * Returns [count, message] tuple where count is the max consecutive count
 * and message is the duplicated message (or null if no duplicates).
 */
export function findMaxConsecutiveDuplicates(messages: string[]): [number, string | null] {
  if (messages.length === 0) {
    return [0, null];
  }

  let currentMsg = messages[0];
  let count = 1;
  let maxCount = 1;
  let maxMsg: string | null = null;

  for (let i = 1; i < messages.length; i++) {
    const msg = messages[i];
    if (msg === currentMsg) {
      count++;
      if (count > maxCount) {
        maxCount = count;
        maxMsg = currentMsg;
      }
    } else {
      currentMsg = msg;
      count = 1;
    }
  }

  return [maxCount, maxMsg];
}

/**
 * Check if a divergence exceeds the warning threshold.
 */
export function shouldWarnBehind(behind: number): boolean {
  return behind >= BEHIND_THRESHOLD;
}

/**
 * Check if consecutive duplicates exceed the suspicious threshold.
 */
export function isSuspiciousPattern(count: number): boolean {
  return count >= SUSPICIOUS_COMMIT_THRESHOLD;
}

/**
 * Run a command with timeout support.
 */
async function runCommand(
  command: string,
  args: string[],
  timeout: number = TIMEOUT_LIGHT,
): Promise<{ stdout: string; exitCode: number | null }> {
  return new Promise((resolve) => {
    const proc = spawn(command, args, {
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let killed = false;

    const timer = setTimeout(() => {
      killed = true;
      proc.kill("SIGTERM");
    }, timeout * 1000);

    proc.stdout?.on("data", (data) => {
      stdout += data.toString();
    });

    proc.on("close", (exitCode) => {
      clearTimeout(timer);
      if (killed) {
        resolve({ stdout: "", exitCode: null });
      } else {
        resolve({ stdout, exitCode });
      }
    });

    proc.on("error", () => {
      clearTimeout(timer);
      resolve({ stdout: "", exitCode: null });
    });
  });
}

/**
 * リモート情報をフェッチする。
 */
async function fetchRemote(): Promise<boolean> {
  const result = await runCommand("git", ["fetch", "origin"], TIMEOUT_MEDIUM);
  return result.exitCode === 0;
}

/**
 * ローカルデフォルトブランチとoriginの差分を取得する。
 *
 * @param defaultBranch - The default branch name (e.g., "main", "master").
 * @param originDefaultBranch - The origin default branch name (e.g., "origin/main").
 * @returns [behind, ahead] のタプル
 */
async function getMainDivergence(
  defaultBranch: string,
  originDefaultBranch: string,
): Promise<[number, number]> {
  try {
    // ローカルデフォルトブランチが存在するか確認
    const verifyResult = await runCommand("git", ["rev-parse", "--verify", defaultBranch]);
    if (verifyResult.exitCode !== 0) {
      return [0, 0];
    }

    // behind: originにあってローカルにないコミット数
    const behindResult = await runCommand("git", [
      "rev-list",
      "--count",
      `${defaultBranch}..${originDefaultBranch}`,
    ]);
    const behindStr = behindResult.stdout.trim();
    const behind = behindResult.exitCode === 0 && behindStr ? Number.parseInt(behindStr, 10) : 0;

    // ahead: ローカルにあってoriginにないコミット数
    const aheadResult = await runCommand("git", [
      "rev-list",
      "--count",
      `${originDefaultBranch}..${defaultBranch}`,
    ]);
    const aheadStr = aheadResult.stdout.trim();
    const ahead = aheadResult.exitCode === 0 && aheadStr ? Number.parseInt(aheadStr, 10) : 0;

    return [behind, ahead];
  } catch {
    return [0, 0];
  }
}

/**
 * ローカルデフォルトブランチに不審なコミットパターンがないか確認する。
 *
 * @param defaultBranch - The default branch name (e.g., "main", "master").
 * @returns [hasSuspicious, count, message] のタプル
 */
async function checkSuspiciousCommits(
  defaultBranch: string,
): Promise<[boolean, number, string | null]> {
  try {
    const result = await runCommand("git", ["log", "--format=%s", "-20", defaultBranch]);
    if (result.exitCode !== 0) {
      return [false, 0, null];
    }

    const rawOutput = result.stdout.trim();
    if (!rawOutput) {
      return [false, 0, null];
    }

    const messages = rawOutput.split("\n");
    const [maxCount, maxMsg] = findMaxConsecutiveDuplicates(messages);

    if (isSuspiciousPattern(maxCount)) {
      return [true, maxCount, maxMsg];
    }

    return [false, 0, null];
  } catch {
    return [false, 0, null];
  }
}

async function main(): Promise<void> {
  // セッションIDの取得のためparse_hook_inputを呼び出す
  const hookInput = await parseHookInput();
  const sessionId = hookInput.session_id;

  // セッション毎に1回だけ実行
  if (!checkAndUpdateSessionMarker(HOOK_NAME)) {
    return;
  }

  // デフォルトブランチを動的に取得
  const defaultBranch = (await getDefaultBranch(process.cwd())) || "main";
  const originDefaultBranch = `origin/${defaultBranch}`;

  // リモート情報を更新
  if (!(await fetchRemote())) {
    // フェッチ失敗は警告しない（ネットワーク問題の可能性）
    return;
  }

  // 差分を確認
  const [behind, ahead] = await getMainDivergence(defaultBranch, originDefaultBranch);

  const warnings: string[] = [];

  if (shouldWarnBehind(behind)) {
    warnings.push(
      `⚠️ ローカル${defaultBranch}が${originDefaultBranch}より${behind}コミット遅れています。\n   \`git pull\` で${defaultBranch}を更新することを推奨します。`,
    );
  }

  if (ahead > 0) {
    warnings.push(
      `⚠️ ローカル${defaultBranch}が${originDefaultBranch}より${ahead}コミット進んでいます。\n   これは異常な状態の可能性があります。\`git status\` で確認してください。`,
    );
  }

  // 不審なコミットパターンをチェック
  const [hasSuspicious, suspiciousCount, suspiciousMsg] =
    await checkSuspiciousCommits(defaultBranch);
  if (hasSuspicious && suspiciousMsg) {
    warnings.push(
      `⚠️ ${defaultBranch}に不審なコミットパターンを検出しました。\n   「${suspiciousMsg}」が${suspiciousCount}回連続しています。\n   \`git reset --hard ${originDefaultBranch}\` での復旧を検討してください。`,
    );
  }

  if (warnings.length > 0) {
    await logHookExecution(
      HOOK_NAME,
      "warn",
      `Main sync warnings detected: behind=${behind}, ahead=${ahead}`,
      { behind, ahead, has_suspicious: hasSuspicious },
      { sessionId },
    );
    console.log(`[${HOOK_NAME}] main同期状態の警告:\n`);
    for (const warning of warnings) {
      console.log(warning);
      console.log();
    }
  }
}

if (import.meta.main) {
  main();
}
