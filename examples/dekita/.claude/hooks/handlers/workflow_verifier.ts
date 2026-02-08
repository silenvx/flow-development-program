#!/usr/bin/env bun
/**
 * ワークフロー実行を検証するユーティリティモジュール。
 *
 * Why:
 *   期待されるフック動作と実際の実行結果を比較することで、
 *   ワークフローの進行状況や問題を可視化できる。
 *
 * What:
 *   - flow_definitions.tsの期待動作とhook-execution.logを比較
 *   - フェーズごとの進捗状況を算出
 *   - 予期せぬブロック/承認を検出
 *   - レポート生成（テキスト/辞書形式）
 *
 * State:
 *   - reads: .claude/logs/execution/hook-execution-*.jsonl
 *
 * Remarks:
 *   - フックではなくユーティリティモジュール
 *   - WorkflowVerifierクラスとverifySession関数を提供
 *   - CLIからも実行可能（--session-id, --since, --verbose）
 *
 * Changelog:
 *   - silenvx/dekita#xxx: モジュール追加
 *   - silenvx/dekita#2461: ppidフォールバック警告追加
 *   - silenvx/dekita#2496: handle_session_id_arg()使用
 *   - silenvx/dekita#2529: ppidフォールバック廃止
 *   - silenvx/dekita#3162: TypeScriptに移植
 */

import { existsSync, readFileSync } from "node:fs";
import { basename, join } from "node:path";
import { EXECUTION_LOG_DIR } from "../lib/common";
import { formatError } from "../lib/format_error";
import { isValidSessionId } from "../lib/session";

interface HookExecution {
  timestamp: string;
  session_id: string;
  hook: string;
  decision?: string;
  branch?: string;
  reason?: string;
  details?: Record<string, unknown>;
}

interface VerificationResult {
  hookName: string;
  status: string; // "ok", "missing", "unexpected_block", "unexpected_approve", "not_fired", "unknown"
  expectedDecision: string;
  actualDecision: string | null;
  executionCount: number;
  message: string;
}

interface PhaseVerification {
  phaseId: string;
  phaseName: string;
  status: string; // "complete", "partial", "not_started", "no_hooks", "unknown"
  hooksVerified: VerificationResult[];
  hooksFired: number;
  hooksExpected: number;
}

// Simplified phase definitions (subset of the Python version)
interface Phase {
  id: string;
  name: string;
  expectedHooks: string[];
}

const PHASES: Phase[] = [
  { id: "session_start", name: "セッション開始", expectedHooks: [] },
  {
    id: "issue_analysis",
    name: "Issue分析",
    expectedHooks: ["active-worktree-check", "branch-protection-check"],
  },
  {
    id: "worktree_setup",
    name: "Worktree作成",
    expectedHooks: ["worktree-creation-check", "worktree-removal-check"],
  },
  { id: "implementation", name: "実装", expectedHooks: ["edit-approval-check", "rework-tracker"] },
  { id: "build_test", name: "ビルド・テスト", expectedHooks: ["build-check", "test-check"] },
  { id: "pr_creation", name: "PR作成", expectedHooks: ["pr-creation-check"] },
  {
    id: "ci_monitoring",
    name: "CI監視",
    expectedHooks: ["ci-wait-check", "ci-failure-notification"],
  },
  { id: "code_review", name: "レビュー対応", expectedHooks: [] },
  { id: "merge", name: "マージ", expectedHooks: ["merge-check", "main-sync-check"] },
  { id: "cleanup", name: "クリーンアップ", expectedHooks: ["worktree-removal-check"] },
  { id: "session_end", name: "セッション終了", expectedHooks: ["related-task-check"] },
];

// Expected hook behaviors (subset)
const EXPECTED_HOOK_BEHAVIORS: Record<string, { triggerType: string; expectedDecision: string }> = {
  "active-worktree-check": { triggerType: "PreToolUse", expectedDecision: "approve" },
  "branch-protection-check": { triggerType: "PreToolUse", expectedDecision: "approve" },
  "edit-approval-check": { triggerType: "PreToolUse", expectedDecision: "approve" },
  "worktree-creation-check": { triggerType: "PreToolUse", expectedDecision: "approve" },
  "worktree-removal-check": { triggerType: "PreToolUse", expectedDecision: "approve" },
  "pr-creation-check": { triggerType: "PreToolUse", expectedDecision: "approve" },
  "ci-wait-check": { triggerType: "PreToolUse", expectedDecision: "approve" },
  "merge-check": { triggerType: "PreToolUse", expectedDecision: "approve" },
  "main-sync-check": { triggerType: "PostToolUse", expectedDecision: "approve" },
  "rework-tracker": { triggerType: "PostToolUse", expectedDecision: "approve" },
  "related-task-check": { triggerType: "Stop", expectedDecision: "approve" },
};

