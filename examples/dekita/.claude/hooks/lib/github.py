#!/usr/bin/env python3
"""GitHub CLI（gh）関連のユーティリティ関数を提供する。

Why:
    gh CLIコマンドのパース・実行・結果解析を一元化し、
    各フックでの重複実装とバグを防ぐ。

What:
    - parse_gh_pr_command(): gh prコマンドからサブコマンド・PR番号抽出
    - extract_pr_number(): PR番号のみを抽出
    - get_pr_number_for_branch(): ブランチからPR番号取得
    - get_pr_merge_status(): PRのマージ状態詳細取得
    - get_observation_issues(): observationラベル付きIssue取得
    - is_pr_merged(): PRのマージ済み判定

Remarks:
    - shlex.splitでクォート文字列を正しく処理
    - heredoc/--body引数内テキストの誤検出を防止
    - エラー時はNone/False/空リストを返すfail-open設計

Changelog:
    - silenvx/dekita#318: heredoc誤検出問題を修正
    - silenvx/dekita#557: PR番号抽出ロジックを統一
    - silenvx/dekita#890: is_pr_merged()追加
    - silenvx/dekita#1258: 絶対パスgh対応
    - silenvx/dekita#2377: get_pr_merge_status()追加
    - silenvx/dekita#2588: get_observation_issues()追加
"""

import json
import shlex
import subprocess

from .command_utils import (
    COMMAND_WRAPPERS,
    ends_with_shell_separator,
    get_command_name,
    is_command_wrapper,
    normalize_shell_separators,
)
from .constants import TIMEOUT_LIGHT, TIMEOUT_MEDIUM

# Internal aliases for backward compatibility (Issue #1337)
# These were previously defined here; now imported from command_utils
_get_command_name = get_command_name
_ends_with_shell_separator = ends_with_shell_separator
_COMMAND_WRAPPERS = COMMAND_WRAPPERS
_is_command_wrapper = is_command_wrapper
_normalize_shell_separators = normalize_shell_separators


