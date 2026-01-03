#!/usr/bin/env python3
# - 責務: gh pr create 時に Closes で参照される Issue のアサイン確認・自動アサイン
# - 重複なし: issue-auto-assign.py は worktree 作成時、本フックは PR 作成時
# - 非ブロック型: 自動アサインを試み、失敗時は警告のみ
# - AGENTS.md: Issue #203 に基づく実装
"""
Hook to check and auto-assign issues referenced in PR body.

When `gh pr create` is executed, this hook:
1. Extracts Issue numbers from PR body (Closes #xxx patterns)
2. Checks if those issues are assigned to the current user
3. Auto-assigns unassigned issues to prevent conflicts

This complements issue-auto-assign.py which handles worktree creation time.
For cases where:
- Existing PR branch doesn't have issue number in name
- Issue was created after the branch/PR
- Multiple issues are referenced in PR body

Limitation:
    This hook only extracts inline body (--body "..." or -b "...").
    Bodies entered via editor or --fill are not available to PreToolUse hooks
    since they don't exist until after command execution.
    This is acceptable because Claude Code always uses inline body arguments.
"""

import json
import re
import subprocess
import sys

from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.input_context import extract_input_context, merge_details_with_context
from lib.session import parse_hook_input
from lib.strings import strip_quoted_strings


def is_gh_pr_create_command(command: str) -> bool:
    """Check if command is a gh pr create command."""
    if not command.strip():
        return False
    stripped_command = strip_quoted_strings(command)
    return bool(re.search(r"gh\s+pr\s+create\b", stripped_command))


def extract_pr_body(command: str) -> str | None:
    """Extract the PR body from gh pr create command.

    Handles:
    - --body "..."
    - -b "..."
    - --body="..."
    - HEREDOC patterns: --body "$(cat <<'EOF' ... EOF)"

    Returns None if body is not explicitly specified inline.
    """
    # Try HEREDOC pattern first (most complex)
    heredoc_pattern = r'--body\s+"\$\(cat\s+<<[\'"]?(\w+)[\'"]?\s*(.*?)\s*\1\s*\)"'
    heredoc_match = re.search(heredoc_pattern, command, re.DOTALL)
    if heredoc_match:
        return heredoc_match.group(2)

    # Standard patterns (ordered by specificity)
    # Pattern for escaped quotes: [^"\\]* matches non-quote/non-backslash,
    # (?:\\.[^"\\]*)* matches any escaped char followed by non-quote/non-backslash
    dq_content = r'([^"\\]*(?:\\.[^"\\]*)*)'  # Double-quoted content with escapes
    sq_content = r"([^'\\]*(?:\\.[^'\\]*)*)"  # Single-quoted content with escapes
    patterns = [
        rf'--body="{dq_content}"',  # --body="..."
        rf"--body='{sq_content}'",  # --body='...'
        rf'-b="{dq_content}"',  # -b="..."
        rf"-b='{sq_content}'",  # -b='...'
        rf'--body\s+"{dq_content}"',  # --body "..."
        rf"--body\s+'{sq_content}'",  # --body '...'
        rf'-b\s+"{dq_content}"',  # -b "..."
        rf"-b\s+'{sq_content}'",  # -b '...'
    ]

    for pattern in patterns:
        match = re.search(pattern, command)
        if match:
            return match.group(1)

    return None


def extract_closes_issues(body: str) -> list[int]:
    """Extract issue numbers from Closes/Fixes/Resolves keywords.

    Returns sorted list of unique issue numbers referenced in the body.
    Duplicates are removed since assign_issue() is idempotent anyway.
    """
    if not body:
        return []

    # GitHub keywords that auto-close issues
    # Supports: Closes #123, Fixes #456, Resolves #789
    pattern = r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?\s+#(\d+)\b"
    matches = re.findall(pattern, body, re.IGNORECASE)
    return sorted(set(int(num) for num in matches))


