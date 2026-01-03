"""Main monitoring loop for ci-monitor.

This module contains the monitor_pr function which is the main monitoring loop.
Extracted from ci-monitor.py as part of Issue #2624 refactoring.
"""

from __future__ import annotations

import time
from typing import Any

from ci_monitor.ai_review import (
    check_and_report_contradictions,
    has_copilot_or_codex_reviewer,
    is_copilot_review_error,
    request_copilot_review,
)
from ci_monitor.constants import (
    ASYNC_REVIEWER_CHECK_DELAY_SECONDS,
    COPILOT_REVIEWER_LOGIN,
    DEFAULT_COPILOT_PENDING_TIMEOUT,
    DEFAULT_LOCAL_CHANGES_MAX_WAIT,
    DEFAULT_LOCAL_CHANGES_WAIT_INTERVAL,
    DEFAULT_MAX_COPILOT_RETRY,
    DEFAULT_MAX_PR_RECREATE,
    DEFAULT_MAX_REBASE,
    DEFAULT_MAX_RETRY_WAIT_POLLS,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_TIMEOUT_MINUTES,
)
from ci_monitor.events import log
from ci_monitor.models import (
    CheckStatus,
    IntervalDirection,
    MergeState,
    MonitorResult,
    RateLimitEventType,
    RetryWaitStatus,
)
from ci_monitor.monitor import log_ci_monitor_event, show_wait_time_hint
from ci_monitor.pr_operations import (
    get_pr_branch_name,
    get_pr_state,
    has_ai_review_pending,
    has_local_changes,
    is_codex_review_pending,
    rebase_pr,
    recreate_pr,
    sync_local_after_rebase,
    wait_for_main_stable,
)
from ci_monitor.rate_limit import (
    check_rate_limit,
    get_adjusted_interval,
    log_rate_limit_event,
    log_rate_limit_warning_to_console,
)
from ci_monitor.review_comments import (
    auto_resolve_duplicate_threads,
    classify_review_comments,
    filter_duplicate_comments,
    get_pr_changed_files,
    get_resolved_thread_hashes,
    get_review_comments,
    get_unresolved_ai_threads,
    get_unresolved_threads,
    log_review_comments_to_quality_log,
)
from ci_monitor.state import save_monitor_state


def log_rate_limit_warning(
    remaining: int,
    limit: int,
    reset_timestamp: int,
    json_mode: bool = False,
) -> None:
    """Wrapper for log_rate_limit_warning_to_console with proper callbacks.

    Args:
        remaining: Remaining API calls.
        limit: Total API call limit.
        reset_timestamp: Unix timestamp when the limit resets.
        json_mode: If True, output structured JSON instead of plain text.
    """
    log_rate_limit_warning_to_console(
        remaining,
        limit,
        reset_timestamp,
        json_mode,
        log_fn=log,
        log_event_fn=log_rate_limit_event,
    )


