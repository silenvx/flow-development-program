"""PR operations for ci-monitor.

This module handles PR validation, state fetching, rebasing, merging, and recreation.
Extracted from ci-monitor.py as part of Issue #1765 refactoring (Phase 6).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from typing import Any

from ci_monitor.ai_review import (
    get_codex_review_requests,
    get_codex_reviews,
    has_copilot_or_codex_reviewer,
    is_gemini_review_pending,
)
from ci_monitor.constants import (
    DEFAULT_STABLE_CHECK_INTERVAL,
    DEFAULT_STABLE_WAIT_MINUTES,
    DEFAULT_STABLE_WAIT_TIMEOUT,
    MERGE_ERROR_BEHIND,
)
from ci_monitor.events import log
from ci_monitor.github_api import run_gh_command, run_gh_command_with_error
from ci_monitor.models import CheckStatus, MergeState, PRState, RebaseResult
from ci_monitor.state import save_monitor_state


def validate_pr_number(pr_number: str) -> tuple[bool, str]:
    """Validate PR number format and range.

    Args:
        pr_number: PR number string to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        num = int(pr_number)
    except ValueError:
        return False, f"Invalid PR number '{pr_number}': must be a positive integer"

    if num <= 0:
        return False, f"Invalid PR number '{pr_number}': must be a positive integer"

    # Soft limit to catch obvious typos
    if num > 999999:
        return False, f"Invalid PR number '{pr_number}': value too large (max: 999999)"

    return True, ""


def validate_pr_numbers(pr_numbers: list[str]) -> list[str]:
    """Validate all PR numbers and exit with error if any are invalid.

    Args:
        pr_numbers: List of PR number strings to validate

    Returns:
        List of validated PR numbers (unchanged if all valid).
        Returns empty list if input is empty (no validation performed).

    Exits:
        Exits with code 1 if any PR number is invalid.
        Error messages are printed to stderr before exit.
    """
    errors = []
    for pr_number in pr_numbers:
        is_valid, error_msg = validate_pr_number(pr_number)
        if not is_valid:
            errors.append(error_msg)

    if errors:
        for error in errors:
            print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)

    return pr_numbers


def get_pr_state(pr_number: str) -> tuple[PRState | None, str | None]:
    """Fetch current PR state from GitHub.

    Returns:
        Tuple of (state, error_message). If state is None, error_message contains
        the reason for failure (e.g., API error, rate limit, timeout).
    """
    # Get merge state using gh pr view
    success, output, error = run_gh_command_with_error(
        [
            "pr",
            "view",
            pr_number,
            "--json",
            "mergeStateStatus",
            "--jq",
            ".mergeStateStatus",
        ]
    )

    if not success:
        return None, error or "Unknown error fetching merge state"

    try:
        merge_state = MergeState(output.strip() if output.strip() else "UNKNOWN")
    except ValueError:
        merge_state = MergeState.UNKNOWN

    # Get requested reviewers using gh api
    success, output = run_gh_command(
        [
            "api",
            f"/repos/{{owner}}/{{repo}}/pulls/{pr_number}",
            "--jq",
            "[.requested_reviewers[].login]",
        ]
    )

    pending_reviewers = []
    if success and output:
        try:
            pending_reviewers = json.loads(output)
        except json.JSONDecodeError:
            # Invalid JSON from API - continue with empty list
            pass

    # Get CI check status
    success, output = run_gh_command(
        [
            "pr",
            "checks",
            pr_number,
            "--json",
            "name,state",
        ]
    )

    check_status = CheckStatus.PENDING
    check_details: list[dict[str, Any]] = []

    if success and output:
        try:
            checks = json.loads(output)
            check_details = checks

            if not checks:
                check_status = CheckStatus.PENDING
            elif any(c.get("state") == "FAILURE" for c in checks):
                check_status = CheckStatus.FAILURE
            elif any(c.get("state") == "CANCELLED" for c in checks):
                check_status = CheckStatus.CANCELLED
            elif all(c.get("state") in ("SUCCESS", "SKIPPED") for c in checks):
                check_status = CheckStatus.SUCCESS
            else:
                check_status = CheckStatus.PENDING
        except json.JSONDecodeError:
            # Invalid JSON from API - continue with pending status
            pass

    return PRState(
        merge_state=merge_state,
        pending_reviewers=pending_reviewers,
        check_status=check_status,
        check_details=check_details,
    ), None