def parse_gh_pr_command(command: str) -> tuple[str | None, str | None]:
    """Parse gh pr command to extract subcommand and PR number.

    Uses shlex.split() to properly handle quoted strings, avoiding false positives
    from text inside --body, heredocs, or other string arguments.

    This fixes Issue #318: heredoc/引数内のテキストを誤検出する問題
    Issue #557: PR番号抽出ロジックを common.py に統一

    Handles:
    - gh pr merge 123
    - gh pr merge #123
    - gh pr checkout 123
    - gh pr view 123
    - gh --repo owner/repo pr merge 123
    - gh -R owner/repo pr close 123 --comment "reason"
    - gh pr edit 123 --title "new title"
    - gh pr merge --squash 123

    Args:
        command: The full command string

    Returns:
        Tuple of (subcommand, pr_number) or (None, None) if not a gh pr command
    """
    # Normalize shell separators (e.g., 'echo foo;gh pr list' -> 'echo foo ; gh pr list')
    normalized = _normalize_shell_separators(command)

    try:
        # Use shlex.split to properly tokenize, handling quoted strings
        tokens = shlex.split(normalized)
    except ValueError:
        # If shlex fails (e.g., unbalanced quotes), fall back to simple split
        tokens = normalized.split()

    if not tokens:
        return None, None

    # Find 'gh' command - handle piped/chained commands by finding first 'gh'
    # Issue #1258: Handle absolute paths like /usr/bin/gh
    # Issue #1258: Skip false positives where 'gh' is an argument path
    # Must be in command position: start of line, after shell operator, or after env var
    gh_start = None
    gh_tokens = []
    search_start = 0
    shell_operators = ("|", ";", "&&", "||")

    while search_start < len(tokens):
        # Find next token whose basename is 'gh' AND is in command position
        gh_start = None
        for i in range(search_start, len(tokens)):
            if _get_command_name(tokens[i]) != "gh":
                continue
            # Check if in valid command position
            if i == 0:
                gh_start = i
                break
            prev_token = tokens[i - 1]
            if prev_token in shell_operators or _ends_with_shell_separator(prev_token):
                gh_start = i
                break
            if "=" in prev_token and not prev_token.startswith("-"):
                gh_start = i
                break
            if _is_command_wrapper(prev_token):
                gh_start = i
                break

        if gh_start is None:
            return None, None

        # Extract tokens after 'gh' until we hit a separator
        gh_tokens = []
        for token in tokens[gh_start + 1 :]:
            if token in shell_operators or _ends_with_shell_separator(token):
                break
            gh_tokens.append(token)

        # If gh_tokens is non-empty, proceed to check for 'pr' subcommand
        # If empty, continue searching for the next 'gh' match
        if gh_tokens:
            break
        search_start = gh_start + 1
        gh_start = None

    if gh_start is None or not gh_tokens:
        return None, None

    # Skip global flags to find 'pr'
    # Flags that take arguments: --repo, -R, --hostname, --config, etc.
    # Flags that don't take arguments: --help, -h, --version
    # Handle both --flag value and --flag=value formats
    i = 0
    flags_with_args = {"--repo", "-R", "--hostname", "--config"}
    flags_without_args = {"--help", "-h", "--version"}
    while i < len(gh_tokens):
        token = gh_tokens[i]
        if token.startswith("-"):
            # Check for --flag=value format (already includes value)
            if "=" in token:
                # Flag already contains its value, just skip this token
                i += 1
            elif token in flags_with_args:
                # Skip flag and its argument (if present and not another flag)
                if i + 1 < len(gh_tokens) and not gh_tokens[i + 1].startswith("-"):
                    i += 2
                else:
                    i += 1
            elif token in flags_without_args:
                # Skip only the flag itself
                i += 1
            else:
                # Unknown flag - assume it might take an argument if next token
                # doesn't start with '-' and isn't 'pr', 'issue', etc.
                if (
                    i + 1 < len(gh_tokens)
                    and not gh_tokens[i + 1].startswith("-")
                    and gh_tokens[i + 1] not in ("pr", "issue", "repo", "auth", "api")
                ):
                    i += 2
                else:
                    i += 1
        else:
            break

    # Check if this is a 'pr' command
    if i >= len(gh_tokens) or gh_tokens[i] != "pr":
        return None, None

    # Get subcommand
    if i + 1 >= len(gh_tokens):
        return None, None
    subcommand = gh_tokens[i + 1]

    # Find PR number (first numeric argument after subcommand)
    # Skip flags and their values to correctly identify PR number
    # Common flags that take values
    subcommand_flags_with_args = {
        "--title",
        "--body",
        "--body-file",
        "-F",
        "--comment",
        "--message",
        "--commit-title",
        "--commit-body",
        "--assignee",
        "--reviewer",
        "--label",
        "--base",
        "--head",
        "--milestone",
        "--project",
        "--reason",
        "--template",
        "--author",
        "--search",
        "--json",
        "--jq",
        "--state",
        "--limit",
        "-L",
        "--page",
        "--repo",
        "-R",
    }
    pr_number = None
    j = i + 2  # Start after 'pr' and subcommand
    while j < len(gh_tokens):
        token = gh_tokens[j]
        if token.startswith("-"):
            # Check for --flag=value format (already includes value)
            if "=" in token:
                j += 1
                continue
            # Check if this flag takes an argument
            if token in subcommand_flags_with_args:
                # Skip flag and its value if present
                if j + 1 < len(gh_tokens) and not gh_tokens[j + 1].startswith("-"):
                    j += 2
                else:
                    j += 1
            else:
                # Unknown flag or boolean flag, skip just the flag
                j += 1
            continue
        # Check for PR number (plain digits or #digits)
        if token.isdigit():
            pr_number = token
            break
        if token.startswith("#") and len(token) > 1 and token[1:].isdigit():
            pr_number = token[1:]
            break
        j += 1

    return subcommand, pr_number


def extract_pr_number(command: str) -> str | None:
    """Extract PR number from gh pr command.

    Convenience wrapper around parse_gh_pr_command that returns only the PR number.

    Args:
        command: The full command string

    Returns:
        PR number as string, or None if not found
    """
    _, pr_number = parse_gh_pr_command(command)
    return pr_number


