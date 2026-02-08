/**
 * Claude Codeステータスラインの動的生成
 *
 * Why:
 *   現在のworktree/Issue/PR/フロー状態を
 *   ステータスラインに表示し、作業状況を可視化するため。
 *
 * What:
 *   - getLanguage(): 言語設定を取得
 *   - getWorktreeInfo(): worktree/ブランチ/PR情報を取得
 *   - getFlowState(): フローフェーズ・イテレーション情報を取得
 *   - sanitize(): Terminal Injection対策
 *
 * Remarks:
 *   - 入力: stdin JSON（model, workspace, session_id）
 *   - 出力: [Model] worktree | PR状態 | フロー | session_id
 *   - gh CLIタイムアウト: 2秒（遅延防止）
 *   - 多言語対応（ja/en）
 *
 * Changelog:
 *   - silenvx/dekita#2875: Shell版からTypeScript版へ移行
 */

import { existsSync, readFileSync } from "node:fs";
import { basename, join } from "node:path";
import { $ } from "bun";

// ========== 定数 ==========
const GH_TIMEOUT_MS = 2000;
// Shell版と同じcase-sensitiveパターン（issue-123形式のみ。Issue-123やISSUE-123は非マッチ）
const ISSUE_PATTERN = /issue-(\d+)/;

// ========== 型定義 ==========
interface StatusLineInput {
  model?: {
    display_name?: string;
  };
  workspace?: {
    current_dir?: string;
  };
  session_id?: string;
}

export interface PRInfo {
  number: number;
  state: "OPEN" | "MERGED" | "CLOSED";
}

interface FlowState {
  active_workflow?: string;
  workflows?: Record<
    string,
    {
      current_phase?: string;
      phases?: Record<
        string,
        {
          iterations?: number;
        }
      >;
    }
  >;
  global?: {
    hooks_fired_total?: number;
  };
}

// ========== 言語設定 ==========
type Language = "ja" | "en";

export interface Messages {
  reviewing: string;
  merged: string;
  closed: string;
  noPr: string;
}

function getLanguage(): Language {
  const statuslineLang = process.env.STATUSLINE_LANG;
  if (statuslineLang) {
    return statuslineLang === "en" ? "en" : "ja";
  }

  const lang = process.env.LANG;
  if (lang) {
    const langCode = lang.split("_")[0];
    return langCode === "en" ? "en" : "ja";
  }

  return "ja";
}

export function getMessages(lang: Language): Messages {
  if (lang === "en") {
    return {
      reviewing: "reviewing",
      merged: "merged",
      closed: "closed",
      noPr: "no PR",
    };
  }
  return {
    reviewing: "レビュー中",
    merged: "マージ済",
    closed: "クローズ",
    noPr: "PRなし",
  };
}

// ========== フェーズ名マッピング ==========
type PhaseNames = Record<string, string>;

const PHASE_NAMES_JA: PhaseNames = {
  session_start: "セッション開始",
  pre_check: "事前確認",
  worktree_create: "worktree作成",
  implementation: "実装",
  pre_commit_check: "コミット前検証",
  local_ai_review: "AIレビュー",
  pr_create: "PR作成",
  issue_work: "Issue作業",
  ci_review: "CIレビュー",
  merge: "マージ",
  cleanup: "クリーンアップ",
  production_check: "本番確認",
  session_end: "セッション終了",
};

const PHASE_NAMES_EN: PhaseNames = {
  session_start: "Session Start",
  pre_check: "Pre Check",
  worktree_create: "Worktree Create",
  implementation: "Implementation",
  pre_commit_check: "Pre Commit Check",
  local_ai_review: "AI Review",
  pr_create: "PR Create",
  issue_work: "Issue Work",
  ci_review: "CI Review",
  merge: "Merge",
  cleanup: "Cleanup",
  production_check: "Production Check",
  session_end: "Session End",
};

function getPhaseNames(lang: Language): PhaseNames {
  return lang === "en" ? PHASE_NAMES_EN : PHASE_NAMES_JA;
}

function getPhaseName(phase: string, phaseNames: PhaseNames): string {
  return phaseNames[phase] ?? phase;
}

// ========== Issue番号抽出 ==========
function extractIssueNumber(input: string): string | null {
  const match = input.match(ISSUE_PATTERN);
  return match ? match[1] : null;
}

