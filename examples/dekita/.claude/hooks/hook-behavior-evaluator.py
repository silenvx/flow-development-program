#!/usr/bin/env python3
"""フックの期待動作と実際の動作のギャップを自動検知する。

Why:
    フックが正しく動作しているかを自動検証し、サイレント障害や
    異常な動作パターンを早期発見する。問題のあるフックを放置すると
    ワークフロー全体の品質が低下する。

What:
    - サイレント障害検出（例外発生したフック）
    - Block率異常検出（期待範囲外のブロック率）
    - ブロックループ検出（短時間に連続ブロック）
    - 未実行フック検出（activeだが実行されていない）

State:
    reads: .claude/state/execution-logs/hook-execution-*.jsonl
    reads: .claude/hooks/metadata.json
    writes: .claude/state/metrics/behavior-anomalies-*.jsonl

Remarks:
    - 情報提供のみでブロックしない
    - hook-effectiveness-evaluatorは効率性評価、これは動作評価
    - metadata.jsonのexpected_block_rateと比較して異常検出

Changelog:
    - silenvx/dekita#1317: 専用メトリクスログファイル追加
    - silenvx/dekita#1840: セッション固有ファイルへの出力対応
    - silenvx/dekita#2607: HookContextによるセッションID管理追加
"""

import json
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from common import _PROJECT_DIR, EXECUTION_LOG_DIR, METRICS_LOG_DIR
from lib.execution import log_hook_execution
from lib.logging import log_to_session_file, read_all_session_log_entries
from lib.results import print_approve_and_log_skip
from lib.session import HookContext, create_hook_context, parse_hook_input
from lib.timestamp import get_local_timestamp

# Analysis configuration
SESSION_WINDOW_MINUTES = 60  # Analyze last 60 minutes
MIN_EXECUTIONS_FOR_RATE_CHECK = 5  # Need at least N executions to check rate
LOOP_THRESHOLD = 5  # Same hook blocking 5+ times in short window
LOOP_WINDOW_SECONDS = 60  # Window for loop detection
MAX_ISSUES_TO_REPORT = 10  # Maximum issues to report

# Path to metadata
METADATA_PATH = _PROJECT_DIR / ".claude" / "hooks" / "metadata.json"

# Self reference
SELF_HOOK_NAME = "hook-behavior-evaluator"