def monitor_pr(
    pr_number: str,
    timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES,
    early_exit: bool = False,
) -> MonitorResult:
    """
    Monitor a PR until CI completes (and optionally review completes) or timeout.

    Args:
        pr_number: PR number to monitor.
        timeout_minutes: Timeout in minutes.
        early_exit: If True, exit immediately when review comments are detected (without waiting for CI).
            Note: CI failures always exit immediately regardless of this flag.
            Note: This only applies to AI reviewer comments (Copilot/Codex). Manual review
            comments are not detected until CI completes.

    Note:
        Issue #2454: The following parameters are now hardcoded to default values:
        - interval: DEFAULT_POLLING_INTERVAL (30s)
        - max_rebase: DEFAULT_MAX_REBASE (3)
        - json_mode: True (always JSON output)
        - wait_review: True (always wait for AI review)
        - resolve_before_rebase: False
        - wait_stable: True

        After a rebase, Copilot/Codex may be re-requested as reviewers asynchronously.
        This function automatically waits for such re-reviews to complete before
        returning success.

        Timing limitation: The async reviewer re-request detection waits
        ASYNC_REVIEWER_CHECK_DELAY_SECONDS (currently 5 seconds) after CI passes
        to check for re-requests. If the re-request takes longer than this delay
        to be triggered, the function may return before detecting it. In this case,
        the merge-check hook will block the merge until the review completes.
    """
    # Issue #2454: Hardcode removed parameters to their default values
    interval = DEFAULT_POLLING_INTERVAL
    max_rebase = DEFAULT_MAX_REBASE
    json_mode = True  # Always JSON output
    wait_review = True  # Always wait for AI review
    resolve_before_rebase = False
    wait_stable = True

    start_time = time.time()
    timeout_seconds = timeout_minutes * 60
    rebase_count = 0
    local_changes_wait_count = 0  # Track wait cycles for local changes (Issue #1307)
    copilot_retry_count = 0  # Track Copilot review retry attempts (Issue #1305)
    copilot_retry_in_progress = False  # Track if waiting for new review after retry (Issue #1343)
    copilot_retry_wait_polls = 0  # Track poll cycles during retry wait (Issue #1343)
    review_notified = False
    rebase_review_checked = False  # Track if we've checked for AI reviewer re-requests after rebase
    pre_rebase_hashes: set[str] = set()  # Track resolved thread hashes before rebase (Issue #839)
    copilot_pending_since: float | None = (
        None  # Track when Copilot first became pending (Issue #1532)
    )
    pr_recreate_count = 0  # Track PR recreation attempts (Issue #1532)

    # Issue #1351: Helper functions to avoid code duplication in retry logic
    def _handle_copilot_retry_wait() -> RetryWaitStatus:
        """
        Handle polling during Copilot retry wait period.

        Returns:
            RetryWaitStatus.CONTINUE: Continue waiting for new review
            RetryWaitStatus.TIMEOUT: Retry wait timed out, proceed with next retry attempt
        """
        nonlocal copilot_retry_in_progress, copilot_retry_wait_polls
        copilot_retry_wait_polls += 1
        if copilot_retry_wait_polls < DEFAULT_MAX_RETRY_WAIT_POLLS:
            log("Waiting for Copilot to start new review...", json_mode)
            time.sleep(adjusted_interval)
            return RetryWaitStatus.CONTINUE
        else:
            log(
                f"Retry wait timeout ({copilot_retry_wait_polls} polls), considering retry failed",
                json_mode,
            )
            copilot_retry_in_progress = False
            copilot_retry_wait_polls = 0
            return RetryWaitStatus.TIMEOUT

    def _execute_copilot_retry(error_message: str) -> bool:
        """
        Execute Copilot review retry and update state.

        Args:
            error_message: The error message from the failed review

        Returns:
            True if retry was requested (caller should continue to next iteration)
            False if max retries reached (caller should handle failure)
        """
        nonlocal copilot_retry_count, copilot_retry_in_progress, copilot_retry_wait_polls
        nonlocal previous_reviewers, was_codex_pending

        copilot_retry_count += 1
        if copilot_retry_count <= DEFAULT_MAX_COPILOT_RETRY:
            log(
                f"Copilot review error detected, retrying ({copilot_retry_count}/{DEFAULT_MAX_COPILOT_RETRY})...",
                json_mode,
                {"error_message": error_message, "retry_count": copilot_retry_count}
                if json_mode
                else None,
            )
            success, error_msg = request_copilot_review(pr_number)
            if success:
                log("Copilot review re-requested, waiting...", json_mode)
                copilot_retry_in_progress = True
                copilot_retry_wait_polls = 0
            else:
                log(
                    f"Failed to re-request Copilot review: {error_msg}",
                    json_mode,
                )
            # Set previous_reviewers to include the re-requested Copilot reviewer
            previous_reviewers = [COPILOT_REVIEWER_LOGIN]
            was_codex_pending = False  # We're only retrying Copilot, not Codex
            return True  # Retry requested, caller should continue
        return False  # Max retries reached

    # Issue #1423: Helper function to save completed state with consistent fields
    def _finalize_monitoring(
        success: bool,
        message: str,
        *,
        unresolved_threads: int | None = None,
        review_completed: bool | None = None,
    ) -> None:
        """Save completed monitoring state with consistent fields.

        Args:
            success: Whether monitoring completed successfully
            message: Result message
            unresolved_threads: Number of unresolved review threads (optional)
            review_completed: Whether AI review was completed (optional)
        """
        state_data: dict[str, Any] = {
            "status": "completed",
            "success": success,
            "message": message,
            "rebase_count": rebase_count,
            "elapsed_seconds": int(time.time() - start_time),
        }
        if unresolved_threads is not None:
            state_data["unresolved_threads"] = unresolved_threads
        if review_completed is not None:
            state_data["review_completed"] = review_completed
        save_monitor_state(pr_number, state_data)

    # Issue #1423: Helper function to format Copilot error message
    def _format_copilot_error(error_message: str | None) -> str:
        """Format Copilot review error message consistently."""
        truncated = error_message[:100] if error_message else "Unknown error"
        return f"Copilot review failed after {DEFAULT_MAX_COPILOT_RETRY} retries: {truncated}"

    log(f"Starting CI monitor for PR #{pr_number}", json_mode)
    log(
        f"Interval: {interval}s, Timeout: {timeout_minutes}min, Max rebase: {max_rebase}",
        json_mode,
    )

    # Issue #1241: Log monitor start
    log_ci_monitor_event(
        pr_number=pr_number,
        action="monitor_start",
        result="started",
        details={
            "interval": interval,
            "timeout_minutes": timeout_minutes,
            "max_rebase": max_rebase,
            "wait_review": wait_review,
        },
    )

    # Get initial state for reviewer tracking
    initial_state, _ = get_pr_state(pr_number)
    previous_reviewers = initial_state.pending_reviewers if initial_state else []
    was_codex_pending = is_codex_review_pending(pr_number)  # Track Codex Cloud review state

    # Issue #2454: wait_review is always True, removed effective_wait_review logic

    # Track polling iterations for periodic hints
    poll_iteration = 0

    # Rate limit monitoring (Issue #896)
    adjusted_interval = interval
    last_rate_limit_check = 0
    # Issue #1347: Increased from 5 to 10 to reduce API calls by 50%
    # Combined with 60s cache, this significantly reduces rate limit API usage
    rate_limit_check_frequency = 10  # Check every N iterations

    # Initial rate limit check
    rate_remaining, rate_limit, reset_ts = check_rate_limit()
    # Use rate_limit > 0 to check for successful API response (0, 0, 0 indicates failure)
    if rate_limit > 0:
        log_rate_limit_warning(rate_remaining, rate_limit, reset_ts, json_mode)
        adjusted_interval = get_adjusted_interval(interval, rate_remaining)
        if adjusted_interval != interval:
            # Calculate direction consistently (same as periodic check)
            direction = (
                IntervalDirection.DECREASE
                if adjusted_interval < interval
                else IntervalDirection.INCREASE
            )
            log(f"Adjusted polling interval to {adjusted_interval}s due to rate limit", json_mode)
            # Log interval adjustment to hook-execution.log (Issue #1244, #1385)
            log_rate_limit_event(
                RateLimitEventType.ADJUSTED_INTERVAL,
                rate_remaining,
                rate_limit,
                reset_ts,
                {
                    "old_interval": interval,
                    "new_interval": adjusted_interval,
                    "direction": direction,
                },
            )

    while True:
        elapsed = time.time() - start_time

        # Issue #1311: Save state for background execution monitoring
        # Issue #1373: Include rate limit info
        state_dict: dict[str, Any] = {
            "status": "monitoring",
            "rebase_count": rebase_count,
            "elapsed_seconds": int(elapsed),
            "timeout_seconds": timeout_seconds,
            "poll_iteration": poll_iteration,
        }
        if rate_limit > 0:
            state_dict["rate_limit"] = {
                "remaining": rate_remaining,
                "limit": rate_limit,
                "reset_at": reset_ts,
            }
        save_monitor_state(pr_number, state_dict)

        if elapsed > timeout_seconds:
            # Build timeout message with guidance
            timeout_msg = f"Timeout after {timeout_minutes} minutes"
            guidance_parts = []

            # Check what we were waiting for
            current_state, _ = get_pr_state(pr_number)
            if current_state:
                if current_state.check_status == CheckStatus.PENDING:
                    guidance_parts.append("CI still pending")
                if has_ai_review_pending(pr_number, current_state.pending_reviewers):
                    guidance_parts.append("AI review still pending")

            if guidance_parts:
                timeout_msg += f" ({', '.join(guidance_parts)})"

            # Issue #1241: Log timeout
            log_ci_monitor_event(
                pr_number=pr_number,
                action="monitor_complete",
                result="timeout",
                details={
                    "elapsed_seconds": int(elapsed),
                    "timeout_minutes": timeout_minutes,
                    "poll_iterations": poll_iteration,
                    "rebase_count": rebase_count,
                    "guidance": guidance_parts,
                },
            )

            # Issue #1311: Save final state for --result retrieval
            _finalize_monitoring(False, timeout_msg)
            return MonitorResult(
                success=False,
                message=timeout_msg,
                rebase_count=rebase_count,
                final_state=current_state,
            )

        state, error = get_pr_state(pr_number)
        if state is None:
            error_detail = f": {error}" if error else ""
            log(f"Failed to fetch PR state{error_detail}, retrying...", json_mode)
            time.sleep(adjusted_interval)
            continue

        # Check merge state
        if state.merge_state == MergeState.BEHIND:
            if rebase_count >= max_rebase:
                # Issue #1239: Wait for main to stabilize before giving up
                if wait_stable:
                    log(
                        f"Max rebase attempts ({max_rebase}) reached, waiting for main to stabilize...",
                        json_mode,
                    )
                    # Calculate remaining timeout for stability wait
                    elapsed = time.time() - start_time
                    remaining_timeout = max(1, int((timeout_seconds - elapsed) / 60))

                    if wait_for_main_stable(
                        timeout_minutes=remaining_timeout,
                        json_mode=json_mode,
                    ):
                        # Main is stable, reset rebase counter and continue
                        log(
                            "Main stabilized, resetting rebase counter and continuing",
                            json_mode,
                        )
                        rebase_count = 0
                        # Don't continue here - fall through to check local changes
                        # and perform the rebase
                    else:
                        # Timeout waiting for stability
                        max_rebase_stable_msg = (
                            f"Max rebase attempts ({max_rebase}) reached and main did not stabilize"
                        )
                        # Issue #1311: Save final state for --result retrieval
                        _finalize_monitoring(False, max_rebase_stable_msg)
                        return MonitorResult(
                            success=False,
                            message=max_rebase_stable_msg,
                            rebase_count=rebase_count,
                            final_state=state,
                        )
                else:
                    max_rebase_msg = f"Max rebase attempts ({max_rebase}) reached"
                    # Issue #1311: Save final state for --result retrieval
                    _finalize_monitoring(False, max_rebase_msg)
                    return MonitorResult(
                        success=False,
                        message=max_rebase_msg,
                        rebase_count=rebase_count,
                        final_state=state,
                    )

            # Check for local changes before rebasing (Issue #865, Issue #1307)
            has_changes, change_description = has_local_changes()
            if has_changes:
                local_changes_wait_count += 1
                if local_changes_wait_count > DEFAULT_LOCAL_CHANGES_MAX_WAIT:
                    # Max wait exceeded, fail
                    log(
                        f"BEHIND detected, but max wait for local changes ({DEFAULT_LOCAL_CHANGES_MAX_WAIT}) exceeded: {change_description}",
                        json_mode,
                    )
                    # Issue #1311: Save final state for --result retrieval
                    local_changes_msg = f"Rebase skipped due to local changes (after {DEFAULT_LOCAL_CHANGES_MAX_WAIT} wait cycles): {change_description}"
                    _finalize_monitoring(False, local_changes_msg)
                    return MonitorResult(
                        success=False,
                        message=local_changes_msg,
                        rebase_count=rebase_count,
                        final_state=state,
                    )

                # Wait for local changes to be resolved (Issue #1307)
                remaining = DEFAULT_LOCAL_CHANGES_MAX_WAIT - local_changes_wait_count
                log(
                    f"BEHIND detected, waiting for local changes to be resolved ({local_changes_wait_count}/{DEFAULT_LOCAL_CHANGES_MAX_WAIT}): {change_description}",
                    json_mode,
                )
                # Honor rate-limit backoff when waiting (Codex review feedback)
                wait_interval = max(DEFAULT_LOCAL_CHANGES_WAIT_INTERVAL, adjusted_interval)
                time.sleep(wait_interval)
                poll_iteration += 1  # Increment to maintain hint/rate-limit check schedule
                continue
            else:
                # Local changes resolved, reset wait counter (Issue #1307)
                if local_changes_wait_count > 0:
                    log(
                        f"Local changes resolved after {local_changes_wait_count} wait cycles",
                        json_mode,
                    )
                    local_changes_wait_count = 0

            # Check for unresolved AI review threads before rebasing (Issue #989)
            if resolve_before_rebase:
                ai_threads = get_unresolved_ai_threads(pr_number)
                # Issue #1195: Treat API failure (None) as "unknown" - don't proceed with rebase
                if ai_threads is None:
                    log(
                        "BEHIND detected, but cannot verify AI thread status (API error) - waiting",
                        json_mode,
                    )
                    time.sleep(adjusted_interval)
                    continue
                if ai_threads:
                    log(
                        f"BEHIND detected, but {len(ai_threads)} unresolved AI review thread(s) - waiting for resolution",
                        json_mode,
                    )
                    time.sleep(adjusted_interval)
                    continue

            log(
                f"BEHIND detected, attempting rebase ({rebase_count + 1}/{max_rebase})...",
                json_mode,
            )
            # Capture resolved thread hashes before rebase for duplicate detection (Issue #839)
            pre_rebase_hashes = get_resolved_thread_hashes(pr_number)
            if pre_rebase_hashes:
                log(
                    f"Captured {len(pre_rebase_hashes)} resolved thread hashes for duplicate detection",
                    json_mode,
                )
            # Issue #1341: Capture file count before rebase for mixed change detection
            files_before = get_pr_changed_files(pr_number)
            files_before_count = len(files_before) if files_before is not None else -1

            # Issue #1348: Enhanced rebase logging
            rebase_result = rebase_pr(pr_number)
            if rebase_result.success:
                rebase_count += 1
                rebase_review_checked = False  # Reset for new rebase
                log("Rebase successful, waiting for new CI to start...", json_mode)

                # Issue #1341: Check for mixed changes after rebase
                files_after = get_pr_changed_files(pr_number)
                files_after_count = len(files_after) if files_after is not None else -1

                # Issue #1348: Calculate file changes for logging
                # Use explicit None checks to distinguish API failure from empty set
                files_before_list = sorted(files_before)[:20] if files_before is not None else []
                files_after_list = sorted(files_after)[:20] if files_after is not None else []
                added_files = (
                    sorted(files_after - files_before)[:10]
                    if files_before is not None and files_after is not None
                    else []
                )
                removed_files = (
                    sorted(files_before - files_after)[:10]
                    if files_before is not None and files_after is not None
                    else []
                )

                if files_before_count > 0 and files_after_count > files_before_count:
                    log(
                        f"⚠️  Warning: Changed files increased after rebase "
                        f"({files_before_count} → {files_after_count}). "
                        "Possible unintended changes mixed in.",
                        json_mode,
                    )
                    log_ci_monitor_event(
                        pr_number=pr_number,
                        action="rebase_file_increase",
                        result="warning",
                        details={
                            "before": files_before_count,
                            "after": files_after_count,
                            "diff": files_after_count - files_before_count,
                            "added_files": added_files,
                        },
                    )
                # Issue #1241, #1348: Log rebase success with file details
                log_ci_monitor_event(
                    pr_number=pr_number,
                    action="rebase",
                    result="success",
                    details={
                        "attempt": rebase_count,
                        "max_rebase": max_rebase,
                        "files_before_count": files_before_count,
                        "files_after_count": files_after_count,
                        "files_before": files_before_list,
                        "files_after": files_after_list,
                        "added_files": added_files,
                        "removed_files": removed_files,
                    },
                )
                # 2回以上のリベースで警告（並行作業が多い可能性）
                if rebase_count >= 2:
                    log(
                        f"⚠️ {rebase_count}回目のリベースが必要でした（並行作業が多い可能性。merge queue検討を推奨）",
                        json_mode,
                    )
                # Sync local branch after remote rebase (Issue #895)
                branch_name = get_pr_branch_name(pr_number)
                if branch_name:
                    sync_success = sync_local_after_rebase(branch_name, json_mode)
                    if not sync_success:
                        log(
                            "Local sync failed (uncommitted changes?). Manual sync may be needed.",
                            json_mode,
                        )
                time.sleep(10)
                start_time = time.time()
                continue
            else:
                # Issue #1348: Log rebase failure with conflict info
                if rebase_result.conflict:
                    log("Rebase failed: conflict detected", json_mode)
                else:
                    log("Rebase failed", json_mode)
                # Issue #1241, #1348: Log rebase failure with conflict details
                log_ci_monitor_event(
                    pr_number=pr_number,
                    action="rebase",
                    result="failure",
                    details={
                        "attempt": rebase_count + 1,
                        "max_rebase": max_rebase,
                        "conflict": rebase_result.conflict,
                        "error_message": rebase_result.error_message[:200]
                        if rebase_result.error_message
                        else None,
                    },
                )
                # Issue #1311: Save final state for --result retrieval
                _finalize_monitoring(False, "Rebase failed")
                return MonitorResult(
                    success=False,
                    message="Rebase failed",
                    rebase_count=rebase_count,
                    final_state=state,
                )

        elif state.merge_state == MergeState.DIRTY:
            # Issue #1311: Save final state for --result retrieval
            dirty_msg = "Conflict detected (DIRTY). Manual resolution required."
            _finalize_monitoring(False, dirty_msg)
            return MonitorResult(
                success=False,
                message=dirty_msg,
                rebase_count=rebase_count,
                final_state=state,
            )

        # Check review completion (for both Copilot and Codex Cloud)
        current_ai_pending = has_ai_review_pending(pr_number, state.pending_reviewers)

        # Issue #1343: Reset retry-in-progress flag when new pending reviewer appears
        if copilot_retry_in_progress and current_ai_pending:
            copilot_retry_in_progress = False
            copilot_retry_wait_polls = 0
            log("New AI reviewer detected, retry wait complete", json_mode)

        # Issue #1532: Track Copilot pending state and check for timeout
        if current_ai_pending and has_copilot_or_codex_reviewer(state.pending_reviewers):
            if copilot_pending_since is None:
                copilot_pending_since = time.time()
                log("Copilot pending timer started", json_mode)
            else:
                pending_duration = time.time() - copilot_pending_since
                if (
                    pending_duration > DEFAULT_COPILOT_PENDING_TIMEOUT
                    and pr_recreate_count < DEFAULT_MAX_PR_RECREATE
                ):
                    # Copilot has been pending too long, try recreating PR
                    log(
                        f"Copilot pending timeout ({pending_duration:.0f}s > {DEFAULT_COPILOT_PENDING_TIMEOUT}s), "
                        f"recreating PR...",
                        json_mode,
                    )
                    success, new_pr_number, message = recreate_pr(pr_number)
                    pr_recreate_count += 1
                    copilot_pending_since = None  # Reset timer after recreation attempt
                    if success:
                        log(message, json_mode)
                        # Finalize monitoring before returning (success=False because original PR monitoring ended)
                        if new_pr_number:
                            recreate_msg = f"PR再作成完了: 新PR #{new_pr_number} を監視してください"
                            details = {"recreated_pr": new_pr_number, "original_pr": pr_number}
                        else:
                            # PR created but couldn't extract number from URL
                            recreate_msg = "PR再作成完了: 新しいPRのURLを確認してください"
                            details = {"original_pr": pr_number}
                        _finalize_monitoring(False, recreate_msg)
                        return MonitorResult(
                            success=False,
                            message=recreate_msg,
                            rebase_count=rebase_count,
                            final_state=state,
                            review_completed=False,
                            ci_passed=state.check_status == CheckStatus.SUCCESS,
                            details=details,
                        )
                    else:
                        log(f"PR再作成に失敗しました: {message}", json_mode)
                        # Continue monitoring original PR
                        # Note: Timer was already reset above. pr_recreate_count has been
                        # incremented so no more recreations will be attempted, but we continue
                        # monitoring in case Copilot eventually completes.
        else:
            # Reset pending timer when Copilot is no longer pending
            if copilot_pending_since is not None:
                copilot_pending_since = None

        if not review_notified and not current_ai_pending:
            # Check if we previously had AI reviewers pending
            # Note: For Codex Cloud, the check is done via is_codex_review_pending()
            # which is called inside has_ai_review_pending()
            if has_copilot_or_codex_reviewer(previous_reviewers) or was_codex_pending:
                # Check if Copilot review ended with an error
                is_error, error_message = is_copilot_review_error(pr_number)
                if is_error:
                    # Issue #1351: Use helper functions to avoid code duplication
                    if copilot_retry_in_progress:
                        if _handle_copilot_retry_wait() == RetryWaitStatus.CONTINUE:
                            continue

                    # Issue #1305: Automatic retry on Copilot review error
                    if _execute_copilot_retry(error_message):
                        time.sleep(adjusted_interval)
                        continue
                    # Max retries reached, fall through to failure handling
                    log(
                        "Copilot review failed with error!",
                        json_mode,
                        {"error_message": error_message} if json_mode else None,
                    )
                    # Issue #1311: Save final state for --result retrieval
                    copilot_error_msg = _format_copilot_error(error_message)
                    _finalize_monitoring(False, copilot_error_msg)
                    return MonitorResult(
                        success=False,
                        message=copilot_error_msg,
                        rebase_count=rebase_count,
                        final_state=state,
                        review_completed=False,
                        ci_passed=state.check_status == CheckStatus.SUCCESS,
                    )

                review_notified = True
                copilot_retry_count = 0  # Reset retry counter on successful review completion
                copilot_retry_in_progress = False  # Issue #1343: Reset retry-in-progress flag
                copilot_retry_wait_polls = 0  # Issue #1343: Reset poll counter

                # Auto-resolve duplicate threads after rebase (Issue #839)
                # This must be done before getting comments to avoid reporting already-resolved duplicates
                resolved_duplicate_hashes: set[str] = set()
                if pre_rebase_hashes:
                    resolved_count, resolved_duplicate_hashes = auto_resolve_duplicate_threads(
                        pr_number, pre_rebase_hashes, json_mode
                    )
                    if resolved_count > 0:
                        log(
                            f"Auto-resolved {resolved_count} duplicate thread(s) after rebase",
                            json_mode,
                            {"resolved_count": resolved_count} if json_mode else None,
                        )
                    # Clear hashes after use to avoid re-processing
                    pre_rebase_hashes = set()

                # Issue #1399: Save previous comments for contradiction detection
                previous_comments = state.review_comments

                comments = get_review_comments(pr_number)
                # Issue #1097: Filter out comments from auto-resolved duplicate threads
                comments = filter_duplicate_comments(comments, resolved_duplicate_hashes)

                # Issue #1399/#1596/#1597: Detect potential contradictions
                check_and_report_contradictions(comments, previous_comments, json_mode)

                state.review_comments = comments

                # Log AI review comments for quality tracking (Issue #610)
                log_review_comments_to_quality_log(pr_number, comments)

                # Classify comments by PR scope
                classified = classify_review_comments(pr_number, comments)

                log(
                    "Review completed!",
                    json_mode,
                    {
                        "review_comments": comments,
                        "requires_action": bool(comments),
                        "comment_count": len(comments),
                        "in_scope_count": len(classified.in_scope),
                        "out_of_scope_count": len(classified.out_of_scope),
                    }
                    if json_mode
                    else None,
                )

                # Early exit: return immediately when review comments are detected
                # Only early exit if CI hasn't failed/cancelled - failures should go through normal handling
                if (
                    early_exit
                    and comments
                    and state.check_status not in (CheckStatus.FAILURE, CheckStatus.CANCELLED)
                ):
                    log(
                        "Early exit: Review comments detected, exiting for immediate action",
                        json_mode,
                    )
                    early_exit_msg = f"Review comments detected ({len(comments)} comments) - early exit for shift-left"
                    # Issue #1311: Save final state for --result retrieval
                    _finalize_monitoring(True, early_exit_msg, review_completed=True)
                    return MonitorResult(
                        success=True,  # Not a failure, just early notification
                        message=early_exit_msg,
                        rebase_count=rebase_count,
                        final_state=state,
                        review_completed=True,
                        ci_passed=state.check_status == CheckStatus.SUCCESS,
                    )

        # Update previous reviewers and Codex pending state for next iteration
        previous_reviewers = state.pending_reviewers
        was_codex_pending = is_codex_review_pending(pr_number)

        # Check CI status
        if state.check_status == CheckStatus.SUCCESS:
            log("CI passed!", json_mode)
            # Issue #1241: Log CI success
            elapsed_time = int(time.time() - start_time)
            log_ci_monitor_event(
                pr_number=pr_number,
                action="ci_state_change",
                result="success",
                details={
                    "elapsed_seconds": elapsed_time,
                    "poll_iterations": poll_iteration,
                    "rebase_count": rebase_count,
                },
            )

            # After CI passes (especially after rebase), Copilot may be re-requested
            # as a reviewer asynchronously. Wait briefly and re-check.
            # Only check once per rebase to avoid repeating the same message.
            if rebase_count > 0 and not rebase_review_checked:
                log("Checking for async AI reviewer re-requests after rebase...", json_mode)
                time.sleep(ASYNC_REVIEWER_CHECK_DELAY_SECONDS)
                refreshed_state, refresh_error = get_pr_state(pr_number)
                if refreshed_state is None:
                    error_detail = f": {refresh_error}" if refresh_error else ""
                    log(f"Failed to refresh PR state{error_detail}, retrying...", json_mode)
                    # Don't mark as checked - retry on next iteration
                    time.sleep(adjusted_interval)
                    continue
                state = refreshed_state
                # Re-check merge state and CI status after refresh (main branch may have advanced)
                if state.merge_state in (MergeState.BEHIND, MergeState.DIRTY):
                    log(
                        f"Merge state changed to {state.merge_state.value} after refresh, restarting loop...",
                        json_mode,
                    )
                    # Don't mark as checked - need to recheck after handling BEHIND/DIRTY
                    continue
                if state.check_status != CheckStatus.SUCCESS:
                    log(
                        f"CI status changed to {state.check_status.value} after refresh, restarting loop...",
                        json_mode,
                    )
                    # Don't mark as checked - need to recheck when CI passes again
                    continue
                # Only mark as checked after successful refresh with CI still passing
                rebase_review_checked = True
                if has_ai_review_pending(pr_number, state.pending_reviewers):
                    log("AI reviewer re-requested after rebase, waiting for review...", json_mode)
                    # Reset review tracking for the new re-review
                    previous_reviewers = state.pending_reviewers
                    was_codex_pending = is_codex_review_pending(pr_number)
                    review_notified = False
                    copilot_retry_count = 0  # Reset retry counter for new review cycle
                    copilot_retry_in_progress = False  # Issue #1343: Reset retry-in-progress flag
                    copilot_retry_wait_polls = 0  # Issue #1343: Reset poll counter
                    time.sleep(adjusted_interval)
                    continue

            # If review is not yet completed, continue waiting
            if not review_notified:
                # Check if there are still AI reviewers pending (Copilot or Codex Cloud)
                if has_ai_review_pending(pr_number, state.pending_reviewers):
                    # Issue #1343: Reset retry-in-progress flag when new pending reviewer appears
                    if copilot_retry_in_progress:
                        copilot_retry_in_progress = False
                        copilot_retry_wait_polls = 0
                        log("New AI reviewer detected, retry wait complete", json_mode)
                    log("Waiting for AI review to complete...", json_mode)
                    time.sleep(adjusted_interval)
                    continue
                else:
                    # No AI reviewers pending but review_notified is False
                    # This means AI review was never requested or already completed before monitoring started
                    # Check for error reviews before proceeding (Issue #339/#342)
                    is_error, error_message = is_copilot_review_error(pr_number)
                    if is_error:
                        # Issue #1351: Use helper functions to avoid code duplication
                        if copilot_retry_in_progress:
                            if _handle_copilot_retry_wait() == RetryWaitStatus.CONTINUE:
                                continue

                        # Issue #1305: Automatic retry on Copilot review error
                        if _execute_copilot_retry(error_message):
                            time.sleep(adjusted_interval)
                            continue
                        # Max retries reached, fall through to failure handling
                        log(
                            "Copilot review failed with error!",
                            json_mode,
                            {"error_message": error_message} if json_mode else None,
                        )
                        # Issue #1311: Save final state for --result retrieval
                        copilot_error_msg = _format_copilot_error(error_message)
                        _finalize_monitoring(False, copilot_error_msg)
                        return MonitorResult(
                            success=False,
                            message=copilot_error_msg,
                            rebase_count=rebase_count,
                            final_state=state,
                            review_completed=False,
                            ci_passed=state.check_status == CheckStatus.SUCCESS,
                        )
                    log("No pending AI reviewers detected, proceeding...", json_mode)

            # Check for unresolved review threads
            unresolved = get_unresolved_threads(pr_number)
            # Issue #1195: Distinguish API failure (None) from empty result ([])
            thread_api_failed = unresolved is None
            if thread_api_failed:
                log(
                    "Warning: Failed to fetch review threads (GraphQL API error)",
                    json_mode,
                )
                unresolved = []  # Fallback to empty list for downstream code
            if unresolved:
                state.unresolved_threads = unresolved
                log(
                    f"Warning: {len(unresolved)} unresolved review threads detected",
                    json_mode,
                )

            # Always fetch comments if not yet fetched (for cases where monitoring started after review)
            if not state.review_comments:
                comments = get_review_comments(pr_number)
                if comments:
                    state.review_comments = comments

            # Build result message with review comment info
            # Issue #1013: Count only unresolved threads, not all review comments
            # state.review_comments includes all review comments (including those in resolved threads), so use len(unresolved) instead
            # Issue #1195: Include API failure warning in result message
            unresolved_count = len(unresolved) if unresolved else 0
            message_parts = ["CI passed"]
            if review_notified:
                message_parts.append("and review completed")
            if thread_api_failed:
                message_parts.append("(thread status unknown - API error)")
            elif unresolved_count > 0:
                message_parts.append(f"({unresolved_count} unresolved thread(s) to address)")

            # Issue #1241: Log successful monitor completion
            log_ci_monitor_event(
                pr_number=pr_number,
                action="monitor_complete",
                result="success",
                details={
                    "total_wait_seconds": int(elapsed),
                    "poll_iterations": poll_iteration,
                    "rebase_count": rebase_count,
                    "review_completed": review_notified,
                    "unresolved_threads": unresolved_count,
                },
            )

            # Issue #1311: Save final state for --result retrieval
            final_message = " ".join(message_parts)
            _finalize_monitoring(True, final_message, unresolved_threads=unresolved_count)
            return MonitorResult(
                success=True,
                message=final_message,
                rebase_count=rebase_count,
                final_state=state,
                review_completed=review_notified,
                ci_passed=True,
            )

        elif state.check_status == CheckStatus.FAILURE:
            failed_checks = [
                c.get("name", "unknown") for c in state.check_details if c.get("state") == "FAILURE"
            ]
            failure_message = f"CI failed: {', '.join(failed_checks)}"
            log(failure_message, json_mode)
            # Issue #1241: Log CI failure
            elapsed_time = int(time.time() - start_time)
            log_ci_monitor_event(
                pr_number=pr_number,
                action="ci_state_change",
                result="failure",
                details={
                    "elapsed_seconds": elapsed_time,
                    "poll_iterations": poll_iteration,
                    "rebase_count": rebase_count,
                    "failed_checks": failed_checks,
                },
            )
            # Issue #1311: Save final state for --result retrieval
            _finalize_monitoring(False, failure_message)
            return MonitorResult(
                success=False,
                message=failure_message,
                rebase_count=rebase_count,
                final_state=state,
                review_completed=review_notified,
                ci_passed=False,
            )

        elif state.check_status == CheckStatus.CANCELLED:
            log("CI cancelled", json_mode)
            # Issue #1241: Log CI cancelled
            elapsed_time = int(time.time() - start_time)
            log_ci_monitor_event(
                pr_number=pr_number,
                action="ci_state_change",
                result="cancelled",
                details={
                    "elapsed_seconds": elapsed_time,
                    "poll_iterations": poll_iteration,
                    "rebase_count": rebase_count,
                },
            )
            # Issue #1311: Save final state for --result retrieval
            _finalize_monitoring(False, "CI cancelled")
            return MonitorResult(
                success=False,
                message="CI cancelled",
                rebase_count=rebase_count,
                final_state=state,
                review_completed=review_notified,
                ci_passed=False,
            )

        # Still pending
        pending_checks = [
            c.get("name", "unknown")
            for c in state.check_details
            if c.get("state") in ("IN_PROGRESS", "PENDING")
        ]
        remaining = int(timeout_seconds - elapsed)
        log(
            f"Waiting... ({len(pending_checks)} checks pending, {remaining}s remaining)",
            json_mode,
        )

        # Show wait time utilization hints periodically
        show_wait_time_hint(pr_number, poll_iteration, json_mode)
        poll_iteration += 1

        # Periodic rate limit check (Issue #896)
        if poll_iteration - last_rate_limit_check >= rate_limit_check_frequency:
            rate_remaining, rate_limit, reset_ts = check_rate_limit()
            # Use rate_limit > 0 to check for successful API response (0, 0, 0 indicates failure)
            if rate_limit > 0:
                log_rate_limit_warning(rate_remaining, rate_limit, reset_ts, json_mode)
                new_interval = get_adjusted_interval(interval, rate_remaining)
                if new_interval != adjusted_interval:
                    old_interval = adjusted_interval
                    adjusted_interval = new_interval
                    direction = (
                        IntervalDirection.DECREASE
                        if new_interval < old_interval
                        else IntervalDirection.INCREASE
                    )
                    log(f"Adjusted polling interval to {adjusted_interval}s", json_mode)
                    # Log interval adjustment to hook-execution.log (Issue #1244, #1385)
                    log_rate_limit_event(
                        RateLimitEventType.ADJUSTED_INTERVAL,
                        rate_remaining,
                        rate_limit,
                        reset_ts,
                        {
                            "old_interval": old_interval,
                            "new_interval": adjusted_interval,
                            "direction": direction,
                        },
                    )
                    # Log recovery event when interval returns to base value (Issue #1385)
                    if new_interval == interval and old_interval != interval:
                        log(
                            f"Rate limit recovered - polling interval returned to {interval}s",
                            json_mode,
                        )
                        log_rate_limit_event(
                            RateLimitEventType.RECOVERED,
                            rate_remaining,
                            rate_limit,
                            reset_ts,
                            {"base_interval": interval, "previous_interval": old_interval},
                        )
            last_rate_limit_check = poll_iteration

        time.sleep(adjusted_interval)

    # This line is intentionally unreachable - it serves as a safety net
    # for type checkers and to catch logic errors if the loop ever exits unexpectedly
    raise AssertionError("Unexpected exit from monitoring loop")  # pragma: no cover