// ========== サニタイズ ==========
/**
 * ANSIエスケープシーケンスと制御文字を除去（Terminal Injection対策）
 */
function sanitize(input: string): string {
  // ANSIエスケープシーケンスを除去
  // ESC文字(0x1B)を明示的に検出するため、制御文字の使用は意図的
  // biome-ignore lint/suspicious/noControlCharactersInRegex: Terminal Injection対策として制御文字の検出が必要
  const withoutAnsi = input.replace(/\x1b\[[0-9;]*[mGKHflSTABCDEFnsuJha-zA-Z]/g, "");
  // 制御文字を除去（0x00-0x1F, 0x7F）
  // biome-ignore lint/suspicious/noControlCharactersInRegex: Terminal Injection対策として制御文字の検出が必要
  return withoutAnsi.replace(/[\x00-\x1f\x7f]/g, "");
}

// ========== PR状態マッピング ==========
export function formatPrInfo(pr: PRInfo, messages: Messages): string {
  const stateMessages: Record<PRInfo["state"], string> = {
    OPEN: messages.reviewing,
    MERGED: messages.merged,
    CLOSED: messages.closed,
  };
  const stateMessage = stateMessages[pr.state] ?? "";
  return stateMessage ? `PR #${pr.number} ${stateMessage}` : `PR #${pr.number}`;
}

// ========== Git/PR情報取得 ==========
async function getWorktreeInfo(dir: string, messages: Messages): Promise<string | null> {
  // Gitリポジトリかチェック & git-dirをキャッシュ（重複呼び出し削減）
  let gitDir: string;
  try {
    const gitDirResult = await $`git -C ${dir} rev-parse --git-dir`.quiet();
    gitDir = gitDirResult.text().trim();
  } catch {
    return null;
  }

  // 現在のブランチ名
  let branch: string;
  try {
    const result = await $`git -C ${dir} branch --show-current`.quiet();
    branch = result.text().trim();
    if (!branch) return null;
  } catch {
    return null;
  }

  // worktree名を抽出（キャッシュしたgitDirを使用）
  let worktreeName = "";
  try {
    if (gitDir.includes("/.worktrees/")) {
      const match = gitDir.match(/\.worktrees\/([^/]+)\//);
      if (match) {
        worktreeName = match[1];
      }
    } else if (dir.includes("/.worktrees/")) {
      const match = dir.match(/\.worktrees\/([^/]+)/);
      if (match) {
        worktreeName = match[1];
      }
    }
  } catch {
    // worktree名の取得に失敗しても続行
  }

  // Issue番号を抽出
  let issueNum: string | null = null;
  if (worktreeName) {
    issueNum = extractIssueNumber(worktreeName);
  }
  if (!issueNum) {
    issueNum = extractIssueNumber(branch);
  }

  // PR情報を取得（gh CLIが使える場合）
  // タイムアウトを設定してステータスライン更新の遅延を防ぐ
  // Bun Shellの.timeout()が期待通りに動作しない場合があるため、Promise.raceで明示的に制御
  let prInfo = "";
  try {
    const timeoutPromise = new Promise<null>((resolve) =>
      setTimeout(() => resolve(null), GH_TIMEOUT_MS).unref(),
    );
    const ghPromise = $`gh pr list --head ${branch} --state all --json number,state --limit 1`
      .cwd(dir)
      .quiet();
    ghPromise.catch(() => {
      // 未処理のrejection警告を防止
    });

    const result = await Promise.race([ghPromise, timeoutPromise]);

    if (result === null) {
      // タイムアウト: ghプロセスはバックグラウンドで自然終了するまで待機
      // 検証済み: Bun ShellPromiseには.child/.kill()が存在しない（Bun 1.3.6時点）
      // プロセス制御が必要な場合はBun.spawnへの移行が必要だが、
      // statuslineの用途では自然終了で許容可能
      prInfo = messages.noPr;
    } else {
      const prData: PRInfo[] = JSON.parse(result.text().trim() || "[]");
      prInfo = prData.length > 0 ? formatPrInfo(prData[0], messages) : messages.noPr;
    }
  } catch {
    // エラー時はPRなしとして扱う
    prInfo = messages.noPr;
  }

  // 表示文字列を構築
  let display: string;
  if (worktreeName) {
    display = worktreeName;
  } else if (issueNum) {
    display = `issue-${issueNum}`;
  } else {
    display = branch;
  }

  if (prInfo) {
    display = `${display} | ${prInfo}`;
  }

  return display;
}

// ========== フロー状態取得 ==========
async function getFlowState(
  projectDir: string | null,
  sessionId: string | null,
  currentDir: string,
  lang: Language,
): Promise<string> {
  let resolvedProjectDir = projectDir;

  if (!resolvedProjectDir) {
    // Try to find project dir from current directory
    try {
      const result = await $`git -C ${currentDir} rev-parse --show-toplevel`.quiet();
      resolvedProjectDir = result.text().trim();
    } catch {
      return "";
    }
  }

  if (!resolvedProjectDir) return "";

  // セッション固有のstate fileを探す
  let stateFile: string;
  if (sessionId) {
    stateFile = join(resolvedProjectDir, ".claude/logs/flow", `state-${sessionId}.json`);
  } else {
    // Fallback to legacy state.json
    stateFile = join(resolvedProjectDir, ".claude/logs/flow/state.json");
  }

  if (!existsSync(stateFile)) return "";

  try {
    const content = readFileSync(stateFile, "utf-8");
    const state: FlowState = JSON.parse(content);

    const activeWorkflow = state.active_workflow;
    if (!activeWorkflow) return "";

    const workflow = state.workflows?.[activeWorkflow];
    if (!workflow) return "";

    const currentPhase = workflow.current_phase;
    if (!currentPhase) return "";

    const iterations = workflow.phases?.[currentPhase]?.iterations ?? 1;
    const hooksFired = state.global?.hooks_fired_total ?? 0;

    const phaseNames = getPhaseNames(lang);
    const phaseName = getPhaseName(currentPhase, phaseNames);

    // iteration 1は表示しない（リトライ時のみ回数表示）
    if (iterations > 1) {
      return `⏳${phaseName} (${iterations}) | 🪝${hooksFired}`;
    }
    return `⏳${phaseName} | 🪝${hooksFired}`;
  } catch {
    return "";
  }
}

// ========== ターミナルタイトル設定 ==========
function setTerminalTitle(title: string): void {
  // OSC escape sequence for terminal title
  process.stderr.write(`\x1b]0;${title}\x07`);
}

// ========== メイン処理 ==========
async function main(): Promise<void> {
  // JSON入力を読み取り
  const text = await Bun.stdin.text();
  let input: StatusLineInput = {};
  if (text.trim()) {
    try {
      input = JSON.parse(text);
    } catch {
      // パースエラーは無視
    }
  }

  // モデル名を取得
  const model = sanitize(input.model?.display_name ?? "Claude");

  // 現在のディレクトリを取得
  const currentDir = input.workspace?.current_dir ?? process.cwd();

  // session_idを取得
  const sessionId = input.session_id ?? null;

  // 言語設定
  const lang = getLanguage();
  const messages = getMessages(lang);

  // Git/worktree情報を取得
  const worktreeInfo = await getWorktreeInfo(currentDir, messages);
  const sanitizedWorktreeInfo = worktreeInfo ? sanitize(worktreeInfo) : null;

  // フロー状態を取得
  const projectDir = process.env.CLAUDE_PROJECT_DIR ?? null;
  const flowState = sanitize(await getFlowState(projectDir, sessionId, currentDir, lang));

  // session_idをサニタイズ
  const sanitizedSessionId = sessionId ? sanitize(sessionId) : "?";

  // 表示名を決定
  let displayName: string;
  if (sanitizedWorktreeInfo) {
    displayName = sanitizedWorktreeInfo;
  } else {
    displayName = sanitize(basename(currentDir));
  }

  // ターミナルタイトルを設定（displayNameからworktree/ブランチ名のみを抽出、PR情報は除外）
  setTerminalTitle(`Claude: ${displayName.split(" | ")[0]}`);

  // ステータスラインを構築
  let statusLine = `[${model}] ${displayName}`;

  if (flowState) {
    statusLine = `${statusLine} | ${flowState}`;
  }

  // session_idを追加して出力
  console.log(`${statusLine} | ${sanitizedSessionId}`);
}

main().catch((err) => {
  console.error("statusline error:", err);
  process.exit(1);
});
