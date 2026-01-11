#!/usr/bin/env python3
"""フックの期待動作と実際の動作のギャップを自動検知する。

Why:
    フックが正しく動作しているかを自動検証し、サイレント障害や
    異常な動作パターンを早期発見する。問題のあるフックを放置すると
    ワークフロー全体の品質が低下する。

What:
    - サイレント障害検出（例外発生したフック）
    - ブロックループ検出（短時間に連続ブロック）
    - 未実行フック検出（登録されているが実行されていない）

State:
    reads: .claude/state/execution-logs/hook-execution-*.jsonl
    reads: .claude/settings.json
    writes: .claude/state/metrics/behavior-anomalies-*.jsonl

Remarks:
    - 情報提供のみでブロックしない
    - hook_effectiveness_evaluatorは効率性評価、これは動作評価
    - metadata.json廃止に伴い、settings.jsonから登録フック一覧を取得

Changelog:
    - silenvx/dekita#1317: 専用メトリクスログファイル追加
    - silenvx/dekita#1840: セッション固有ファイルへの出力対応
    - silenvx/dekita#2607: HookContextによるセッションID管理追加
    - silenvx/dekita#2762: metadata.json依存を削除、settings.jsonベースに移行
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
LOOP_THRESHOLD = 5  # Same hook blocking 5+ times in short window
LOOP_WINDOW_SECONDS = 60  # Window for loop detection
MAX_ISSUES_TO_REPORT = 10  # Maximum issues to report

# Path to settings
SETTINGS_PATH = _PROJECT_DIR / ".claude" / "settings.json"

# Self reference
SELF_HOOK_NAME = "hook_behavior_evaluator"


def load_settings() -> dict[str, Any]:
    """Load settings.json."""
    try:
        if SETTINGS_PATH.exists():
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass  # Settings file is optional; return empty dict if unreadable
    return {}


def get_registered_hooks(settings: dict[str, Any]) -> dict[str, list[str]]:
    """Extract registered hooks and their triggers from settings.json.

    Returns:
        Dict mapping hook_name to list of trigger types (e.g., ["PreToolUse", "Stop"])
    """
    registered: dict[str, list[str]] = {}
    hooks_config = settings.get("hooks", {})

    for event_type, event_hooks in hooks_config.items():
        if not isinstance(event_hooks, list):
            continue
        for hook_group in event_hooks:
            if not isinstance(hook_group, dict):
                continue
            hooks_list = hook_group.get("hooks", [])
            for hook in hooks_list:
                if isinstance(hook, dict):
                    command = hook.get("command", "")
                    # Extract hook name from command path
                    # e.g., "python3 .../hooks/branch_check.py" -> "branch_check"
                    match = re.search(r"/([^/]+)\.py[\"']?\s*$", command)
                    if match:
                        hook_name = match.group(1)
                        if hook_name not in registered:
                            registered[hook_name] = []
                        if event_type not in registered[hook_name]:
                            registered[hook_name].append(event_type)
    return registered


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


def detect_missing_hooks(logs: list[dict], registered_hooks: dict[str, list[str]]) -> list[dict]:
    """Detect hooks registered in settings.json but never executed.

    Note: If no PreToolUse/PostToolUse hooks were executed during the session,
    this check is skipped to avoid false positives for sessions without tool usage.
    """
    issues = []

    executed_hooks = {entry.get("hook") for entry in logs}

    def has_pretool_posttool(triggers: list[str]) -> bool:
        """Check if any trigger is PreToolUse or PostToolUse."""
        return any("PreToolUse" in t or "PostToolUse" in t for t in triggers)

    # Check if any PreToolUse/PostToolUse hooks were executed in this session
    pretool_posttool_hooks = {
        hook_name
        for hook_name, triggers in registered_hooks.items()
        if has_pretool_posttool(triggers)
    }
    executed_pretool_posttool = executed_hooks & pretool_posttool_hooks
    if not executed_pretool_posttool:
        return issues  # Skip check - no tool usage in this session

    for hook_name, triggers in registered_hooks.items():
        if hook_name in executed_hooks:
            continue
        if hook_name == SELF_HOOK_NAME:
            continue

        # Only report if it's a PreToolUse or PostToolUse hook
        if has_pretool_posttool(triggers):
            issues.append(
                {
                    "type": "missing_execution",
                    "hook": hook_name,
                    "trigger": ", ".join(triggers),
                    "message": (
                        f"{hook_name} は登録済みだがセッション中に一度も実行されていない。"
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
            "- ブロックループ: トリガー条件が厳しすぎないか確認",
            "- 未実行: settings.json の登録状況を確認",
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
        settings = load_settings()
        registered_hooks = get_registered_hooks(settings)

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
        all_issues.extend(detect_block_loops(logs))
        all_issues.extend(detect_missing_hooks(logs, registered_hooks))

        if all_issues:
            report = format_report(all_issues, len(logs))
            result["systemMessage"] = report

            # Issue #1317: 専用ログファイルに詳細を出力
            log_behavior_anomalies(ctx, all_issues, len(logs))

            # 詳細情報を記録
            issues_to_log = all_issues[:MAX_ISSUES_TO_REPORT]
            issues_detail = []
            for issue in issues_to_log:
                issue_type = issue.get("type", "unknown")
                detail = {
                    "hook": issue.get("hook", "unknown"),
                    "type": issue_type,
                }
                # タイプ別に追加情報を記録
                if issue_type == "missing_execution":
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