def load_metadata() -> dict[str, Any]:
    """Load hook metadata from metadata.json."""
    try:
        if METADATA_PATH.exists():
            with open(METADATA_PATH, encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        # Metadata is optional; fall back to empty config on error
        pass
    return {}


def parse_expected_block_rate(rate_str: str) -> tuple[float, float]:
    """Parse expected_block_rate string like '5-10%' into (min, max) tuple.

    Returns:
        Tuple of (min_rate, max_rate) as decimals (0.0-1.0)
        Returns (0.0, 1.0) if parsing fails (accepts any rate)
    """
    if not rate_str:
        return (0.0, 1.0)

    # Remove % and whitespace
    rate_str = rate_str.replace("%", "").strip()

    # Try to parse as range (e.g., "5-10")
    match = re.match(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", rate_str)
    if match:
        min_rate = float(match.group(1)) / 100
        max_rate = float(match.group(2)) / 100
        # Ensure min <= max (swap if reversed)
        if min_rate > max_rate:
            min_rate, max_rate = max_rate, min_rate
        return (min_rate, max_rate)

    # Try to parse as single value (e.g., "5")
    try:
        rate = float(rate_str) / 100
        # Allow ±5% margin for single values
        return (max(0, rate - 0.05), min(1, rate + 0.05))
    except ValueError:
        return (0.0, 1.0)


def load_session_logs(session_window_minutes: int = SESSION_WINDOW_MINUTES) -> list[dict]:
    """Load hook execution logs from current session window (all sessions)."""
    entries = read_all_session_log_entries(EXECUTION_LOG_DIR, "hook-execution")
    if not entries:
        return []

    logs = []
    cutoff = datetime.now(UTC) - timedelta(minutes=session_window_minutes)

    for entry in entries:
        try:
            ts_str = entry.get("timestamp", "")
            if ts_str:
                if ts_str.endswith("Z"):
                    ts_str = ts_str[:-1] + "+00:00"
                ts = datetime.fromisoformat(ts_str)
                if ts >= cutoff:
                    entry["_parsed_timestamp"] = ts
                    logs.append(entry)
        except ValueError:
            continue

    return logs


def detect_silent_failures(logs: list[dict]) -> list[dict]:
    """Detect hooks that threw exceptions or had errors.

    Looks for:
    - reason containing "Error:" or "Exception"
    - decision is approve but reason suggests failure
    """
    issues = []
    error_patterns = [
        r"Error:",
        r"Exception",
        r"Traceback",
        r"failed to",
        r"timeout",
        r"could not",
    ]
    pattern = re.compile("|".join(error_patterns), re.IGNORECASE)

    error_hooks: dict[str, list[str]] = defaultdict(list)

    for entry in logs:
        hook = entry.get("hook", "unknown")
        if hook == SELF_HOOK_NAME:
            continue

        reason = entry.get("reason", "")
        if reason and pattern.search(reason):
            error_hooks[hook].append(reason[:100])

    for hook, errors in error_hooks.items():
        if errors:
            issues.append(
                {
                    "type": "silent_failure",
                    "hook": hook,
                    "count": len(errors),
                    "examples": errors[:3],
                    "message": f"{hook} で {len(errors)} 件のエラーが発生。例: {errors[0][:50]}...",
                }
            )

    return issues


def detect_block_rate_anomalies(logs: list[dict], metadata: dict) -> list[dict]:
    """Detect hooks with block rate outside expected range."""
    issues = []

    # Aggregate by hook
    hook_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "block": 0})

    for entry in logs:
        hook = entry.get("hook", "unknown")
        if hook == SELF_HOOK_NAME:
            continue
        hook_stats[hook]["total"] += 1
        if entry.get("decision") == "block":
            hook_stats[hook]["block"] += 1

    hooks_metadata = metadata.get("hooks", {})

    for hook, stats in hook_stats.items():
        total = stats["total"]
        if total < MIN_EXECUTIONS_FOR_RATE_CHECK:
            continue

        block_count = stats["block"]
        actual_rate = block_count / total

        # Get expected rate from metadata
        hook_meta = hooks_metadata.get(hook, {})
        expected_str = hook_meta.get("expected_block_rate", "")
        if not expected_str:
            continue

        min_rate, max_rate = parse_expected_block_rate(expected_str)

        # Check if actual rate is outside expected range
        # Allow 10% margin for small sample sizes
        margin = 0.1 if total < 20 else 0.05
        adjusted_min = max(0, min_rate - margin)
        adjusted_max = min(1, max_rate + margin)

        if actual_rate < adjusted_min or actual_rate > adjusted_max:
            direction = "高い" if actual_rate > adjusted_max else "低い"
            issues.append(
                {
                    "type": "block_rate_anomaly",
                    "hook": hook,
                    "actual_rate": round(actual_rate * 100, 1),
                    "expected_range": expected_str,
                    "total": total,
                    "block_count": block_count,
                    "message": (
                        f"{hook} の Block 率が期待より{direction}: "
                        f"実際 {round(actual_rate * 100, 1)}% (期待: {expected_str}、{total}回中{block_count}回ブロック)"
                    ),
                }
            )

    return issues


def detect_block_loops(logs: list[dict]) -> list[dict]:
    """Detect hooks that block repeatedly in short time windows."""
    issues = []

    # Sort logs by timestamp
    sorted_logs = sorted(
        [log for log in logs if log.get("_parsed_timestamp")], key=lambda x: x["_parsed_timestamp"]
    )

    # Track consecutive blocks per hook
    hook_block_sequences: dict[str, list[datetime]] = defaultdict(list)

    for entry in sorted_logs:
        if entry.get("decision") != "block":
            continue

        hook = entry.get("hook", "unknown")
        if hook == SELF_HOOK_NAME:
            continue

        ts = entry["_parsed_timestamp"]
        hook_block_sequences[hook].append(ts)

    # Analyze sequences for loops
    for hook, timestamps in hook_block_sequences.items():
        if len(timestamps) < LOOP_THRESHOLD:
            continue

        # Check for clusters within LOOP_WINDOW_SECONDS
        # Note: The slice timestamps[i:] creates a copy, which could be optimized
        # for very large timestamp lists using binary search. However, in practice,
        # the number of block events per hook per session is small (<100).
        for i in range(len(timestamps) - LOOP_THRESHOLD + 1):
            window_start = timestamps[i]
            window_end = window_start + timedelta(seconds=LOOP_WINDOW_SECONDS)
            blocks_in_window = sum(1 for ts in timestamps[i:] if ts <= window_end)

            if blocks_in_window >= LOOP_THRESHOLD:
                issues.append(
                    {
                        "type": "block_loop",
                        "hook": hook,
                        "count": blocks_in_window,
                        "window_seconds": LOOP_WINDOW_SECONDS,
                        "message": (
                            f"{hook} が {LOOP_WINDOW_SECONDS}秒以内に "
                            f"{blocks_in_window}回連続ブロック。"
                            "無限ループの兆候または条件の見直しが必要。"
                        ),
                    }
                )
                break  # Report once per hook

    return issues