def has_local_changes() -> tuple[bool, str]:
    """Check for uncommitted or unpushed local changes.

    Issue #865: Prevents ci-monitor from rebasing when local changes exist,
    which would cause push conflicts.

    Returns:
        Tuple of (has_changes, description).
        has_changes is True if uncommitted or unpushed changes exist.
        description explains what changes were found.
    """
    reasons = []

    # Check for uncommitted changes (staged or unstaged)
    # Issue #1805: Exclude untracked files (??) as they don't affect rebase
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Filter out untracked files (lines starting with "??")
            tracked_changes = [
                line
                for line in result.stdout.strip().split("\n")
                if line and not line.startswith("??")
            ]
            if tracked_changes:
                reasons.append("uncommitted changes")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # Git not available or timed out - assume no changes (fail-safe)
        pass

    # Check for unpushed commits
    try:
        result = subprocess.run(
            ["git", "log", "@{u}..HEAD", "--oneline"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            count = len(result.stdout.strip().split("\n"))
            reasons.append(f"{count} unpushed commit(s)")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # Upstream not set or git unavailable - ignore
        pass

    if reasons:
        return True, ", ".join(reasons)
    return False, ""


def get_main_last_commit_time() -> int | None:
    """Get the timestamp of the last commit on origin/main.

    Issue #1239: Used to detect when main branch is stable (no recent updates).

    Returns:
        Unix timestamp of the last commit, or None if failed.
    """
    try:
        # First, fetch to get latest remote state
        fetch_result = subprocess.run(
            ["git", "fetch", "origin", "main"],
            capture_output=True,
            timeout=30,
        )
        if fetch_result.returncode != 0:
            return None

        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "origin/main"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        # Git command failed or timed out - return None to trigger retry
        pass
    return None


def wait_for_main_stable(
    stable_duration_minutes: int = DEFAULT_STABLE_WAIT_MINUTES,
    check_interval: int = DEFAULT_STABLE_CHECK_INTERVAL,
    timeout_minutes: int = DEFAULT_STABLE_WAIT_TIMEOUT,
    json_mode: bool = False,
) -> bool:
    """Wait for main branch to stabilize (no recent updates).

    Issue #1239: When max_rebase is reached during active concurrent development,
    wait for main to stop being updated before continuing with rebases.

    Args:
        stable_duration_minutes: How long main must be stable before returning.
        check_interval: Seconds between stability checks.
        timeout_minutes: Maximum time to wait for stability.
        json_mode: Whether to output in JSON format.

    Returns:
        True if main became stable, False if timeout.
    """
    start_time = time.time()
    timeout_seconds = timeout_minutes * 60
    stable_duration_seconds = stable_duration_minutes * 60

    log(
        f"mainブランチの安定を待機中（{stable_duration_minutes}分間更新なしが必要）...",
        json_mode,
    )
    if not json_mode:
        print(
            f"\n⏳  リベース試行上限に達しました。mainの安定を待機中。\n"
            f"   mainは{stable_duration_minutes}分間更新がない必要があります。\n"
            f"   タイムアウト: {timeout_minutes}分。\n"
        )

    last_known_commit_time: int | None = None

    while time.time() - start_time < timeout_seconds:
        current_commit_time = get_main_last_commit_time()
        if current_commit_time is None:
            log("mainのコミット時刻を取得できませんでした。再試行中...", json_mode)
            time.sleep(check_interval)
            continue

        now = int(time.time())
        time_since_last_commit = now - current_commit_time

        if time_since_last_commit >= stable_duration_seconds:
            log(
                f"mainブランチが安定しました（最終更新: {time_since_last_commit // 60}分前）",
                json_mode,
            )
            if not json_mode:
                print(
                    f"\n✅  mainブランチが安定しました。最終更新は{time_since_last_commit // 60}分前です。\n"
                    f"   リベース操作を再開します。\n"
                )
            return True

        if last_known_commit_time != current_commit_time:
            if last_known_commit_time is not None:
                log("mainが更新されました。安定タイマーをリセット", json_mode)
                if not json_mode:
                    print("   ↻ mainが更新されました。安定タイマーをリセット。")
            last_known_commit_time = current_commit_time

        remaining_wait = stable_duration_seconds - time_since_last_commit
        if not json_mode:
            print(
                f"   ⏳ main最終更新: {time_since_last_commit}秒前、"
                f"あと{remaining_wait}秒の安定が必要",
                end="\r",
            )

        time.sleep(check_interval)

    log("mainの安定待機がタイムアウトしました", json_mode)
    if not json_mode:
        print(f"\n⏰  {timeout_minutes}分経過でタイムアウトしました。処理を続行します。\n")
    return False


def rebase_pr(pr_number: str) -> RebaseResult:
    """Attempt to rebase the PR (Issue #1348: Enhanced logging).

    Returns:
        RebaseResult with success status, conflict flag, and optional error message.
    """
    success, stdout, stderr = run_gh_command_with_error(
        ["pr", "update-branch", pr_number, "--rebase"], timeout=60
    )

    if success:
        return RebaseResult(success=True)

    error_output = stderr or stdout or ""
    conflict_indicators = ["conflict", "merge conflict", "could not be rebased"]
    has_conflict = any(indicator in error_output.lower() for indicator in conflict_indicators)

    return RebaseResult(
        success=False,
        conflict=has_conflict,
        error_message=error_output if error_output else None,
    )


def merge_pr(pr_number: str) -> tuple[bool, str]:
    """Merge the PR using squash merge.

    Args:
        pr_number: The PR number.

    Returns:
        Tuple of (success, message). On failure, message is MERGE_ERROR_BEHIND
        if the branch is behind main and needs rebase, otherwise the error message.
    """
    success, stdout, stderr = run_gh_command_with_error(
        ["pr", "merge", pr_number, "--squash"],
        timeout=120,
    )
    if success:
        return True, "Merge successful"

    error_output = stderr or stdout
    if error_output and "not up to date" in error_output.lower():
        return False, MERGE_ERROR_BEHIND
    return False, error_output or "Unknown error"


def get_pr_branch_name(pr_number: str) -> str | None:
    """Get the head branch name of a PR.

    Args:
        pr_number: The PR number.

    Returns:
        The branch name, or None if it could not be determined.
    """
    success, output = run_gh_command(
        ["pr", "view", pr_number, "--json", "headRefName", "--jq", ".headRefName"]
    )
    if success and output:
        return output.strip()
    return None


def format_rebase_summary(count: int) -> str:
    """Format rebase count message for output (Issue #1364).

    When multiple rebases were needed, suggests considering merge queue
    to reduce CI churn from concurrent development.

    Args:
        count: Number of rebases performed.

    Returns:
        Formatted message string.
    """
    suffix = " (consider merge queue)" if count >= 2 else ""
    return f"Rebases performed: {count}{suffix}"


def sync_local_after_rebase(branch_name: str, json_mode: bool = False) -> bool:
    """Sync local branch after remote rebase (Issue #895).

    After ci-monitor rebases the remote branch via `gh pr update-branch`,
    the local branch becomes out of sync. This function pulls the rebased
    changes to keep local and remote in sync.

    Args:
        branch_name: The branch name to sync.
        json_mode: If True, suppress human-readable output to preserve JSON format.

    Returns:
        True if sync was successful or not needed, False if sync failed.
    """
    # Check if we're in a git repository
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return True

    # Check current branch
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return True
        current_branch = result.stdout.strip()
        if current_branch != branch_name:
            if not json_mode:
                print(
                    f"ℹ️  Local branch is not on the target branch ({current_branch} != {branch_name}), skipping sync"
                )
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return True

    # Check for uncommitted changes
    try:
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if status_result.returncode == 0 and status_result.stdout.strip():
            if not json_mode:
                print(
                    "⚠️  コミットされていないローカル変更があります。自動同期をスキップします。手動で実行:"
                )
                print(f"   git pull --rebase origin {branch_name}")
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # git status failed - proceed with sync attempt anyway
        pass

    # Attempt to sync
    try:
        if not json_mode:
            print("🔄 ローカルブランチをリモートと同期中...")
        result = subprocess.run(
            ["git", "pull", "--rebase", "origin", branch_name],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            if not json_mode:
                print("✅ ローカルブランチの同期が完了しました")
            return True
        if not json_mode:
            print(f"⚠️  ローカルブランチの同期に失敗しました: {result.stderr.strip()}")
            print(f"   手動で実行: git pull --rebase origin {branch_name}")
        return False
    except subprocess.TimeoutExpired:
        if not json_mode:
            print(
                f"⚠️  同期がタイムアウトしました。手動で実行: git pull --rebase origin {branch_name}"
            )
        return False
    except FileNotFoundError:
        return True


def reopen_pr_with_retry(
    pr_number: str, comment: str, max_retries: int = 3
) -> tuple[bool, str, int]:
    """Reopen a PR with retry logic.

    Issue #1558: When PR reopen fails, retry up to max_retries times
    with a short delay between attempts.

    Args:
        pr_number: The PR number to reopen
        comment: Comment to add when reopening
        max_retries: Maximum number of retry attempts (default: 3, minimum: 1)

    Returns:
        Tuple of (success, error_message, attempts_made).
        If successful, error_message is empty.
        attempts_made indicates how many attempts were made.
    """
    if max_retries < 1:
        max_retries = 1

    last_error = ""
    for attempt in range(max_retries):
        success, output = run_gh_command(
            [
                "pr",
                "reopen",
                pr_number,
                "--comment",
                comment,
            ]
        )
        if success:
            return True, "", attempt + 1
        last_error = output
        if attempt < max_retries - 1:
            time.sleep(1)
    return False, last_error, max_retries


def recreate_pr(pr_number: str) -> tuple[bool, str | None, str]:
    """Close existing PR and create a new one from the same branch.

    Issue #1532: When Copilot review is stuck in pending state for too long,
    recreating the PR often resolves the issue.

    Args:
        pr_number: The PR number to recreate.

    Returns:
        Tuple of (success, new_pr_number_or_none, message).
        If successful, new_pr_number is the number of the newly created PR.
        message contains either the success message or error details.
    """
    # 1. Get existing PR details
    success, output = run_gh_command(
        [
            "pr",
            "view",
            pr_number,
            "--json",
            "title,body,headRefName,baseRefName,labels,assignees,isDraft",
        ]
    )

    if not success:
        return False, None, f"Failed to get PR details: {output}"

    try:
        pr_data = json.loads(output)
    except json.JSONDecodeError:
        return False, None, f"Failed to parse PR details: {output}"

    title = pr_data.get("title", "")
    body = pr_data.get("body") or ""
    head_branch = pr_data.get("headRefName", "")
    base_branch = pr_data.get("baseRefName", "main")
    labels = [label.get("name", "") for label in pr_data.get("labels", [])]
    assignees = [a.get("login", "") for a in pr_data.get("assignees", [])]
    is_draft = pr_data.get("isDraft", False)

    if not title:
        return False, None, f"PRタイトルを取得できませんでした (PR番号: {pr_number})"

    if not head_branch:
        return False, None, f"ブランチ名を取得できませんでした (PR: {pr_number})"

    # 2. Close existing PR with comment
    close_comment = (
        "Copilotレビューがpending状態でスタックしているため、PRを自動で作り直します。\n\n"
        "新しいPRが自動作成されます。"
    )
    success, close_output = run_gh_command(
        [
            "pr",
            "close",
            pr_number,
            "--comment",
            close_comment,
        ]
    )

    if not success:
        return False, None, f"PRのクローズに失敗しました: {close_output}"

    # 3. Add auto-recreation note to body
    recreation_note = (
        f"\n\n---\n"
        f"🔄 **自動再作成**: Copilotレビューがpending状態でスタックしていたため、"
        f"PR #{pr_number} から自動で作り直されました。"
    )
    new_body = body + recreation_note

    # 4. Create new PR
    create_args = [
        "pr",
        "create",
        "--title",
        title,
        "--body",
        new_body,
        "--base",
        base_branch,
        "--head",
        head_branch,
    ]

    for label in labels:
        if label:
            create_args.extend(["--label", label])

    for assignee in assignees:
        if assignee:
            create_args.extend(["--assignee", assignee])

    if is_draft:
        create_args.append("--draft")

    success, create_output = run_gh_command(create_args)

    if not success:
        # Reopen original PR since creation failed
        reopen_comment = (
            f"新しいPRの作成に失敗したため、このPRを再オープンしました。\n\n"
            f"**失敗理由**: {create_output}"
        )
        max_reopen_retries = 3
        reopen_success, reopen_error, attempts = reopen_pr_with_retry(
            pr_number, reopen_comment, max_retries=max_reopen_retries
        )
        if reopen_success:
            return (
                False,
                None,
                f"新しいPRの作成に失敗しました: {create_output}。元のPR #{pr_number} を再オープンしました。",
            )

        # All retry attempts failed - save recovery state
        recovery_state = {
            "status": "pr_recovery_needed",
            "success": False,
            "message": f"PR #{pr_number} が閉じられたまま復旧に失敗しました",
            "closed_pr": pr_number,
            "create_error": create_output,
            "reopen_error": reopen_error,
            "reopen_attempts": attempts,
        }
        save_monitor_state(pr_number, recovery_state)
        return (
            False,
            None,
            f"新しいPRの作成に失敗しました: {create_output}。元のPRの再オープンにも失敗しました（{attempts}回リトライ）: {reopen_error}",
        )

    # Extract new PR number from output
    match = re.search(r"/pull/(\d+)", create_output)
    new_pr_number = match.group(1) if match else None

    if new_pr_number:
        return (
            True,
            new_pr_number,
            f"PR #{pr_number} を閉じて新しい PR #{new_pr_number} を作成しました",
        )
    return (
        True,
        None,
        f"新しいPRを作成しましたが、PR番号を取得できませんでした: {create_output}",
    )


def is_codex_review_pending(pr_number: str) -> bool:
    """Check if there's a pending Codex review request.

    A Codex review is pending if there's an @codex review comment and
    no Codex review has been posted after that comment was created.

    Returns True if Codex review is requested but not yet complete.
    """
    requests = get_codex_review_requests(pr_number)
    if not requests:
        return False

    reviews = get_codex_reviews(pr_number)

    for request in requests:
        request_time = request.created_at
        has_review = any(review.get("submitted_at", "") > request_time for review in reviews)

        if has_review:
            continue

        return True

    return False


def has_ai_review_pending(pr_number: str, pending_reviewers: list[str]) -> bool:
    """Check if any AI review (Copilot, Codex Cloud, or Gemini) is pending.

    Issue #2711: Added Gemini Code Assist to the checks.

    This combines two detection mechanisms:
    1. GitHub reviewer assignments (Copilot, Codex, or Gemini in pending_reviewers,
       with rate limit detection for Gemini)
    2. Codex Cloud via @codex review comments

    Args:
        pr_number: PR number to check
        pending_reviewers: List of pending reviewers from GitHub API

    Returns:
        True if AI review is in progress but not yet complete.
    """
    if has_copilot_or_codex_reviewer(pending_reviewers):
        return True

    if is_codex_review_pending(pr_number):
        return True

    # Issue #2711: Check for Gemini review (skipped if rate limited)
    if is_gemini_review_pending(pr_number, pending_reviewers):
        return True

    return False
