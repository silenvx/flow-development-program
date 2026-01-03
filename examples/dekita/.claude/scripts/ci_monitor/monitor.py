"""Monitor functions for ci-monitor.

This module contains the core monitoring functions including:
- check_once: Single check for PR state changes
- monitor_notify_only: Check once and emit event
- monitor_pr: Main monitoring loop (planned for next phase, ~1200 lines)
- monitor_multiple_prs: Parallel monitoring for multiple PRs

Extracted from ci-monitor.py as part of Issue #1765 refactoring (Phase 7).
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from ci_monitor.ai_review import (
    has_copilot_or_codex_reviewer,
    is_copilot_review_error,
)
from ci_monitor.constants import (
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_TIMEOUT_MINUTES,
)
from ci_monitor.events import create_event, emit_event, log
from ci_monitor.github_api import run_gh_command
from ci_monitor.models import (
    CheckStatus,
    EventType,
    MergeState,
    MonitorEvent,
    MultiPREvent,
)
from ci_monitor.pr_operations import (
    get_pr_state,
)
from ci_monitor.review_comments import (
    get_pr_changed_files,
    get_review_comments,
    get_unresolved_threads,
    log_review_comments_to_quality_log,
    strip_code_blocks,
)

# Add parent directory to path for importing lib modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hooks"))
from lib.execution import log_hook_execution  # noqa: E402

if TYPE_CHECKING:
    pass

# Issue #1779: Removed duplicate constant definitions
# The following constants were previously defined here but are available from ci_monitor.constants:
# - DEFAULT_MAX_RETRY_WAIT_POLLS, DEFAULT_COPILOT_PENDING_TIMEOUT
# - DEFAULT_MAX_PR_RECREATE, DEFAULT_LOCAL_CHANGES_MAX_WAIT
# - DEFAULT_LOCAL_CHANGES_WAIT_INTERVAL, ASYNC_REVIEWER_CHECK_DELAY_SECONDS
# These are not used directly in this module, but are documented here for reference.


def _sanitize_for_log(value: Any) -> Any:
    """Sanitize a value for safe logging by removing control characters.

    Args:
        value: Value to sanitize (handles str, list, dict, and other types).

    Returns:
        Sanitized value with control characters removed from strings.
    """
    if isinstance(value, str):
        # Remove all control characters (0x00-0x1f except tab 0x09)
        return "".join(c if c == "\t" or ord(c) >= 0x20 else "" for c in value)
    if isinstance(value, list):
        return [_sanitize_for_log(v) for v in value]
    if isinstance(value, dict):
        return {k: _sanitize_for_log(v) for k, v in value.items()}
    return value


def log_ci_monitor_event(
    pr_number: str,
    action: Literal[
        "ci_state_change",
        "monitor_complete",
        "monitor_start",
        "rebase",
        "rebase_file_increase",
    ],
    result: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Log ci-monitor event to hook-execution.log for post-session analysis.

    Issue #1241: Log important ci-monitor events (monitor start, rebase,
    CI state changes, monitor completion) for debugging and post-session analysis.

    Args:
        pr_number: PR number being monitored.
        action: Type of action.
        result: Result of the action.
        details: Additional details to include in the log entry.
    """
    # Sanitize inputs to prevent log injection (remove control chars)
    safe_pr = _sanitize_for_log(pr_number)
    safe_result = _sanitize_for_log(result)
    safe_details = _sanitize_for_log(details) if details else None

    event_details: dict[str, Any] = {
        "pr_number": safe_pr,
        "action": action,
        "result": safe_result,
    }
    if safe_details:
        event_details.update(safe_details)

    log_hook_execution(
        hook_name="ci-monitor",
        decision=action,
        reason=f"PR #{safe_pr}: {action} - {safe_result}",
        details=event_details,
    )


# Issue #1779: Removed log_rate_limit_warning function
# Use ci_monitor.rate_limit.log_rate_limit_warning_to_console instead


def get_pr_closes_issues(pr_number: str) -> list[str]:
    """Get issue numbers being closed by this PR.

    Extracts issue numbers from Closes/Fixes/Resolves keywords in PR body.

    Args:
        pr_number: PR number to check.

    Returns:
        List of issue numbers found.
    """
    success, output = run_gh_command(["pr", "view", pr_number, "--json", "body", "--jq", ".body"])

    if not success or not output:
        return []

    # Find blocks starting with closing keywords
    # Handles comma-separated issues: "Closes #123, #456"
    block_pattern = r"(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?):?\s+#\d+(?:\s*,\s*#\d+)*"
    blocks = re.findall(block_pattern, output, re.IGNORECASE)

    # Extract all issue numbers from matched blocks
    all_numbers = []
    for block in blocks:
        numbers = re.findall(r"#(\d+)", block)
        all_numbers.extend(numbers)

    return list(set(all_numbers))