/**
 * Read session log entries from the session-specific log file.
 */
function readSessionLogEntries(sessionId: string): HookExecution[] {
  // EXECUTION_LOG_DIR is already an absolute path from lib/common
  const safeSessionId = basename(sessionId);
  const logFile = join(EXECUTION_LOG_DIR, `hook-execution-${safeSessionId}.jsonl`);

  if (!existsSync(logFile)) {
    return [];
  }

  const entries: HookExecution[] = [];

  try {
    const content = readFileSync(logFile, "utf-8");
    for (const line of content.split("\n")) {
      if (!line.trim()) {
        continue;
      }
      try {
        const entry = JSON.parse(line);
        entries.push({
          timestamp: entry.timestamp ?? "",
          session_id: entry.session_id ?? "",
          hook: entry.hook ?? "",
          decision: entry.decision ?? "",
          branch: entry.branch,
          reason: entry.reason,
          details: entry.details,
        });
      } catch {
        // Skip invalid lines
      }
    }
  } catch {
    // File read error
  }

  return entries;
}

/**
 * Workflow Verifier class.
 */
class WorkflowVerifier {
  sessionId: string | null;
  sinceHours: number | null;
  executions: HookExecution[];

  constructor(sessionId: string | null = null, sinceHours: number | null = null) {
    if (sessionId && !isValidSessionId(sessionId)) {
      throw new Error(`Invalid session ID format: ${sessionId}`);
    }

    if (sinceHours !== null && sinceHours < 0) {
      throw new Error(`sinceHours must be non-negative, got ${sinceHours}`);
    }

    this.sessionId = sessionId;
    this.sinceHours = sinceHours;
    this.executions = [];

    this.loadExecutions();
  }

  private loadExecutions(): void {
    if (!this.sessionId) {
      return;
    }

    const entries = readSessionLogEntries(this.sessionId);

    // Calculate cutoff time if sinceHours is set
    let cutoffTime: Date | null = null;
    if (this.sinceHours !== null) {
      cutoffTime = new Date(Date.now() - this.sinceHours * 60 * 60 * 1000);
    }

    for (const entry of entries) {
      if (cutoffTime !== null) {
        if (!entry.timestamp) {
          continue;
        }
        try {
          const entryTime = new Date(entry.timestamp);
          if (entryTime < cutoffTime) {
            continue;
          }
        } catch {
          continue;
        }
      }

      this.executions.push(entry);
    }
  }

  getExecutionCount(hookName: string): number {
    return this.executions.filter((e) => e.hook === hookName).length;
  }

  getExecutionsForHook(hookName: string): HookExecution[] {
    return this.executions.filter((e) => e.hook === hookName);
  }

  getDecisionSummary(hookName: string): { approve: number; block: number } {
    const summary = { approve: 0, block: 0 };
    for (const e of this.getExecutionsForHook(hookName)) {
      if (e.decision === "approve") {
        summary.approve++;
      } else if (e.decision === "block") {
        summary.block++;
      }
    }
    return summary;
  }

  verifyHook(hookName: string): VerificationResult {
    const expected = EXPECTED_HOOK_BEHAVIORS[hookName];
    if (!expected) {
      return {
        hookName,
        status: "unknown",
        expectedDecision: "unknown",
        actualDecision: null,
        executionCount: 0,
        message: `Hook '${hookName}' is not defined in EXPECTED_HOOK_BEHAVIORS`,
      };
    }

    const executions = this.getExecutionsForHook(hookName);
    const count = executions.length;

    if (count === 0) {
      return {
        hookName,
        status: "not_fired",
        expectedDecision: expected.expectedDecision,
        actualDecision: null,
        executionCount: 0,
        message: `Hook '${hookName}' was not fired (expected on ${expected.triggerType})`,
      };
    }

    const decisions = this.getDecisionSummary(hookName);

    if (expected.expectedDecision === "approve" && decisions.block > 0) {
      return {
        hookName,
        status: "unexpected_block",
        expectedDecision: "approve",
        actualDecision: "block",
        executionCount: count,
        message: `Hook blocked ${decisions.block} time(s) but was expected to approve`,
      };
    }

    if (expected.expectedDecision === "block" && decisions.approve > 0) {
      return {
        hookName,
        status: "unexpected_approve",
        expectedDecision: "block",
        actualDecision: "approve",
        executionCount: count,
        message: `Hook approved ${decisions.approve} time(s) but was expected to block`,
      };
    }

    return {
      hookName,
      status: "ok",
      expectedDecision: expected.expectedDecision,
      actualDecision: decisions.approve >= decisions.block ? "approve" : "block",
      executionCount: count,
      message: `Hook fired ${count} time(s) (${decisions.approve} approve, ${decisions.block} block)`,
    };
  }

