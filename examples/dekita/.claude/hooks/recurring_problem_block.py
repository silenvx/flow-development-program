#!/usr/bin/env python3
"""繰り返し発生する問題を検出し、Issue作成を強制してからマージを許可。

Why:
    同じフックで何度もブロックされる場合、根本的な改善が必要。
    Issue作成を強制することで、問題を放置せず仕組み化を促す。

What:
    - gh pr merge コマンドを検出
    - 過去7日間のフック実行ログを集計
    - 3セッション以上で3回以上ブロックされたフックを検出
    - 該当する[改善]Issueがなければブロック

State:
    - reads: .claude/logs/flow/hook-execution-*.jsonl

Remarks:
    - ブロック型フック
    - PROTECTIVE_HOOKSは繰り返しブロック対象外
    - WORKFLOW_PROBLEM_HOOKSが対象（現在は空）
    - Issue作成後はブロック解除

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#1994: セッション別ログファイル対応
    - silenvx/dekita#2042: codex-review-checkをPROTECTIVE_HOOKSに追加
    - silenvx/dekita#2084: ci-wait-check等をPROTECTIVE_HOOKSに追加
    - silenvx/dekita#2115: flow-effect-verifierをPROTECTIVE_HOOKSに追加
    - silenvx/dekita#2182: planning-enforcementをPROTECTIVE_HOOKSに追加
    - silenvx/dekita#2217: worktree-warningをPROTECTIVE_HOOKSに追加
    - silenvx/dekita#2226: クローズ済みIssueも検索対象に
"""

import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from common import EXECUTION_LOG_DIR
from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.logging import read_all_session_log_entries
from lib.results import make_approve_result, make_block_result
from lib.session import parse_hook_input

# Configuration
RECURRING_THRESHOLD = 3  # Block if detected in 3+ sessions
RECURRING_DAYS = 7  # Look back 7 days
BLOCK_COUNT_THRESHOLD = 3  # Consider it a problem if blocked 3+ times in a session

# Hooks that indicate workflow problems when blocked repeatedly
# These are hooks where repeated blocks suggest the agent needs to improve its workflow
#
# Selection criteria for this list:
# - Hooks that enforce workflow best practices (not security/protection)
# - Repeated blocks indicate the agent is not following expected patterns
# - Improving behavior would reduce future blocks
#
# Issue #2084: Removed ci-wait-check, resolve-thread-guard, related-task-check
# These are normal guard functions (not workflow problems):
# - ci-wait-check: Blocks merge before CI completion (expected behavior)
# - resolve-thread-guard: Blocks unsigned comment resolution (expected behavior)
# - related-task-check: Blocks until related tasks are confirmed (expected behavior)
#
# Issue #2115: Removed flow-effect-verifier
# flow-effect-verifier blocks when flows are incomplete (expected behavior).
# Repeated blocks indicate incomplete flows, not agent workflow problems.
#
# Issue #2182: Removed planning-enforcement
# planning-enforcement blocks when no plan file exists, but:
# - This is not a workflow problem (agent correctly blocks unplanned work)
# - Bypass conditions (labels, title prefixes, SKIP_PLAN) handle valid cases
# - Repeated blocks indicate multiple Issues requiring planning (expected)
#
# Issue #2217: Removed worktree-warning (moved to PROTECTIVE_HOOKS)
# worktree-warning blocks editing on main branch (expected behavior).
# The workflow of "edit blocked → create worktree → edit" is valid.
WORKFLOW_PROBLEM_HOOKS: frozenset[str] = frozenset()