def get_issue_incomplete_criteria(issue_number: str) -> list[str]:
    """Get incomplete acceptance criteria for an issue.

    Fetches issue body and extracts incomplete (unchecked and non-strikethrough) checkbox items.

    Args:
        issue_number: Issue number to check.

    Returns:
        List of incomplete criteria text (empty if none or error).
    """
    success, output = run_gh_command(["issue", "view", issue_number, "--json", "body,state"])

    if not success or not output:
        return []

    try:
        data = json.loads(output)
        body = data.get("body") or ""
        state = data.get("state") or ""

        # Skip closed Issues
        if state == "CLOSED":
            return []

        # Strip code blocks before extracting checkboxes (Issue #830)
        body_without_code = strip_code_blocks(body)

        # Extract checkbox items
        # Issue #823: Treat strikethrough checkboxes as completed
        incomplete = []
        pattern = r"^[\s]*[-*]\s*\[([ xX])\]\s*(.+)$"
        strikethrough_pattern = re.compile(r"^~~.+~~$")

        for line in body_without_code.split("\n"):
            match = re.match(pattern, line)
            if match:
                checkbox_mark = match.group(1).lower()
                criteria_text = match.group(2).strip()
                # Checkbox is incomplete if not marked and not strikethrough
                is_completed = checkbox_mark == "x" or bool(
                    strikethrough_pattern.match(criteria_text)
                )
                if not is_completed:
                    # Truncate long criteria text
                    if len(criteria_text) > 30:
                        criteria_text = criteria_text[:27] + "..."
                    incomplete.append(f"「{criteria_text}」")

        return incomplete
    except (json.JSONDecodeError, KeyError):
        return []


def get_observation_issues() -> list[dict]:
    """Get open issues with observation label.

    Issue #2583: Check for pending observation issues during CI waits.
    """
    import subprocess

    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--label",
                "observation",
                "--state",
                "open",
                "--json",
                "number,title",
                "--limit",
                "3",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []

        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []


def get_wait_time_suggestions(pr_number: str) -> list[str]:
    """Get actionable suggestions for wait time utilization.

    Checks for unresolved threads and returns specific action suggestions.
    """
    suggestions = []

    # Check for unresolved review threads
    # Issue #1195: Handle API failure (None means API failed, not "no unresolved threads")
    unresolved = get_unresolved_threads(pr_number)
    if unresolved:  # Skip if None (API failed) or empty list
        suggestions.append(f"未解決スレッド {len(unresolved)}件 → resolveまたはコメント対応")

    # Check for review comments (may include comments in resolved threads)
    comments = get_review_comments(pr_number)
    if comments:
        suggestions.append(
            f"レビューコメント（解決済み含む） {len(comments)}件 → 必要に応じて対応・返信を確認"
        )

    # Check for incomplete acceptance criteria in Closes target Issues (Issue #831)
    closes_issues = get_pr_closes_issues(pr_number)
    for issue_num in closes_issues:
        incomplete = get_issue_incomplete_criteria(issue_num)
        if incomplete:
            # Show up to 2 criteria with "等" suffix if more
            criteria_text = ", ".join(incomplete[:2])
            if len(incomplete) > 2:
                criteria_text += "等"
            suggestions.append(f"Issue #{issue_num} の受け入れ条件を確認 → 未完了: {criteria_text}")

    return suggestions


def show_wait_time_hint(
    pr_number: str, iteration: int, json_mode: bool = False, hint_interval: int = 3
) -> None:
    """Show wait time utilization hints periodically.

    Args:
        pr_number: PR number to check for actionable items.
        iteration: Current polling iteration count.
        json_mode: Whether to output in JSON format.
        hint_interval: Show hints every N iterations (default: 3).
    """
    # Only show hints every hint_interval iterations (to avoid spam)
    if iteration % hint_interval != 0 or iteration == 0:
        return

    suggestions = get_wait_time_suggestions(pr_number)
    if not suggestions:
        return

    if json_mode:
        log("待ち時間の活用提案があります", json_mode, {"suggestions": suggestions})
    else:
        print("    💡 待ち時間の有効活用:")
        for suggestion in suggestions:
            print(f"       - {suggestion}")