  verifyPhase(phaseId: string): PhaseVerification {
    const phase = PHASES.find((p) => p.id === phaseId);
    if (!phase) {
      return {
        phaseId,
        phaseName: "Unknown",
        status: "unknown",
        hooksVerified: [],
        hooksFired: 0,
        hooksExpected: 0,
      };
    }

    const results: VerificationResult[] = [];
    let hooksFired = 0;

    for (const hookName of phase.expectedHooks) {
      const result = this.verifyHook(hookName);
      results.push(result);
      if (result.executionCount > 0) {
        hooksFired++;
      }
    }

    const hooksExpected = phase.expectedHooks.length;
    let status: string;

    if (hooksExpected === 0) {
      status = "no_hooks";
    } else if (hooksFired === 0) {
      status = "not_started";
    } else if (hooksFired === hooksExpected) {
      status = "complete";
    } else {
      status = "partial";
    }

    return {
      phaseId: phase.id,
      phaseName: phase.name,
      status,
      hooksVerified: results,
      hooksFired,
      hooksExpected,
    };
  }

  verifyAllPhases(): PhaseVerification[] {
    return PHASES.map((phase) => this.verifyPhase(phase.id));
  }

  getCurrentPhase(): Phase | null {
    let current: Phase | null = null;

    for (const phase of PHASES) {
      for (const hookName of phase.expectedHooks) {
        if (this.getExecutionCount(hookName) > 0) {
          current = phase;
          break;
        }
      }
    }

    return current;
  }

  getFiredHooks(): string[] {
    return [...new Set(this.executions.map((e) => e.hook))];
  }

  getUnfiredHooks(): string[] {
    const fired = new Set(this.getFiredHooks());
    const allHooks = new Set(Object.keys(EXPECTED_HOOK_BEHAVIORS));
    return [...allHooks].filter((h) => !fired.has(h));
  }

  getUndefinedHooks(): string[] {
    const fired = new Set(this.getFiredHooks());
    const defined = new Set(Object.keys(EXPECTED_HOOK_BEHAVIORS));
    return [...fired].filter((h) => !defined.has(h));
  }

  generateReport(verbose = false): string {
    const lines: string[] = [];
    lines.push("## ワークフロー検証レポート");
    lines.push("");

    lines.push(`**セッション**: ${this.sessionId}`);
    lines.push(`**検証時刻**: ${new Date().toISOString()}`);
    if (this.sinceHours !== null) {
      lines.push(`**対象期間**: 直近 ${this.sinceHours} 時間`);
    }
    lines.push(`**実行ログエントリ数**: ${this.executions.length}`);
    lines.push("");

    const currentPhase = this.getCurrentPhase();
    if (currentPhase) {
      lines.push(`**推定現在フェーズ**: ${currentPhase.name} (${currentPhase.id})`);
    } else {
      lines.push("**推定現在フェーズ**: なし（フック未発動）");
    }
    lines.push("");

    lines.push("### フェーズ進捗");
    lines.push("");

    const phaseResults = this.verifyAllPhases();
    for (const pr of phaseResults) {
      const statusIcon: Record<string, string> = {
        complete: "✅",
        partial: "⏳",
        not_started: "⬜",
        no_hooks: "➖",
        unknown: "❓",
      };

      lines.push(
        `${statusIcon[pr.status] ?? "❓"} **${pr.phaseName}** (${pr.phaseId}): ` +
          `${pr.hooksFired}/${pr.hooksExpected} hooks`,
      );

      if (verbose) {
        for (const hookResult of pr.hooksVerified) {
          const hookIcon: Record<string, string> = {
            ok: "✅",
            not_fired: "⬜",
            unexpected_block: "⚠️",
            unexpected_approve: "⚠️",
            unknown: "❓",
          };
          lines.push(
            `  ${hookIcon[hookResult.status] ?? "❓"} ${hookResult.hookName}: ${hookResult.message}`,
          );
        }
      }
    }
    lines.push("");

    // Issues
    const issues: string[] = [];

    const undefined_hooks = this.getUndefinedHooks();
    if (undefined_hooks.length > 0) {
      issues.push(`**未定義フック発動**: ${undefined_hooks.join(", ")}`);
    }

    for (const pr of phaseResults) {
      for (const hr of pr.hooksVerified) {
        if (hr.status === "unexpected_block" || hr.status === "unexpected_approve") {
          issues.push(`**${hr.hookName}**: ${hr.message}`);
        }
      }
    }

    if (issues.length > 0) {
      lines.push("### 検出された問題");
      lines.push("");
      for (const issue of issues) {
        lines.push(`- ${issue}`);
      }
      lines.push("");
    } else {
      lines.push("### 検出された問題");
      lines.push("");
      lines.push("なし");
      lines.push("");
    }

    const firedHooks = this.getFiredHooks();
    const unfiredHooks = this.getUnfiredHooks();

    lines.push("### サマリー");
    lines.push("");
    lines.push(`- **発動済みフック**: ${firedHooks.length}`);
    lines.push(`- **未発動フック**: ${unfiredHooks.length}`);
    lines.push(`- **未定義フック**: ${undefined_hooks.length}`);
    lines.push("");

    return lines.join("\n");
  }

