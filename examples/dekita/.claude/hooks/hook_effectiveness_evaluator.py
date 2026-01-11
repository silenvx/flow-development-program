#!/usr/bin/env python3
"""セッション中のフック実行を分析し、改善提案を出力する。

Why:
    フックが適切に機能しているかをセッション終了時に評価し、
    過剰発動・無視された警告・繰り返しブロックを検出する。
    フックの品質改善サイクルを回すための情報を提供する。

What:
    - 過剰発動検出（発動多数でほぼapprove）
    - 無視された警告検出（警告出力されても対応なし）
    - 繰り返しブロック検出（同じ理由で複数回ブロック）
    - 改善提案を生成・出力

State:
    reads: .claude/state/execution-logs/hook-execution-*.jsonl

Remarks:
    - 情報提供のみでブロックしない
    - hook-behavior-evaluatorは動作評価、これは効率性評価
    - 自己参照を除外して誤検知を防止

Changelog:
    - silenvx/dekita#2607: HookContextによるセッションID管理追加
"""

import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from common import EXECUTION_LOG_DIR
from lib.execution import log_hook_execution
from lib.logging import read_all_session_log_entries
from lib.results import print_approve_and_log_skip
from lib.session import create_hook_context, parse_hook_input

# Thresholds for analysis
OVERACTIVE_THRESHOLD = 10  # More than 10 triggers = potentially overactive
NOISE_RATIO_THRESHOLD = 0.95  # 95%+ approve = probably noise
SESSION_WINDOW_MINUTES = 60  # Analyze last 60 minutes
REASON_TRUNCATION_LENGTH = 100  # Max length for reason strings
INPUT_PREVIEW_TRUNCATION_LENGTH = 50  # Max length for input preview strings
MAX_SUGGESTIONS = 5  # Maximum number of suggestions to display
WARNING_THRESHOLD = 3  # Minimum warnings to flag as "ignored"
REPEATED_BLOCK_THRESHOLD = 3  # Minimum repeats to flag as "repeated block"

# Exclude this hook from analysis to avoid self-flagging
SELF_HOOK_NAME = "hook_effectiveness_evaluator"


def load_session_logs(session_window_minutes: int = SESSION_WINDOW_MINUTES) -> list[dict]:
    """Load hook execution logs from current session window (all sessions)."""
    entries = read_all_session_log_entries(EXECUTION_LOG_DIR, "hook-execution")
    if not entries:
        return []

    logs = []
    cutoff = datetime.now(UTC) - timedelta(minutes=session_window_minutes)

    for entry in entries:
        try:
            # Parse timestamp
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


def analyze_hook_frequency(logs: list[dict]) -> dict[str, dict[str, Any]]:
    """Analyze hook trigger frequency and approve/block ratio."""
    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "total": 0,
            "approve": 0,
            "block": 0,
            "reasons": [],
            "tool_names": [],
            "input_previews": [],
        }
    )

    for entry in logs:
        hook = entry.get("hook", "unknown")
        decision = entry.get("decision", "approve")
        stats[hook]["total"] += 1
        if decision == "approve":
            stats[hook]["approve"] += 1
        else:
            stats[hook]["block"] += 1
            reason = entry.get("reason", "")
            if reason:
                stats[hook]["reasons"].append(reason[:REASON_TRUNCATION_LENGTH])

        # Collect input context from details
        details = entry.get("details", {})
        if isinstance(details, dict):
            tool_name = details.get("tool_name")
            if tool_name:
                stats[hook]["tool_names"].append(tool_name)
            input_preview = details.get("input_preview", "")
            if input_preview:
                # Truncate for aggregation
                stats[hook]["input_previews"].append(
                    input_preview[:INPUT_PREVIEW_TRUNCATION_LENGTH]
                )

    return dict(stats)


def detect_overactive_hooks(stats: dict[str, dict[str, Any]]) -> list[dict]:
    """Detect hooks that trigger too frequently with little effect."""
    issues = []

    for hook, data in stats.items():
        # Skip self to avoid false positive
        if hook == SELF_HOOK_NAME:
            continue

        total = data["total"]
        approve = data["approve"]

        if total >= OVERACTIVE_THRESHOLD:
            ratio = approve / total if total > 0 else 0
            if ratio >= NOISE_RATIO_THRESHOLD:
                # Analyze input patterns for more specific suggestions
                tool_names = data.get("tool_names", [])
                input_previews = data.get("input_previews", [])

                # Find most common tool triggering this hook
                tool_counter = Counter(tool_names)
                most_common_tool = tool_counter.most_common(1)

                # Find common input patterns (prefix matching)
                input_counter = Counter(input_previews)
                common_inputs = input_counter.most_common(3)

                suggestion = f"{hook}が{total}回発動し{round(ratio * 100, 1)}%がapprove。"

                # Add tool-specific insight (no leading space for Japanese text)
                if most_common_tool:
                    tool, count = most_common_tool[0]
                    suggestion += f"主に{tool}ツールで発動({count}回)。"

                # Add input pattern insight
                if common_inputs:
                    patterns = [f'"{inp}"' for inp, _ in common_inputs[:2] if inp]
                    if patterns:
                        suggestion += f"よく見る入力: {', '.join(patterns)}。"

                suggestion += "発動条件を絞るか、不要なら無効化を検討。"

                issues.append(
                    {
                        "hook": hook,
                        "type": "overactive",
                        "total": total,
                        "approve_ratio": round(ratio * 100, 1),
                        "suggestion": suggestion,
                    }
                )

    return issues