# Protective hooks - blocks are expected and not workflow problems
# These should NOT trigger improvement suggestions
# Issue #2042: codex-review-check is a quality gate, not a workflow problem indicator
# Issue #2084: Added ci-wait-check, resolve-thread-guard, related-task-check
#   These are normal guard functions that block as expected:
#   - ci-wait-check: Blocks merge before CI completion
#   - resolve-thread-guard: Blocks unsigned comment resolution
#   - related-task-check: Blocks until related tasks are confirmed
# Issue #2115: Added flow-effect-verifier
#   - flow-effect-verifier: Blocks when flows are incomplete (expected behavior)
# Issue #2182: Added planning-enforcement
#   - planning-enforcement: Blocks unplanned work (expected behavior with bypass options)
# Issue #2217: Added worktree-warning
#   - worktree-warning: Blocks editing on main branch (expected behavior)
PROTECTIVE_HOOKS = frozenset(
    {
        "codex-review-check",
        "worktree-session-guard",
        "worktree-removal-check",
        "locked-worktree-guard",
        "ci-wait-check",
        "resolve-thread-guard",
        "related-task-check",
        "flow-effect-verifier",
        "planning-enforcement",
        "worktree-warning",
    }
)


def aggregate_recurring_problems(days: int = RECURRING_DAYS) -> dict[str, int]:
    """Count sessions where each hook repeatedly blocked.

    Issue #1994: Reads from all session-specific hook-execution-{session_id}.jsonl files
    (aggregates across all sessions) instead of single hook-execution.log with rotation.

    Counts unique sessions where a hook blocked 3+ times (indicates workflow problem).

    Args:
        days: Number of days to look back (default: 7)

    Returns:
        Dict mapping hook name to unique session count where it blocked repeatedly.
        Empty dict if no files exist or on error.
    """
    # Issue #1994: Read from session-specific files
    entries = read_all_session_log_entries(EXECUTION_LOG_DIR, "hook-execution")
    if not entries:
        return {}

    cutoff = datetime.now(UTC) - timedelta(days=days)

    # Track block counts per session per hook
    # Key: (hook_name, session_id), Value: block count
    session_block_counts: dict[tuple[str, str], int] = defaultdict(int)

    for entry in entries:
        try:
            # Only count blocks
            if entry.get("decision") != "block":
                continue

            hook_name = entry.get("hook", "")

            # Skip protective hooks (expected blocks)
            if hook_name in PROTECTIVE_HOOKS:
                continue

            # Only count workflow problem hooks
            if hook_name not in WORKFLOW_PROBLEM_HOOKS:
                continue

            # Parse timestamp
            timestamp_str = entry.get("timestamp", "")
            if not timestamp_str:
                continue

            # Handle timezone
            if timestamp_str.endswith("Z"):
                timestamp_str = timestamp_str[:-1] + "+00:00"
            timestamp = datetime.fromisoformat(timestamp_str)

            if timestamp < cutoff:
                continue

            session_id = entry.get("session_id", "unknown")
            session_block_counts[(hook_name, session_id)] += 1

        except (ValueError, KeyError):
            continue

    # Count sessions where hook blocked 3+ times (threshold for "repeated")
    # Key: hook_name, Value: set of session_ids with repeated blocks
    hook_sessions: dict[str, set[str]] = defaultdict(set)
    for (hook_name, session_id), count in session_block_counts.items():
        if count >= BLOCK_COUNT_THRESHOLD:
            hook_sessions[hook_name].add(session_id)

    # Return count of unique sessions per hook
    return {hook: len(sessions) for hook, sessions in hook_sessions.items()}


def escape_github_search_term(term: str) -> str:
    """Escape special characters for GitHub search query.

    GitHub search interprets certain characters specially. This function
    escapes them to ensure literal matching.

    Args:
        term: The search term to escape

    Returns:
        Escaped search term safe for GitHub search queries
    """
    # Characters that have special meaning in GitHub search:
    # - Backslash is used as an escape character
    # - Double quotes are used for exact phrase matching
    #
    # We first escape backslashes, then escape internal double quotes.
    # The ordering matters so that we don't double-escape the backslashes
    # introduced when escaping quotes.
    escaped = term.replace("\\", "\\\\")
    escaped = escaped.replace('"', '\\"')
    return escaped