def check_once(pr_number: str, previous_reviewers: list[str]) -> MonitorEvent | None:
    """Check PR state once and return an event if something notable happened.

    Returns None if no notable event occurred.
    """
    state, error = get_pr_state(pr_number)
    if state is None:
        error_detail = f": {error}" if error else ""
        return create_event(
            EventType.ERROR,
            pr_number,
            f"PR状態の取得に失敗しました{error_detail}",
            suggested_action="再試行するか、GitHub APIの状態を確認してください",
        )

    # Check merge state
    if state.merge_state == MergeState.BEHIND:
        return create_event(
            EventType.BEHIND_DETECTED,
            pr_number,
            "ブランチがmainより古くなっています",
            details={"merge_state": state.merge_state.value},
            suggested_action=f"gh pr update-branch {pr_number} --rebase",
        )

    if state.merge_state == MergeState.DIRTY:
        return create_event(
            EventType.DIRTY_DETECTED,
            pr_number,
            "コンフリクトが検出されました",
            details={"merge_state": state.merge_state.value},
            suggested_action="手動でコンフリクトを解決してください",
        )

    # Check review completion
    had_ai_reviewer = has_copilot_or_codex_reviewer(previous_reviewers)
    has_ai_reviewer_now = has_copilot_or_codex_reviewer(state.pending_reviewers)

    if had_ai_reviewer and not has_ai_reviewer_now:
        # Check if Copilot review ended with an error
        is_error, error_message = is_copilot_review_error(pr_number)
        if is_error:
            return create_event(
                EventType.REVIEW_ERROR,
                pr_number,
                "Copilotレビューがエラーで失敗しました",
                details={
                    "error_message": error_message,
                },
                suggested_action="Copilotレビューを再リクエストしてください",
            )

        comments = get_review_comments(pr_number)
        # Log AI review comments for quality tracking (Issue #610)
        log_review_comments_to_quality_log(pr_number, comments)
        return create_event(
            EventType.REVIEW_COMPLETED,
            pr_number,
            f"AIレビューが完了しました（コメント{len(comments)}件）",
            details={
                "comment_count": len(comments),
                "comments": comments,
            },
            suggested_action="コメントを確認して対応してください",
        )

    # Check CI status
    if state.check_status == CheckStatus.SUCCESS:
        return create_event(
            EventType.CI_PASSED,
            pr_number,
            "全てのCIチェックが成功しました",
            details={
                "checks": [c.get("name", "unknown") for c in state.check_details],
            },
            suggested_action="マージ可能です",
        )

    if state.check_status == CheckStatus.FAILURE:
        failed = [
            c.get("name", "unknown") for c in state.check_details if c.get("state") == "FAILURE"
        ]
        return create_event(
            EventType.CI_FAILED,
            pr_number,
            f"CIが失敗しました: {', '.join(failed)}",
            details={
                "failed_checks": failed,
                "all_checks": [c.get("name", "unknown") for c in state.check_details],
            },
            suggested_action="失敗したテスト/チェックを修正してください",
        )

    if state.check_status == CheckStatus.CANCELLED:
        return create_event(
            EventType.CI_FAILED,
            pr_number,
            "CIがキャンセルされました",
            details={
                "all_checks": [c.get("name", "unknown") for c in state.check_details],
            },
            suggested_action="CIを再実行するか、キャンセル原因を調査してください",
        )

    # No notable event
    return None


def monitor_notify_only(pr_number: str) -> int:
    """Check PR state once and emit an event if something notable happened.

    Designed for use with Claude Code's parallel task spawning.

    Returns:
        0 if an event was emitted, 1 if no notable event
    """
    state, error = get_pr_state(pr_number)
    if state is None:
        error_detail = f": {error}" if error else ""
        event = create_event(
            EventType.ERROR,
            pr_number,
            f"PR状態の取得に失敗しました{error_detail}",
        )
        emit_event(event)
        return 0

    # Note: With empty previous_reviewers, REVIEW_COMPLETED event won't be
    # detected on first call. This is a design limitation - notify-only mode
    # cannot detect review completion without state from a previous check.
    # Use blocking mode for full review tracking, or call notify-only repeatedly.
    event = check_once(pr_number, [])
    if event:
        emit_event(event)
        return 0

    # No notable event - output status
    pending_checks = [
        c.get("name", "unknown")
        for c in state.check_details
        if c.get("state") in ("IN_PROGRESS", "PENDING")
    ]
    log_data = {
        "type": "status",
        "pr_number": pr_number,
        "merge_state": state.merge_state.value,
        "check_status": state.check_status.value,
        "pending_checks": pending_checks,
        "pending_reviewers": state.pending_reviewers,
    }
    print(json.dumps(log_data, ensure_ascii=False), flush=True)
    return 1


def check_self_reference(pr_number: str) -> bool:
    """Check if this PR modifies ci-monitor.py itself.

    When monitoring a PR that changes ci-monitor.py, the running version
    may have bugs that are being fixed, leading to confusing behavior.

    Returns:
        True if ci-monitor.py is in the changed files, False otherwise.
    """
    changed_files = get_pr_changed_files(pr_number)

    if changed_files is None:
        return False

    # Match any path ending with ci-monitor.py (includes my-ci-monitor.py etc.)
    return any(f.endswith("ci-monitor.py") for f in changed_files)