def detect_repeated_blocks(logs: list[dict]) -> list[dict]:
    """Detect hooks that repeatedly block for the same reason."""
    issues = []
    block_sequences: dict[str, list[str]] = defaultdict(list)

    for entry in logs:
        if entry.get("decision") == "block":
            hook = entry.get("hook", "unknown")
            reason = entry.get("reason", "")[:REASON_TRUNCATION_LENGTH]
            block_sequences[hook].append(reason)

    for hook, reasons in block_sequences.items():
        if len(reasons) >= REPEATED_BLOCK_THRESHOLD:
            # Check for repeated similar reasons
            reason_counts = Counter(reasons)
            most_common = reason_counts.most_common(1)
            if most_common and most_common[0][1] >= REPEATED_BLOCK_THRESHOLD:
                issues.append(
                    {
                        "hook": hook,
                        "type": "repeated_block",
                        "count": most_common[0][1],
                        "reason": most_common[0][0],
                        "suggestion": (
                            f"{hook}が同じ理由で{most_common[0][1]}回ブロック。"
                            "ブロック条件の見直しまたはガイダンス改善を検討。"
                        ),
                    }
                )

    return issues


def detect_ignored_warnings(logs: list[dict]) -> list[dict]:
    """Detect warnings that were likely ignored (same hook warned multiple times)."""
    issues = []
    warning_sequences: dict[str, int] = defaultdict(int)

    for entry in logs:
        if entry.get("decision") == "approve":
            reason = entry.get("reason", "")
            # Warnings typically contain these patterns
            if reason and any(
                keyword in reason.lower() for keyword in ["warning", "⚠️", "注意", "確認", "推奨"]
            ):
                hook = entry.get("hook", "unknown")
                warning_sequences[hook] += 1

    for hook, count in warning_sequences.items():
        if count >= WARNING_THRESHOLD:
            issues.append(
                {
                    "hook": hook,
                    "type": "ignored_warning",
                    "count": count,
                    "suggestion": (
                        f"{hook}の警告が{count}回出力されたが対応なし。"
                        "警告メッセージの明確化またはblock化を検討。"
                    ),
                }
            )

    return issues


def generate_improvement_suggestions(
    logs: list[dict], stats: dict[str, dict[str, Any]]
) -> list[str]:
    """Generate actionable improvement suggestions."""
    suggestions = []

    # Analyze hooks
    overactive = detect_overactive_hooks(stats)
    repeated = detect_repeated_blocks(logs)
    ignored = detect_ignored_warnings(logs)

    for issue in overactive:
        suggestions.append(f"[過剰発動] {issue['suggestion']}")

    for issue in repeated:
        suggestions.append(f"[繰り返しブロック] {issue['suggestion']}")

    for issue in ignored:
        suggestions.append(f"[無視された警告] {issue['suggestion']}")

    return suggestions


def main():
    """Stop hook to evaluate hook effectiveness."""
    result = {"decision": "approve"}

    try:
        # Read input from stdin (Stop hook receives JSON context)
        input_data = parse_hook_input()
        # Issue #2607: Create context for session_id logging
        ctx = create_hook_context(input_data)

        # Prevent infinite loops: if stop_hook_active is set, approve immediately
        if input_data.get("stop_hook_active"):
            print_approve_and_log_skip(SELF_HOOK_NAME, "stop_hook_active", ctx=ctx)
            return

        # Load and analyze session logs
        logs = load_session_logs()

        if not logs:
            # No logs to analyze
            log_hook_execution(SELF_HOOK_NAME, "approve", "ログなし")
            print(json.dumps(result))
            return

        stats = analyze_hook_frequency(logs)
        suggestions = generate_improvement_suggestions(logs, stats)

        if suggestions:
            # Format summary
            summary_lines = [
                "## フック有効性レビュー",
                f"分析対象: 直近{SESSION_WINDOW_MINUTES}分間、{len(logs)}件のフック実行",
                "",
                "### 改善提案:",
            ]
            for i, suggestion in enumerate(suggestions[:MAX_SUGGESTIONS], 1):
                summary_lines.append(f"{i}. {suggestion}")

            summary_lines.extend(
                [
                    "",
                    "**アクション**: 上記フックの改善が必要な場合、",
                    "- フックスクリプトの条件を調整",
                    "- settings.jsonでの無効化",
                    "- メッセージの明確化",
                    "のいずれかを検討してください。",
                ]
            )

            result["systemMessage"] = "\n".join(summary_lines)
            log_hook_execution(
                SELF_HOOK_NAME,
                "approve",
                f"{len(suggestions)}件の改善提案",
                {"suggestions": len(suggestions), "analyzed_logs": len(logs)},
            )
        else:
            # No issues found
            log_hook_execution(
                SELF_HOOK_NAME,
                "approve",
                "問題なし",
                {"analyzed_logs": len(logs)},
            )

    except Exception as e:
        # Don't block on errors
        print(f"[{SELF_HOOK_NAME}] Error: {e}", file=sys.stderr)
        log_hook_execution(SELF_HOOK_NAME, "approve", f"Error: {e}")

    print(json.dumps(result))


if __name__ == "__main__":
    main()
