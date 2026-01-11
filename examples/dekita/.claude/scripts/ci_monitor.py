#!/usr/bin/env python3
"""PRのCI・レビュー状態を監視する。

Why:
    CI待機中のBEHIND検知・自動リベース、レビュー完了検知を
    自動化し、開発フローを効率化するため。

What:
    - monitor_pr(): PRの状態を監視
    - handle_behind(): BEHIND検知時に自動リベース
    - check_reviews(): レビュー完了を検知
    - emit_event(): 構造化イベントを出力

State:
    - reads: GitHub API（gh pr view, gh api）
    - writes: .claude/logs/session/*/ci-monitor-*.jsonl（ログ）

Remarks:
    - Single PR mode: 1つのPRを監視し完了まで待機
    - Multi-PR mode: 複数PRを並列監視
    - --notify-only: 1回チェックして即終了
    - --early-exit: CI失敗/レビュー検知で即終了（シフトレフト）
    - イベント: BEHIND_DETECTED, CI_PASSED, CI_FAILED, REVIEW_COMPLETED等

Changelog:
    - silenvx/dekita#1000: CI監視機能を追加
    - silenvx/dekita#1637: --wait-review, --jsonをデフォルト有効化
    - silenvx/dekita#2399: --mergeオプション廃止
    - silenvx/dekita#2454: オプション簡素化
    - silenvx/dekita#2624: ci_monitorパッケージへのリファクタリング

Note:
    This file is now a thin wrapper that imports from the ci_monitor package.
    See Issue #2624 for the refactoring details.
"""

import argparse
import json
import sys
from pathlib import Path

# Add parent directory to path for importing common module
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
# Import everything from the ci_monitor package
from ci_monitor import (
    DEFAULT_TIMEOUT_MINUTES,
    EventType,
    check_self_reference,
    monitor_multiple_prs,
    monitor_pr,
    set_session_id,
    validate_pr_numbers,
)
from lib.session import is_valid_session_id


def positive_int(value: str) -> int:
    """Validate that value is a positive integer (for argparse type)."""
    try:
        int_value = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: '{value}'") from None
    if int_value < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got: {int_value}")
    return int_value


def main():
    """Main entry point."""
    # Issue #2454: Simplified argument parser - removed rarely-used options
    # Removed: --interval, --max-rebase, --no-wait-stable, --json/--no-json,
    #          --wait-review/--no-wait-review, --notify-only, --resolve-before-rebase,
    #          --status, --result
    # These options are now hardcoded to their default/recommended values.
    parser = argparse.ArgumentParser(
        description="Monitor PR CI status with auto-rebase and review detection"
    )
    parser.add_argument("pr_numbers", nargs="+", help="PR number(s) to monitor")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_MINUTES,
        help=f"Timeout in minutes (default: {DEFAULT_TIMEOUT_MINUTES})",
    )
    parser.add_argument(
        "--early-exit",
        action="store_true",
        help="Exit immediately when review comments are detected (shift-left). CI failures already exit immediately regardless of this flag.",
    )
    # Issue #2310: Session ID propagation for proper log identification
    parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Claude session ID for log tracking (passed from Claude context)",
    )

    args = parser.parse_args()

    # Issue #2310: Set session ID for proper session tracking.
    # Issue #2624: Uses session module for session ID management.
    if args.session_id:
        # Validate UUID format using is_valid_session_id() (Issue #2301)
        # This prevents invalid session IDs from causing log integrity issues
        if not is_valid_session_id(args.session_id):
            parser.error(f"--session-id must be a valid UUID, got: {args.session_id}")
        set_session_id(args.session_id)

    pr_numbers = args.pr_numbers

    # Validate PR numbers before processing
    validate_pr_numbers(pr_numbers)

    # Check if any PR modifies ci-monitor.py itself
    for pr_number in pr_numbers:
        if check_self_reference(pr_number):
            print(
                f"\n⚠️  Warning: PR #{pr_number} modifies ci-monitor.py itself.\n"
                "   The running monitor may behave differently from the changes being tested.\n"
                "\n"
                "   Recommended actions:\n"
                "   1. Even if CI passes, consider re-verifying with the updated script\n"
                "   2. Confirm tests are running against the changed code\n"
                "   3. Monitor script behavior after merge\n",
                file=sys.stderr,
            )

    # Single PR mode
    if len(pr_numbers) == 1:
        pr_number = pr_numbers[0]

        # Issue #2454: Removed --status, --result, --notify-only modes
        # These were for background execution which is not used in practice.

        # Monitor PR with hardcoded defaults (Issue #2454)
        result = monitor_pr(
            pr_number=pr_number,
            timeout_minutes=args.timeout,
            early_exit=args.early_exit,
        )

        # Final output (always JSON - Issue #2454)
        output = {
            "success": result.success,
            "message": result.message,
            "rebase_count": result.rebase_count,
            "review_completed": result.review_completed,
            "ci_passed": result.ci_passed,
        }
        if result.final_state:
            output["final_state"] = {
                "merge_state": result.final_state.merge_state.value,
                "pending_reviewers": result.final_state.pending_reviewers,
                "check_status": result.final_state.check_status.value,
            }
            if result.final_state.review_comments:
                output["review_comments"] = result.final_state.review_comments
            if result.final_state.unresolved_threads:
                output["unresolved_threads"] = result.final_state.unresolved_threads
        print(json.dumps(output, indent=2))

        # Issue #2399: --merge removed. Use `gh pr merge` directly to ensure
        # PostToolUse hooks (e.g., post-merge-reflection-enforcer) fire correctly.
        sys.exit(0 if result.success else 1)

    # Multi-PR mode (Issue #2454: simplified, removed unused options)
    # Note: --early-exit is ignored in multi-PR mode (single-PR feature only)
    if args.early_exit:
        print(
            "Warning: --early-exit ignored in multi-PR mode",
            file=sys.stderr,
        )

    events = monitor_multiple_prs(
        pr_numbers=pr_numbers,
        timeout_minutes=args.timeout,
    )

    # Output results (always JSON - Issue #2454)
    any_failure = False
    output = {
        "mode": "multi-pr",
        "prs": [],
    }
    for evt in events:
        pr_data = {
            "pr_number": evt.pr_number,
            "event": evt.event.to_dict() if evt.event else None,
        }
        if evt.state:
            pr_data["state"] = {
                "merge_state": evt.state.merge_state.value,
                "check_status": evt.state.check_status.value,
                "pending_reviewers": evt.state.pending_reviewers,
            }
        output["prs"].append(pr_data)
        if evt.event and evt.event.event_type in (
            EventType.CI_FAILED,
            EventType.ERROR,
            EventType.TIMEOUT,
            EventType.DIRTY_DETECTED,
            EventType.BEHIND_DETECTED,
            EventType.REVIEW_ERROR,
        ):
            any_failure = True
    print(json.dumps(output, indent=2))

    sys.exit(1 if any_failure else 0)


if __name__ == "__main__":
    main()