def get_pr_number_for_branch(branch: str) -> str | None:
    """Get PR number associated with a branch.

    Uses `gh pr view` to find the PR for the given branch.

    Args:
        branch: The git branch name

    Returns:
        PR number as string, or None if no PR found
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "view", branch, "--json", "number"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            pr_num = data.get("number")
            return str(pr_num) if pr_num else None
    except Exception:
        # Silently ignore all errors (timeout, JSON parse, network, etc.)
        # Return None to indicate PR not found or not accessible
        pass
    return None


def get_pr_merge_status(pr_number: str) -> dict:
    """Get detailed PR merge status for guidance messages.

    Issue #2377: Provides detailed status information when merge is blocked,
    helping Claude Code understand what actions are needed.

    Args:
        pr_number: The PR number as a string.

    Returns:
        Dictionary containing:
        - mergeable: Whether PR can be merged (bool or None if unknown)
        - merge_state_status: GitHub's merge state (e.g., "BLOCKED", "CLEAN")
        - review_decision: Review decision (e.g., "APPROVED", "REVIEW_REQUIRED", "")
        - status_check_status: Overall CI status ("SUCCESS", "PENDING", "FAILURE")
        - required_approvals: Number of required approvals (int)
        - current_approvals: Number of current approvals (int)
        - blocking_reasons: List of human-readable blocking reasons
        - suggested_actions: List of suggested actions to resolve
    """
    result = {
        "mergeable": None,
        "merge_state_status": "UNKNOWN",
        "review_decision": "",
        "status_check_status": "UNKNOWN",
        # required_approvals: Reserved for future use (GitHub API doesn't expose this easily)
        "required_approvals": 0,
        "current_approvals": 0,
        "blocking_reasons": [],
        "suggested_actions": [],
    }

    try:
        # Get PR status
        pr_result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                pr_number,
                "--json",
                "mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,reviews",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if pr_result.returncode != 0:
            return result

        data = json.loads(pr_result.stdout)
        result["mergeable"] = data.get("mergeable") == "MERGEABLE"
        result["merge_state_status"] = data.get("mergeStateStatus", "UNKNOWN")
        result["review_decision"] = data.get("reviewDecision", "")

        # Count approvals (unique reviewers only to avoid counting same reviewer twice)
        reviews = data.get("reviews", [])
        approving_reviewers = {
            r.get("author", {}).get("login")
            for r in reviews
            if r.get("state") == "APPROVED" and r.get("author", {}).get("login")
        }
        result["current_approvals"] = len(approving_reviewers)

        # Check CI status
        # statusCheckRollup is a list of check runs
        checks = data.get("statusCheckRollup", [])
        if checks:
            # Get conclusion or status, defaulting to empty string if both are None
            statuses = [c.get("conclusion") or c.get("status") or "" for c in checks]
            # SUCCESS and SKIPPED are considered passing
            success_statuses = ("SUCCESS", "SKIPPED")
            if all(s in success_statuses for s in statuses):
                result["status_check_status"] = "SUCCESS"
            elif any(s in ("FAILURE", "ERROR") for s in statuses):
                result["status_check_status"] = "FAILURE"
            else:
                result["status_check_status"] = "PENDING"
        else:
            result["status_check_status"] = "NONE"

        # Determine blocking reasons and suggested actions
        merge_state = result["merge_state_status"]

        # Check for BEHIND state (needs rebase)
        if merge_state == "BEHIND":
            result["blocking_reasons"].append("mainブランチより遅れています（BEHIND）")
            result["suggested_actions"].append("git rebase origin/main でリベースしてください")

        # Check for BLOCKED state
        if merge_state in ("BLOCKED", "BEHIND"):
            # Check review requirement
            if result["review_decision"] in ("", "REVIEW_REQUIRED"):
                result["blocking_reasons"].append("レビュー承認が必要ですが、承認されていません")
                result["suggested_actions"].append("別のレビュアーにレビュー承認を依頼してください")

            # Check CI status
            if result["status_check_status"] == "FAILURE":
                result["blocking_reasons"].append("CIチェックが失敗しています")
                result["suggested_actions"].append(
                    f"gh pr checks {pr_number} でCI状態を確認してください"
                )
            elif result["status_check_status"] == "PENDING":
                result["blocking_reasons"].append("CIチェックが実行中です")
                result["suggested_actions"].append("CIが完了するまで待機してください")

    except Exception:
        # Return default values on error
        pass

    return result


def get_observation_issues(
    limit: int = 50,
    fields: list[str] | None = None,
    timeout: int | None = None,
) -> list[dict]:
    """Get open issues with observation label.

    Issue #2588: Shared implementation for observation issue retrieval.
    Used by observation-reminder.py and observation-session-reminder.py.

    Args:
        limit: Maximum number of issues to return. Default 50.
        fields: JSON fields to retrieve. Default ["number", "title"].
        timeout: Timeout in seconds. Default uses TIMEOUT_LIGHT.

    Returns:
        List of observation issues as dictionaries.
    """
    if fields is None:
        fields = ["number", "title"]
    if timeout is None:
        timeout = TIMEOUT_LIGHT

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
                ",".join(fields),
                "--limit",
                str(limit),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return []

        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []


def is_pr_merged(pr_number: str) -> bool:
    """Check if a PR is already merged.

    This is used to skip checks for PRs that have already been merged,
    avoiding false positives from hooks that run after another hook
    has completed a merge operation (Issue #890).

    Args:
        pr_number: The PR number as a string.

    Returns:
        True if the PR is merged, False otherwise (including errors).
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/:owner/:repo/pulls/{pr_number}",
                "--jq",
                ".merged",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            return result.stdout.strip().lower() == "true"
    except Exception:
        # On error, assume not merged to fail open
        pass
    return False