def detect_missing_hooks(logs: list[dict], metadata: dict) -> list[dict]:
    """Detect hooks defined in metadata but never executed.

    Hooks can be excluded from this check by setting skip_missing_check: true
    in metadata.json. This is useful for hooks with specific trigger conditions
    that may not be met during every session.

    Note: If no PreToolUse/PostToolUse hooks (as defined in metadata) were executed
    during the session, this check is skipped to avoid false positives for sessions
    without tool usage.
    """
    issues = []

    hooks_metadata = metadata.get("hooks", {})
    executed_hooks = {entry.get("hook") for entry in logs}

    # Check if any PreToolUse/PostToolUse hooks were executed in this session
    # If none were executed, skip the missing check to avoid false positives
    # for sessions without tool usage (e.g., SessionStart only)
    pretool_posttool_hooks = {
        hook_name
        for hook_name, hook_meta in hooks_metadata.items()
        if "PreToolUse" in hook_meta.get("trigger", "")
        or "PostToolUse" in hook_meta.get("trigger", "")
    }
    executed_pretool_posttool = executed_hooks & pretool_posttool_hooks
    if not executed_pretool_posttool:
        return issues  # Skip check - no tool usage in this session

    for hook_name, hook_meta in hooks_metadata.items():
        if hook_meta.get("status") != "active":
            continue
        if hook_name in executed_hooks:
            continue
        if hook_name == SELF_HOOK_NAME:
            continue
        # Skip hooks marked with skip_missing_check flag
        if hook_meta.get("skip_missing_check", False):
            continue

        # Only report if it's a PreToolUse or PostToolUse hook
        trigger = hook_meta.get("trigger", "")
        if "PreToolUse" in trigger or "PostToolUse" in trigger:
            issues.append(
                {
                    "type": "missing_execution",
                    "hook": hook_name,
                    "trigger": trigger,
                    "message": (
                        f"{hook_name} は active だがセッション中に一度も実行されていない。"
                        "設定ミスまたはトリガー条件の問題の可能性。"
                    ),
                }
            )

    return issues


def log_behavior_anomalies(ctx: HookContext, issues: list[dict], log_count: int) -> None:
    """Log behavior anomalies to dedicated metrics file (Issue #1317).

    Logs each issue as a separate JSONL entry for easier analysis and aggregation.
    Issue #1840: Now writes to session-specific file.

    Args:
        ctx: HookContext for session information.
        issues: List of detected behavior anomaly issues.
        log_count: Number of logs analyzed.
    """
    if not issues:
        return

    session_id = ctx.get_session_id()

    for issue in issues:  # Log all anomalies without truncation
        entry = {
            "timestamp": get_local_timestamp(),  # Generate per entry for time-series analysis
            "analyzed_logs": log_count,
            "type": issue.get("type", "unknown"),
            "hook": issue.get("hook", "unknown"),
        }

        # Add type-specific details with explicit defaults
        issue_type = issue.get("type")
        if issue_type == "silent_failure":
            entry["count"] = issue.get("count", 0)
            entry["examples"] = issue.get("examples", [])[:3]
        elif issue_type == "block_rate_anomaly":
            entry["actual_rate"] = issue.get("actual_rate", 0.0)
            entry["expected_range"] = issue.get("expected_range", "")
            entry["total"] = issue.get("total", 0)
            entry["block_count"] = issue.get("block_count", 0)
        elif issue_type == "block_loop":
            entry["count"] = issue.get("count", 0)
            entry["window_seconds"] = issue.get("window_seconds", 0)
        elif issue_type == "missing_execution":
            entry["trigger"] = issue.get("trigger", "")

        # Issue #1840: Write to session-specific file
        log_to_session_file(METRICS_LOG_DIR, "behavior-anomalies", session_id, entry)


