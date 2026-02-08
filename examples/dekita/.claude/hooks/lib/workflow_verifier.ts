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
 *   - WorkflowVerifierクラスとverifyCurrentSession関数を提供
 *
 * Changelog:
 *   - silenvx/dekita#3051: モジュール追加
 *   - silenvx/dekita#3157: TypeScriptに移行
 */

import { EXECUTION_LOG_DIR } from "./common";
import { EXPECTED_HOOK_BEHAVIORS, type Phase, getAllPhases, getPhase } from "./flow_definitions";
import { readSessionLogEntries } from "./logging";
import { isValidSessionId } from "./session";

// =============================================================================
// Types
// =============================================================================

export interface HookExecution {
  timestamp: string;
  session_id: string;
  hook: string;
  decision: string;
  branch?: string;
  reason?: string;
  details: Record<string, unknown>;
}

export interface VerificationResult {
  hook_name: string;
  status: string; // "ok", "missing", "unexpected_block", "unexpected_approve"
  expected_decision: string;
  actual_decision: string | null;
  execution_count: number;
  message: string;
}

export interface PhaseVerification {
  phase_id: string;
  phase_name: string;
  status: string; // "complete", "partial", "not_started"
  hooks_verified: VerificationResult[];
  hooks_fired: number;
  hooks_expected: number;
}

// =============================================================================
// Helper Functions
// =============================================================================

function parseHookExecutionEntry(entry: Record<string, unknown>): HookExecution {
  return {
    timestamp: (entry.timestamp as string) ?? "",
    session_id: (entry.session_id as string) ?? "",
    hook: (entry.hook as string) ?? "",
    decision: (entry.decision as string) ?? "",
    branch: entry.branch as string | undefined,
    reason: entry.reason as string | undefined,
    details: (entry.details as Record<string, unknown>) ?? {},
  };
}

// =============================================================================
// WorkflowVerifier Class
// =============================================================================

export class WorkflowVerifier {
  sessionId: string | null;
  sinceHours: number | null;
  executions: HookExecution[];

  /**
   * Private constructor. Use `WorkflowVerifier.create()` to instantiate.
   */
  private constructor(sessionId: string | null, sinceHours: number | null) {
    this.sessionId = sessionId;
    this.sinceHours = sinceHours;
    this.executions = [];
  }

  /**
   * Factory method to create a WorkflowVerifier instance.
   * Async because it needs to load execution logs from files.
   */
  static async create(
    sessionId: string | null = null,
    sinceHours: number | null = null,
  ): Promise<WorkflowVerifier> {
    // Validate session ID
    if (sessionId && !isValidSessionId(sessionId)) {
      throw new Error(`Invalid session ID format: ${sessionId}`);
    }

    // Validate sinceHours
    if (sinceHours !== null && sinceHours < 0) {
      throw new Error(`sinceHours must be non-negative, got ${sinceHours}`);
    }

    const verifier = new WorkflowVerifier(sessionId, sinceHours);
    await verifier._loadExecutions();
    return verifier;
  }

  private async _loadExecutions(): Promise<void> {
    if (!this.sessionId) {
      return;
    }

    // Read entries from session-specific log file
    const entries = await readSessionLogEntries(
      EXECUTION_LOG_DIR,
      "hook-execution",
      this.sessionId,
    );

    // Calculate cutoff time if sinceHours is set
    let cutoffTime: Date | null = null;
    if (this.sinceHours !== null) {
      cutoffTime = new Date(Date.now() - this.sinceHours * 60 * 60 * 1000);
    }

    for (const entry of entries) {
      // Filter by timestamp if cutoff is set
      if (cutoffTime !== null) {
        const timestampStr = (entry.timestamp as string) ?? "";
        if (!timestampStr) {
          continue;
        }
        try {
          const entryTime = new Date(timestampStr);
          if (entryTime < cutoffTime) {
            continue;
          }
        } catch {
          continue;
        }
      }

      this.executions.push(parseHookExecutionEntry(entry));
    }
  }

  getExecutionCount(hookName: string): number {
    return this.executions.filter((e) => e.hook === hookName).length;
  }

  getExecutionsForHook(hookName: string): HookExecution[] {
    return this.executions.filter((e) => e.hook === hookName);
  }

  getDecisionSummary(hookName: string): Record<string, number> {
    const summary: Record<string, number> = { approve: 0, block: 0 };
    for (const e of this.getExecutionsForHook(hookName)) {
      if (e.decision in summary) {
        summary[e.decision]++;
      }
    }
    return summary;
  }

  verifyHook(hookName: string): VerificationResult {
    const expected = EXPECTED_HOOK_BEHAVIORS[hookName];
    if (!expected) {
      return {
        hook_name: hookName,
        status: "unknown",
        expected_decision: "unknown",
        actual_decision: null,
        execution_count: 0,
        message: `Hook '${hookName}' is not defined in EXPECTED_HOOK_BEHAVIORS`,
      };
    }

    const executions = this.getExecutionsForHook(hookName);
    const count = executions.length;

    if (count === 0) {
      return {
        hook_name: hookName,
        status: "not_fired",
        expected_decision: expected.expectedDecision,
        actual_decision: null,
        execution_count: 0,
        message: `Hook '${hookName}' was not fired (expected on ${expected.triggerType})`,
      };
    }

    const decisions = this.getDecisionSummary(hookName);
    const blocks = decisions.block;
    const approves = decisions.approve;

    if (expected.expectedDecision === "approve") {
      if (blocks > 0) {
        return {
          hook_name: hookName,
          status: "unexpected_block",
          expected_decision: "approve",
          actual_decision: "block",
          execution_count: count,
          message: `Hook blocked ${blocks} time(s) but was expected to approve`,
        };
      }
    } else if (expected.expectedDecision === "block") {
      if (approves > 0) {
        return {
          hook_name: hookName,
          status: "unexpected_approve",
          expected_decision: "block",
          actual_decision: "approve",
          execution_count: count,
          message: `Hook approved ${approves} time(s) but was expected to block`,
        };
      }
    }

    return {
      hook_name: hookName,
      status: "ok",
      expected_decision: expected.expectedDecision,
      actual_decision: approves >= blocks ? "approve" : "block",
      execution_count: count,
      message: `Hook fired ${count} time(s) (${approves} approve, ${blocks} block)`,
    };
  }