def has_issue(source: str) -> bool:
    """Check if an Issue (open or closed) exists for this problem.

    Issue #2226: Changed from checking only open Issues to checking all Issues.
    Once an Issue is created (regardless of state), the problem is considered
    "addressed" and should not block merges.

    Searches for Issues with title containing "[改善] {source}".

    Args:
        source: The problem source name

    Returns:
        True if an Issue exists (open or closed)
    """
    try:
        # Search for all issues with matching title pattern
        # Issue #2226: Use --state all to include closed Issues (NOT_PLANNED, COMPLETED)
        # Escape special characters in source name for GitHub search (Issue #607)
        escaped_source = escape_github_search_term(source)
        search_term = f'"[改善] {escaped_source}"'
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--state",
                "all",
                "--search",
                f"{search_term} in:title",
                "--json",
                "number,title",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )

        if result.returncode != 0:
            # Fail open: if gh CLI fails, don't block
            return True

        issues = json.loads(result.stdout) if result.stdout.strip() else []
        # Verify title actually contains the pattern (search can be fuzzy)
        for issue in issues:
            title = issue.get("title", "")
            if f"[改善] {source}" in title:
                return True

        return False

    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        # Fail open: if we can't check, don't block
        return True


def check_is_merge_command(command: str) -> bool:
    """Check if command is a gh pr merge invocation.

    Handles commands with:
    - Direct invocation: gh pr merge 123
    - Chained commands: cd repo && gh pr merge 123
    - Environment variable prefixes: GH_TOKEN="..." gh pr merge 123
    """
    # Split by shell operators and check each part
    parts = re.split(r"\s*(?:&&|\|\||;)\s*", command)
    for part in parts:
        # Match optional env var assignments followed by gh pr merge
        # Pattern: (VAR=value )* gh pr merge
        if re.match(r"^\s*(?:\w+=\S*\s+)*gh\s+pr\s+merge\b", part):
            return True
    return False


def main():
    """
    PreToolUse hook for Bash commands.

    Blocks `gh pr merge` when recurring problems are detected and unaddressed.
    """
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Only check gh pr merge commands
        if not check_is_merge_command(command):
            # Not a merge command, log and skip silently (no output per design principle)
            log_hook_execution("recurring-problem-block", "skip", "Not a merge command")
            sys.exit(0)

        # Aggregate recurring problems
        session_counts = aggregate_recurring_problems()

        # Find problems exceeding threshold
        blocking_problems = []
        for source, count in session_counts.items():
            if count >= RECURRING_THRESHOLD:
                # Check if Issue already exists
                if has_issue(source):
                    continue

                blocking_problems.append({"source": source, "count": count})

        # If no blocking problems, approve silently (no output per design principle)
        if not blocking_problems:
            log_hook_execution("recurring-problem-block", "approve", "No blocking problems")
            sys.exit(0)

        # Build block message
        problem_list = "\n".join(
            f"  - {p['source']}: {p['count']}セッションで検出" for p in blocking_problems[:5]
        )
        more_msg = (
            f"\n  ... 他 {len(blocking_problems) - 5} 件" if len(blocking_problems) > 5 else ""
        )

        first_problem = blocking_problems[0]["source"]
        reason = (
            f"⚠️ 繰り返し検出されている問題があります。\n\n"
            f"検出された問題:\n{problem_list}{more_msg}\n\n"
            f"対応が必要です:\n"
            f'gh issue create --title "[改善] {first_problem}の対策を検討" '
            f"--label enhancement,P2\n\n"
            f"Issueを作成するとブロックが解除されます。"
        )

        result = make_block_result("recurring-problem-block", reason)
        log_hook_execution(
            "recurring-problem-block",
            "block",
            reason,
            {"blocking_problems": blocking_problems},
        )
        print(json.dumps(result))
        sys.exit(0)

    except Exception as e:
        # On error, approve to avoid blocking
        error_msg = f"Hook error: {e}"
        print(f"[recurring-problem-block] {error_msg}", file=sys.stderr)
        result = make_approve_result("recurring-problem-block", error_msg)
        log_hook_execution("recurring-problem-block", "approve", error_msg)
        print(json.dumps(result))
        sys.exit(0)


if __name__ == "__main__":
    main()