def format_report(issues: list[dict], log_count: int) -> str:
    """Format issues into a human-readable report."""
    if not issues:
        return ""

    lines = [
        "## Hook 動作評価レポート",
        f"分析対象: 直近 {SESSION_WINDOW_MINUTES} 分間、{log_count} 件の実行ログ",
        "",
    ]

    # Group by type
    by_type: dict[str, list[dict]] = defaultdict(list)
    for issue in issues:
        by_type[issue["type"]].append(issue)

    type_labels = {
        "silent_failure": "エラー検出",
        "block_rate_anomaly": "Block 率異常",
        "block_loop": "ブロックループ兆候",
        "missing_execution": "未実行 Hook",
    }

    for issue_type, type_issues in by_type.items():
        label = type_labels.get(issue_type, issue_type)
        lines.append(f"### {label} ({len(type_issues)} 件)")
        for i, issue in enumerate(type_issues[:MAX_ISSUES_TO_REPORT], 1):
            lines.append(f"{i}. {issue['message']}")
        lines.append("")

    lines.extend(
        [
            "---",
            "**推奨アクション**:",
            "- エラー検出: ログを確認し、例外処理を改善",
            "- Block 率異常: metadata.json の expected_block_rate を更新、または条件を見直し",
            "- ブロックループ: トリガー条件が厳しすぎないか確認",
            "- 未実行: settings.json と metadata.json の整合性を確認",
        ]
    )

    return "\n".join(lines)


def main():
    """Stop hook to evaluate hook behavior."""
    result = {"decision": "approve"}

    try:
        # Read input from stdin
        input_data = parse_hook_input()

        ctx = create_hook_context(input_data)

        # Prevent infinite loops
        # Issue #2607: Create context for session_id logging
        if input_data.get("stop_hook_active"):
            print_approve_and_log_skip(SELF_HOOK_NAME, "stop_hook_active", ctx=ctx)
            return

        # Load data
        logs = load_session_logs()
        metadata = load_metadata()

        if not logs:
            # Early return when no logs: detect_missing_hooks would flag all active
            # PreToolUse/PostToolUse hooks as "missing" which is noise at session start.
            # We only check for missing hooks after some activity has occurred.
            log_hook_execution(SELF_HOOK_NAME, "approve", "ログなし")
            print(json.dumps(result))
            return

        # Run detections
        all_issues: list[dict] = []
        all_issues.extend(detect_silent_failures(logs))
        all_issues.extend(detect_block_rate_anomalies(logs, metadata))
        all_issues.extend(detect_block_loops(logs))
        all_issues.extend(detect_missing_hooks(logs, metadata))

        if all_issues:
            report = format_report(all_issues, len(logs))
            result["systemMessage"] = report

            # Issue #1317: 専用ログファイルに詳細を出力
            log_behavior_anomalies(ctx, all_issues, len(logs))

            # 詳細情報を記録
            # - issue_types: 全件から抽出（概要把握のため）
            # - issues: 先頭MAX_ISSUES_TO_REPORT件のみ（ログサイズ制限）
            issues_to_log = all_issues[:MAX_ISSUES_TO_REPORT]
            issues_detail = []
            for issue in issues_to_log:
                issue_type = issue.get("type", "unknown")
                detail = {
                    "hook": issue.get("hook", "unknown"),
                    "type": issue_type,
                }
                # タイプ別に追加情報を記録
                if issue_type == "block_rate_anomaly":
                    detail["observed"] = issue.get("actual_rate")
                    detail["expected"] = issue.get("expected_range")
                    detail["total"] = issue.get("total")
                    detail["block_count"] = issue.get("block_count")
                elif issue_type == "missing_execution":
                    detail["trigger"] = issue.get("trigger")
                elif issue_type == "silent_failure":
                    detail["count"] = issue.get("count")
                    detail["examples"] = issue.get("examples", [])[:2]
                elif issue_type == "block_loop":
                    detail["count"] = issue.get("count")
                    detail["window_seconds"] = issue.get("window_seconds")
                issues_detail.append(detail)

            log_hook_execution(
                SELF_HOOK_NAME,
                "approve",
                f"{len(all_issues)} 件の動作異常を検出",
                {
                    "issues_count": len(all_issues),
                    "analyzed_logs": len(logs),
                    "issue_types": list({i.get("type", "unknown") for i in all_issues}),
                    "issues": issues_detail,
                },
            )
        else:
            log_hook_execution(
                SELF_HOOK_NAME, "approve", "動作異常なし", {"analyzed_logs": len(logs)}
            )

    except Exception as e:
        print(f"[{SELF_HOOK_NAME}] Error: {e}", file=sys.stderr)
        log_hook_execution(SELF_HOOK_NAME, "approve", f"Error: {e}")

    print(json.dumps(result))


if __name__ == "__main__":
    main()
