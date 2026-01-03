#!/usr/bin/env python3
"""ワークフロー実行を検証するユーティリティモジュール。

Why:
    期待されるフック動作と実際の実行結果を比較することで、
    ワークフローの進行状況や問題を可視化できる。

What:
    - flow_definitions.pyの期待動作とhook-execution.logを比較
    - フェーズごとの進捗状況を算出
    - 予期せぬブロック/承認を検出
    - レポート生成（テキスト/辞書形式）

State:
    - reads: .claude/logs/execution/hook-execution-*.jsonl

Remarks:
    - フックではなくユーティリティモジュール
    - WorkflowVerifierクラスとverify_current_session関数を提供
    - CLIからも実行可能（--session-id, --since, --verbose）

Changelog:
    - silenvx/dekita#xxx: モジュール追加
    - silenvx/dekita#2461: ppidフォールバック警告追加
    - silenvx/dekita#2496: handle_session_id_arg()使用
    - silenvx/dekita#2529: ppidフォールバック廃止
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from common import EXECUTION_LOG_DIR
from flow_definitions import (
    EXPECTED_HOOK_BEHAVIORS,
    Phase,
    get_all_phases,
    get_phase,
)
from lib.logging import read_session_log_entries
from lib.session import handle_session_id_arg

logger = logging.getLogger(__name__)


@dataclass
class HookExecution:
    """A single hook execution record from the log."""

    timestamp: str
    session_id: str
    hook: str
    decision: str
    branch: str | None = None
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_log_entry(cls, entry: dict[str, Any]) -> HookExecution:
        """Create HookExecution from a log entry dict."""
        return cls(
            timestamp=entry.get("timestamp", ""),
            session_id=entry.get("session_id", ""),
            hook=entry.get("hook", ""),
            decision=entry.get("decision", ""),
            branch=entry.get("branch"),
            reason=entry.get("reason"),
            details=entry.get("details", {}),
        )


@dataclass
class VerificationResult:
    """Result of verifying a single hook behavior."""

    hook_name: str
    status: str  # "ok", "missing", "unexpected_block", "unexpected_approve"
    expected_decision: str
    actual_decision: str | None
    execution_count: int
    message: str


@dataclass
class PhaseVerification:
    """Verification result for a phase."""

    phase_id: str
    phase_name: str
    status: str  # "complete", "partial", "not_started"
    hooks_verified: list[VerificationResult]
    hooks_fired: int
    hooks_expected: int


class WorkflowVerifier:
    """Verifies workflow execution against expected behaviors.

    Reads hook execution logs and compares them against the expected
    hook behaviors defined in flow_definitions.py.

    Attributes:
        session_id: The Claude session ID to verify (defaults to current)
        since_hours: Only include logs from the last N hours (None = all)
        executions: List of HookExecution records from the log
    """

    def __init__(self, session_id: str | None = None, since_hours: float | None = None):
        """Initialize the verifier.

        Args:
            session_id: Optional session ID. If None, uses PPID-based fallback.
            since_hours: Only include logs from the last N hours.
                        If None, includes all logs for the session.

        Security: Validates session_id to prevent path traversal attacks.
        """
        from lib.session import is_valid_session_id

        # Issue #2529: ppidフォールバック完全廃止
        if session_id and not is_valid_session_id(session_id):
            raise ValueError(f"Invalid session ID format: {session_id}")
        self.session_id = session_id
        # Validate since_hours: must be non-negative if provided
        if since_hours is not None and since_hours < 0:
            raise ValueError(f"since_hours must be non-negative, got {since_hours}")
        self.since_hours = since_hours
        self.executions: list[HookExecution] = []
        self._load_executions()

    def _load_executions(self) -> None:
        """Load hook executions from the session-specific log file."""
        if not self.session_id:
            return

        # Read entries from session-specific log file
        entries = read_session_log_entries(EXECUTION_LOG_DIR, "hook-execution", self.session_id)

        # Calculate cutoff time if since_hours is set
        cutoff_time: datetime | None = None
        if self.since_hours is not None:
            cutoff_time = datetime.now(UTC) - timedelta(hours=self.since_hours)

        for entry in entries:
            # Filter by timestamp if cutoff is set
            if cutoff_time is not None:
                timestamp_str = entry.get("timestamp", "")
                if not timestamp_str:
                    # Skip entries without timestamp when filtering is active
                    continue
                try:
                    # Parse ISO format timestamp
                    entry_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                    if entry_time < cutoff_time:
                        continue
                except ValueError:
                    # Invalid timestamp format, skip entry when filtering
                    continue

            self.executions.append(HookExecution.from_log_entry(entry))

    def get_execution_count(self, hook_name: str) -> int:
        """Get the number of times a hook was executed.

        Args:
            hook_name: The hook name to count.

        Returns:
            Number of executions in this session.
        """
        return sum(1 for e in self.executions if e.hook == hook_name)

    def get_executions_for_hook(self, hook_name: str) -> list[HookExecution]:
        """Get all executions for a specific hook.

        Args:
            hook_name: The hook name.

        Returns:
            List of HookExecution records.
        """
        return [e for e in self.executions if e.hook == hook_name]

    def get_decision_summary(self, hook_name: str) -> dict[str, int]:
        """Get summary of decisions for a hook.

        Args:
            hook_name: The hook name.

        Returns:
            Dict with {"approve": N, "block": N}.
        """
        summary = {"approve": 0, "block": 0}
        for e in self.get_executions_for_hook(hook_name):
            if e.decision in summary:
                summary[e.decision] += 1
        return summary

    def verify_hook(self, hook_name: str) -> VerificationResult:
        """Verify a single hook against its expected behavior.

        Args:
            hook_name: The hook name to verify.

        Returns:
            VerificationResult with status and details.
        """
        expected = EXPECTED_HOOK_BEHAVIORS.get(hook_name)
        if not expected:
            return VerificationResult(
                hook_name=hook_name,
                status="unknown",
                expected_decision="unknown",
                actual_decision=None,
                execution_count=0,
                message=f"Hook '{hook_name}' is not defined in EXPECTED_HOOK_BEHAVIORS",
            )

        executions = self.get_executions_for_hook(hook_name)
        count = len(executions)

        if count == 0:
            # Hook not fired - might be expected if the tool wasn't used
            return VerificationResult(
                hook_name=hook_name,
                status="not_fired",
                expected_decision=expected.expected_decision,
                actual_decision=None,
                execution_count=0,
                message=f"Hook '{hook_name}' was not fired (expected on {expected.trigger_type})",
            )

        # Analyze decisions
        decisions = self.get_decision_summary(hook_name)
        blocks = decisions["block"]
        approves = decisions["approve"]

        # Determine status based on expected behavior
        if expected.expected_decision == "approve":
            if blocks > 0:
                return VerificationResult(
                    hook_name=hook_name,
                    status="unexpected_block",
                    expected_decision="approve",
                    actual_decision="block",
                    execution_count=count,
                    message=f"Hook blocked {blocks} time(s) but was expected to approve",
                )
        elif expected.expected_decision == "block":
            if approves > 0:
                return VerificationResult(
                    hook_name=hook_name,
                    status="unexpected_approve",
                    expected_decision="block",
                    actual_decision="approve",
                    execution_count=count,
                    message=f"Hook approved {approves} time(s) but was expected to block",
                )

        # Either decision is ok, or matched expectations
        return VerificationResult(
            hook_name=hook_name,
            status="ok",
            expected_decision=expected.expected_decision,
            actual_decision="approve" if approves >= blocks else "block",
            execution_count=count,
            message=f"Hook fired {count} time(s) ({approves} approve, {blocks} block)",
        )

    def verify_phase(self, phase_id: str) -> PhaseVerification:
        """Verify all hooks in a phase.

        Args:
            phase_id: The phase ID to verify.

        Returns:
            PhaseVerification with results for all hooks in the phase.
        """
        phase = get_phase(phase_id)
        if not phase:
            return PhaseVerification(
                phase_id=phase_id,
                phase_name="Unknown",
                status="unknown",
                hooks_verified=[],
                hooks_fired=0,
                hooks_expected=0,
            )

        results: list[VerificationResult] = []
        hooks_fired = 0

        for hook_name in phase.expected_hooks:
            result = self.verify_hook(hook_name)
            results.append(result)
            if result.execution_count > 0:
                hooks_fired += 1

        # Determine phase status
        hooks_expected = len(phase.expected_hooks)
        if hooks_expected == 0:
            status = "no_hooks"
        elif hooks_fired == 0:
            status = "not_started"
        elif hooks_fired == hooks_expected:
            status = "complete"
        else:
            status = "partial"

        return PhaseVerification(
            phase_id=phase.id,
            phase_name=phase.name,
            status=status,
            hooks_verified=results,
            hooks_fired=hooks_fired,
            hooks_expected=hooks_expected,
        )

    def verify_all_phases(self) -> list[PhaseVerification]:
        """Verify all development phases.

        Returns:
            List of PhaseVerification for all 13 phases.
        """
        return [self.verify_phase(phase.id) for phase in get_all_phases()]

    def get_current_phase(self) -> Phase | None:
        """Estimate the current development phase based on hook executions.

        Uses a heuristic: the current phase is the most advanced phase
        that has at least one hook fired.

        Returns:
            The estimated current Phase, or None if no hooks have fired.
        """
        phases = get_all_phases()
        current = None

        for phase in phases:
            for hook_name in phase.expected_hooks:
                if self.get_execution_count(hook_name) > 0:
                    current = phase
                    break

        return current

    def get_fired_hooks(self) -> list[str]:
        """Get list of unique hooks that have fired in this session.

        Returns:
            List of hook names that have at least one execution.
        """
        return list({e.hook for e in self.executions})

    def get_unfired_hooks(self) -> list[str]:
        """Get list of defined hooks that haven't fired.

        Returns:
            List of hook names that are defined but have no executions.
        """
        fired = set(self.get_fired_hooks())
        all_hooks = set(EXPECTED_HOOK_BEHAVIORS.keys())
        return list(all_hooks - fired)

    def get_undefined_hooks(self) -> list[str]:
        """Get list of hooks that fired but aren't defined.

        Returns:
            List of hook names that executed but aren't in EXPECTED_HOOK_BEHAVIORS.
        """
        fired = set(self.get_fired_hooks())
        defined = set(EXPECTED_HOOK_BEHAVIORS.keys())
        return list(fired - defined)

    def generate_report(self, verbose: bool = False) -> str:
        """Generate a verification report.

        Args:
            verbose: If True, include details for all hooks.
                    If False, only include summary and issues.

        Returns:
            Formatted report string.
        """
        lines: list[str] = []
        lines.append("## ワークフロー検証レポート")
        lines.append("")

        # Session info
        lines.append(f"**セッション**: {self.session_id}")
        lines.append(f"**検証時刻**: {datetime.now(UTC).isoformat()}")
        if self.since_hours is not None:
            lines.append(f"**対象期間**: 直近 {self.since_hours} 時間")
        lines.append(f"**実行ログエントリ数**: {len(self.executions)}")
        lines.append("")

        # Current phase estimation
        current_phase = self.get_current_phase()
        if current_phase:
            lines.append(f"**推定現在フェーズ**: {current_phase.name} ({current_phase.id})")
        else:
            lines.append("**推定現在フェーズ**: なし（フック未発動）")
        lines.append("")

        # Phase progress
        lines.append("### フェーズ進捗")
        lines.append("")

        phase_results = self.verify_all_phases()
        for pr in phase_results:
            status_icon = {
                "complete": "✅",
                "partial": "⏳",
                "not_started": "⬜",
                "no_hooks": "➖",
                "unknown": "❓",
            }.get(pr.status, "❓")

            lines.append(
                f"{status_icon} **{pr.phase_name}** ({pr.phase_id}): "
                f"{pr.hooks_fired}/{pr.hooks_expected} hooks"
            )

            if verbose:
                for hook_result in pr.hooks_verified:
                    hook_icon = {
                        "ok": "✅",
                        "not_fired": "⬜",
                        "unexpected_block": "⚠️",
                        "unexpected_approve": "⚠️",
                        "unknown": "❓",
                    }.get(hook_result.status, "❓")
                    lines.append(f"  {hook_icon} {hook_result.hook_name}: {hook_result.message}")

        lines.append("")

        # Issues
        issues: list[str] = []

        # Check for undefined hooks that fired
        undefined = self.get_undefined_hooks()
        if undefined:
            issues.append(f"**未定義フック発動**: {', '.join(undefined)}")

        # Check for unexpected blocks/approves
        for pr in phase_results:
            for hr in pr.hooks_verified:
                if hr.status in ("unexpected_block", "unexpected_approve"):
                    issues.append(f"**{hr.hook_name}**: {hr.message}")

        if issues:
            lines.append("### 検出された問題")
            lines.append("")
            for issue in issues:
                lines.append(f"- {issue}")
            lines.append("")
        else:
            lines.append("### 検出された問題")
            lines.append("")
            lines.append("なし")
            lines.append("")

        # Summary stats
        fired_hooks = self.get_fired_hooks()
        unfired_hooks = self.get_unfired_hooks()

        lines.append("### サマリー")
        lines.append("")
        lines.append(f"- **発動済みフック**: {len(fired_hooks)}")
        lines.append(f"- **未発動フック**: {len(unfired_hooks)}")
        lines.append(f"- **未定義フック**: {len(undefined)}")
        lines.append("")

        return "\n".join(lines)

    def get_summary_dict(self) -> dict[str, Any]:
        """Get verification summary as a dictionary.

        Useful for programmatic access to verification results.

        Returns:
            Dict with summary data.
        """
        phase_results = self.verify_all_phases()
        current_phase = self.get_current_phase()

        phases_summary = []
        for pr in phase_results:
            phases_summary.append(
                {
                    "phase_id": pr.phase_id,
                    "phase_name": pr.phase_name,
                    "status": pr.status,
                    "hooks_fired": pr.hooks_fired,
                    "hooks_expected": pr.hooks_expected,
                }
            )

        issues = []
        for pr in phase_results:
            for hr in pr.hooks_verified:
                if hr.status in ("unexpected_block", "unexpected_approve"):
                    issues.append(
                        {
                            "hook": hr.hook_name,
                            "status": hr.status,
                            "message": hr.message,
                        }
                    )

        undefined = self.get_undefined_hooks()
        if undefined:
            issues.append(
                {
                    "hook": "undefined",
                    "status": "undefined_hooks_fired",
                    "message": f"Undefined hooks fired: {', '.join(undefined)}",
                }
            )

        return {
            "session_id": self.session_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "execution_count": len(self.executions),
            "current_phase": current_phase.id if current_phase else None,
            "phases": phases_summary,
            "fired_hooks": len(self.get_fired_hooks()),
            "unfired_hooks": len(self.get_unfired_hooks()),
            "undefined_hooks": len(undefined),
            "issues": issues,
            "has_issues": len(issues) > 0,
        }


def verify_current_session(verbose: bool = False, since_hours: float | None = None) -> str:
    """Convenience function to verify the current session.

    Args:
        verbose: If True, include detailed hook information.
        since_hours: Only include logs from the last N hours.

    Returns:
        Formatted verification report.
    """
    verifier = WorkflowVerifier(since_hours=since_hours)
    return verifier.generate_report(verbose=verbose)


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Verify workflow execution")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Include detailed hook information"
    )
    parser.add_argument(
        "--since",
        type=float,
        metavar="HOURS",
        help="Only include logs from the last N hours (e.g., --since 1 for last hour)",
    )
    parser.add_argument(
        "--session-id",
        type=str,
        metavar="SESSION_ID",
        help="Session ID to verify (required when running standalone)",
    )
    args = parser.parse_args()

    # Issue #2496: Use handle_session_id_arg() return value instead of global state
    validated_session_id = handle_session_id_arg(args.session_id)

    # Pass validated session_id to WorkflowVerifier
    # If None, WorkflowVerifier will use PPID-based fallback
    verifier = WorkflowVerifier(session_id=validated_session_id, since_hours=args.since)

    # Issue #2461: Warn if using fallback session ID (ppid-*)
    if verifier.session_id.startswith("ppid-"):
        script_name = os.path.basename(sys.argv[0])
        warning_lines = [
            f"⚠️ セッションIDがフォールバック値({verifier.session_id})です。",
            "   正しいログが取得できません。",
            "   --session-id オプションでセッションIDを指定してください。",
            f"   例: python3 {script_name} --session-id d80d22ce-2bdc-4efb-ac27-7eb604ad9b6f",
        ]
        print("\n".join(warning_lines) + "\n", file=sys.stderr)

    print(verifier.generate_report(verbose=args.verbose))