def _monitor_single_pr_for_event(
    pr_number: str,
    interval: int = DEFAULT_POLLING_INTERVAL,
    timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES,
    stop_event: threading.Event | None = None,
) -> MultiPREvent:
    """Monitor a single PR and return when an actionable event occurs.

    This is a simplified monitor for multi-PR mode that returns as soon as
    any actionable event (review completed, CI passed/failed, rebase needed) occurs.

    Args:
        pr_number: PR number to monitor.
        interval: Polling interval in seconds.
        timeout_minutes: Timeout in minutes.
        stop_event: Optional threading.Event to signal early termination.

    Returns:
        MultiPREvent with the first actionable event detected, or None event if stopped.
    """
    start_time = time.time()
    timeout_seconds = timeout_minutes * 60

    # Get initial state for reviewer tracking
    initial_state, _ = get_pr_state(pr_number)
    previous_reviewers = initial_state.pending_reviewers if initial_state else []

    while True:
        # Check if we should stop (another PR already has an event)
        if stop_event is not None and stop_event.is_set():
            stop_state, _ = get_pr_state(pr_number)
            return MultiPREvent(pr_number=pr_number, event=None, state=stop_state)

        elapsed = time.time() - start_time
        if elapsed > timeout_seconds:
            timeout_state, _ = get_pr_state(pr_number)
            return MultiPREvent(
                pr_number=pr_number,
                event=create_event(
                    EventType.TIMEOUT,
                    pr_number,
                    f"{timeout_minutes}分経過でタイムアウトしました",
                ),
                state=timeout_state,
            )

        state, _ = get_pr_state(pr_number)
        if state is None:
            # Use stop_event.wait() instead of time.sleep() to allow early termination
            if stop_event is not None:
                stop_event.wait(interval)
            else:
                time.sleep(interval)
            continue

        # Check for actionable events
        event = check_once(pr_number, previous_reviewers)
        if event:
            return MultiPREvent(pr_number=pr_number, event=event, state=state)

        # Update previous reviewers for next iteration
        previous_reviewers = state.pending_reviewers

        # Use stop_event.wait() instead of time.sleep() to allow early termination
        if stop_event is not None:
            stop_event.wait(interval)
        else:
            time.sleep(interval)

    # This line is intentionally unreachable - it serves as a safety net
    # for type checkers and to catch logic errors if the loop ever exits unexpectedly
    raise AssertionError("Unexpected exit from monitoring loop")  # pragma: no cover


def monitor_multiple_prs(
    pr_numbers: list[str],
    interval: int = DEFAULT_POLLING_INTERVAL,
    timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES,
    json_mode: bool = False,
) -> list[MultiPREvent]:
    """Monitor multiple PRs in parallel and return on first actionable event.

    Uses ThreadPoolExecutor to monitor multiple PRs concurrently.
    Returns immediately when any PR has an actionable event, signaling
    other monitors to stop via threading.Event.

    Args:
        pr_numbers: List of PR numbers to monitor.
        interval: Polling interval in seconds.
        timeout_minutes: Timeout in minutes for each PR.
        json_mode: Output in JSON format.

    Returns:
        List of MultiPREvent containing events detected before returning.
    """
    if not pr_numbers:
        return []

    log(f"PRの並列監視を開始: {', '.join(pr_numbers)}", json_mode)
    log(f"ポーリング間隔: {interval}秒, タイムアウト: {timeout_minutes}分/PR", json_mode)

    events: list[MultiPREvent] = []
    stop_event = threading.Event()

    with ThreadPoolExecutor(max_workers=len(pr_numbers)) as executor:
        # Submit all PR monitors with shared stop_event
        future_to_pr = {
            executor.submit(
                _monitor_single_pr_for_event, pr_number, interval, timeout_minutes, stop_event
            ): pr_number
            for pr_number in pr_numbers
        }

        # Return as soon as any PR has an actionable event
        for future in as_completed(future_to_pr):
            pr_number = future_to_pr[future]
            try:
                result = future.result()
                # Skip results from stopped monitors (no event)
                if result.event is None and stop_event.is_set():
                    continue
                events.append(result)
                if result.event:
                    log(
                        f"PR #{pr_number}: {result.event.event_type.value} - {result.event.message}",
                        json_mode,
                    )
                    # Signal other monitors to stop
                    stop_event.set()
                    return events
            except Exception as e:
                events.append(
                    MultiPREvent(
                        pr_number=pr_number,
                        event=create_event(
                            EventType.ERROR,
                            pr_number,
                            f"監視エラー: {str(e)}",
                        ),
                        state=None,
                    )
                )
                log(f"PR #{pr_number}: エラー - {str(e)}", json_mode)
                # Signal other monitors to stop
                stop_event.set()
                return events

        return events