  getSummaryDict(): Record<string, unknown> {
    const phaseResults = this.verifyAllPhases();
    const currentPhase = this.getCurrentPhase();

    const phasesSummary = phaseResults.map((pr) => ({
      phase_id: pr.phaseId,
      phase_name: pr.phaseName,
      status: pr.status,
      hooks_fired: pr.hooksFired,
      hooks_expected: pr.hooksExpected,
    }));

    const issues: Array<{ hook: string; status: string; message: string }> = [];

    for (const pr of phaseResults) {
      for (const hr of pr.hooksVerified) {
        if (hr.status === "unexpected_block" || hr.status === "unexpected_approve") {
          issues.push({
            hook: hr.hookName,
            status: hr.status,
            message: hr.message,
          });
        }
      }
    }

    const undefinedHooks = this.getUndefinedHooks();
    if (undefinedHooks.length > 0) {
      issues.push({
        hook: "undefined",
        status: "undefined_hooks_fired",
        message: `Undefined hooks fired: ${undefinedHooks.join(", ")}`,
      });
    }

    return {
      session_id: this.sessionId,
      timestamp: new Date().toISOString(),
      execution_count: this.executions.length,
      current_phase: currentPhase?.id ?? null,
      phases: phasesSummary,
      fired_hooks: this.getFiredHooks().length,
      unfired_hooks: this.getUnfiredHooks().length,
      undefined_hooks: undefinedHooks.length,
      issues,
      has_issues: issues.length > 0,
    };
  }
}

/**
 * Convenience function to verify a session.
 */
function verifySession(
  sessionId: string,
  verbose = false,
  sinceHours: number | null = null,
): string {
  const verifier = new WorkflowVerifier(sessionId, sinceHours);
  return verifier.generateReport(verbose);
}

// CLI entry point
if (import.meta.main) {
  const args = process.argv.slice(2);
  let verbose = false;
  let sinceHours: number | null = null;
  let sessionId: string | null = null;

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === "-v" || arg === "--verbose") {
      verbose = true;
    } else if (arg === "--since" && args[i + 1]) {
      sinceHours = Number.parseFloat(args[++i]);
    } else if (arg === "--session-id" && args[i + 1]) {
      sessionId = args[++i];
    }
  }

  if (!sessionId) {
    console.error("Error: --session-id is required");
    console.error(
      "Usage: bun run workflow_verifier.ts --session-id <SESSION_ID> [--verbose] [--since <HOURS>]",
    );
    process.exit(1);
  }

  // Validate --since value
  if (sinceHours !== null && (Number.isNaN(sinceHours) || sinceHours < 0)) {
    console.error("Error: --since must be a non-negative number");
    process.exit(1);
  }

  try {
    const report = verifySession(sessionId, verbose, sinceHours);
    console.log(report);
  } catch (error) {
    console.error(`Error: ${formatError(error)}`);
    process.exit(1);
  }
}

export { WorkflowVerifier, verifySession };