  verifyPhase(phaseId: string): PhaseVerification {
    const phase = getPhase(phaseId);
    if (!phase) {
      return {
        phase_id: phaseId,
        phase_name: "Unknown",
        status: "unknown",
        hooks_verified: [],
        hooks_fired: 0,
        hooks_expected: 0,
      };
    }

    const results: VerificationResult[] = [];
    let hooksFired = 0;

    for (const hookName of phase.expectedHooks) {
      const result = this.verifyHook(hookName);
      results.push(result);
      if (result.execution_count > 0) {
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
      phase_id: phase.id,
      phase_name: phase.name,
      status,
      hooks_verified: results,
      hooks_fired: hooksFired,
      hooks_expected: hooksExpected,
    };
  }

  verifyAllPhases(): PhaseVerification[] {
    return getAllPhases().map((phase) => this.verifyPhase(phase.id));
  }

  getCurrentPhase(): Phase | null {
    const phases = getAllPhases();
    let current: Phase | null = null;

    for (const phase of phases) {
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

    // Session info
    lines.push(`**セッション**: ${this.sessionId}`);
    lines.push(`**検証時刻**: ${new Date().toISOString()}`);
    if (this.sinceHours !== null) {
      lines.push(`**対象期間**: 直近 ${this.sinceHours} 時間`);
    }
    lines.push(`**実行ログエントリ数**: ${this.executions.length}`);
    lines.push("");

    // Current phase estimation
    const currentPhase = this.getCurrentPhase();
    if (currentPhase) {
      lines.push(`**推定現在フェーズ**: ${currentPhase.name} (${currentPhase.id})`);
    } else {
      lines.push("**推定現在フェーズ**: なし（フック未発動）");
    }
    lines.push("");

    // Phase progress
    lines.push("### フェーズ進捗");
    lines.push("");

    const phaseResults = this.verifyAllPhases();
    const statusIcons: Record<string, string> = {
      complete: "✅",
      partial: "⏳",
      not_started: "⬜",
      no_hooks: "➖",
      unknown: "❓",
    };

    for (const pr of phaseResults) {
      const icon = statusIcons[pr.status] ?? "❓";
      lines.push(
        `${icon} **${pr.phase_name}** (${pr.phase_id}): ${pr.hooks_fired}/${pr.hooks_expected} hooks`,
      );

      if (verbose) {
        const hookIcons: Record<string, string> = {
          ok: "✅",
          not_fired: "⬜",
          unexpected_block: "⚠️",
          unexpected_approve: "⚠️",
          unknown: "❓",
        };
        for (const hookResult of pr.hooks_verified) {
          const hookIcon = hookIcons[hookResult.status] ?? "❓";
          lines.push(`  ${hookIcon} ${hookResult.hook_name}: ${hookResult.message}`);
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
      for (const hr of pr.hooks_verified) {
        if (hr.status === "unexpected_block" || hr.status === "unexpected_approve") {
          issues.push(`**${hr.hook_name}**: ${hr.message}`);
        }
      }
    }

    lines.push("### 検出された問題");
    lines.push("");
    if (issues.length > 0) {
      for (const issue of issues) {
        lines.push(`- ${issue}`);
      }
    } else {
      lines.push("なし");
    }
    lines.push("");

    // Summary stats
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
      phase_id: pr.phase_id,
      phase_name: pr.phase_name,
      status: pr.status,
      hooks_fired: pr.hooks_fired,
      hooks_expected: pr.hooks_expected,
    }));

    const issues: Array<{ hook: string; status: string; message: string }> = [];
    for (const pr of phaseResults) {
      for (const hr of pr.hooks_verified) {
        if (hr.status === "unexpected_block" || hr.status === "unexpected_approve") {
          issues.push({
            hook: hr.hook_name,
            status: hr.status,
            message: hr.message,
          });
        }
      }
    }

    const undefined_hooks = this.getUndefinedHooks();
    if (undefined_hooks.length > 0) {
      issues.push({
        hook: "undefined",
        status: "undefined_hooks_fired",
        message: `Undefined hooks fired: ${undefined_hooks.join(", ")}`,
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
      undefined_hooks: undefined_hooks.length,
      issues,
      has_issues: issues.length > 0,
    };
  }
}

// =============================================================================
// Convenience Function
// =============================================================================

export async function verifyCurrentSession(
  verbose = false,
  sinceHours: number | null = null,
  sessionId: string | null = null,
): Promise<string> {
  const verifier = await WorkflowVerifier.create(sessionId, sinceHours);
  return verifier.generateReport(verbose);
}