def get_issue_assignees(issue_number: int) -> list[str] | None:
    """Get current assignees of an issue.

    Returns:
        list[str]: List of assignee logins if successful
        None: If the lookup failed (to distinguish from empty assignees)
    """
    try:
        result = subprocess.run(
            ["gh", "issue", "view", str(issue_number), "--json", "assignees"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return [a.get("login", "") for a in data.get("assignees", [])]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        # Fail silently: gh command unavailable, timeout, or invalid JSON
        # Return None to indicate lookup failure (distinct from empty assignees)
        pass
    return None


def get_current_user() -> str | None:
    """Get current GitHub user login."""
    try:
        result = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # Fail silently: gh command unavailable or timeout
        # Return None to skip current user comparison
        pass
    return None


def assign_issue(issue_number: int) -> bool:
    """Assign the issue to the current user."""
    try:
        result = subprocess.run(
            ["gh", "issue", "edit", str(issue_number), "--add-assignee", "@me"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def main():
    """PreToolUse hook for Bash commands.

    Checks and auto-assigns issues referenced in PR body via Closes keyword.
    """
    result = {"decision": "approve"}

    try:
        data = parse_hook_input()
        input_context = extract_input_context(data)
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Only check gh pr create commands
        if not is_gh_pr_create_command(command):
            log_hook_execution(
                "pr-issue-assign-check",
                "approve",
                "Not gh pr create",
                input_context,
            )
            print(json.dumps(result))
            sys.exit(0)

        # Extract PR body
        body = extract_pr_body(command)
        if not body:
            log_hook_execution(
                "pr-issue-assign-check",
                "approve",
                "No body found",
                input_context,
            )
            print(json.dumps(result))
            sys.exit(0)

        # Extract issue numbers from Closes keywords
        issue_numbers = extract_closes_issues(body)
        if not issue_numbers:
            log_hook_execution(
                "pr-issue-assign-check",
                "approve",
                "No Closes keywords",
                input_context,
            )
            print(json.dumps(result))
            sys.exit(0)

        # Get current user for comparison
        current_user = get_current_user()

        # Check and auto-assign each issue
        messages = []
        for issue_num in issue_numbers:
            assignees = get_issue_assignees(issue_num)

            if assignees is None:
                # Lookup failed - skip to avoid wrongly assigning already-assigned issues
                messages.append(f"⚠️ Issue #{issue_num} のアサイン確認に失敗。手動確認を推奨")
            elif not assignees:
                # No assignees (empty list) - auto-assign
                if assign_issue(issue_num):
                    messages.append(f"✅ Issue #{issue_num} に自動アサインしました（競合防止）")
                else:
                    messages.append(
                        f"⚠️ Issue #{issue_num} のアサインに失敗。"
                        f"手動: `gh issue edit {issue_num} --add-assignee @me`"
                    )
            elif current_user and current_user not in assignees:
                # Assigned to someone else - warn but don't block
                messages.append(
                    f"ℹ️ Issue #{issue_num} は他者にアサイン済み: {', '.join(assignees)}"
                )
            # If current user is assigned, no message needed

        if messages:
            result["systemMessage"] = "\n".join(messages)
            log_hook_execution(
                "pr-issue-assign-check",
                "approve",
                f"Processed {len(issue_numbers)} issue(s)",
                merge_details_with_context({"issues": issue_numbers}, input_context),
            )
        else:
            log_hook_execution(
                "pr-issue-assign-check",
                "approve",
                "All issues already assigned",
                merge_details_with_context({"issues": issue_numbers}, input_context),
            )

    except Exception as e:
        error_msg = f"Hook error: {e}"
        print(f"[pr-issue-assign-check] {error_msg}", file=sys.stderr)
        # Preserve input_context if available for debugging
        log_hook_execution(
            "pr-issue-assign-check",
            "approve",
            error_msg,
            input_context if "input_context" in dir() else {},
        )

    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
