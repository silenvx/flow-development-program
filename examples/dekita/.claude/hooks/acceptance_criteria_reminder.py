#!/usr/bin/env python3
"""PR作成時に対象Issueの受け入れ条件未完了を警告する。

Why:
    受け入れ条件が未完了のままPRを作成すると、マージ時にmerge-checkで
    ブロックされる。PR作成時に警告することで、事前に気づいて対処できる。

What:
    - gh pr createコマンドを検出
    - ブランチ名からIssue番号を抽出
    - 対象Issueの受け入れ条件（チェックボックス）を確認
    - 未完了項目がある場合に警告（ブロックはしない）

Remarks:
    - 警告型フック（ブロックしない、systemMessageで通知）
    - merge-checkはマージ時、本フックはPR作成時に警告
    - 取り消し線（~~）付きの項目はスキップ

Changelog:
    - silenvx/dekita#1288: フック追加
    - silenvx/dekita#823: 取り消し線の扱い
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

# Add hooks directory to path for imports
HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))

from lib.execution import log_hook_execution
from lib.git import get_current_branch
from lib.session import parse_hook_input


def strip_code_blocks(text: str) -> str:
    """Remove code blocks from text to avoid false positives.

    Removes:
    - Fenced code blocks (```...```)
    - Inline code (`...`)
    """
    # Remove fenced code blocks (multiline)
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Remove inline code
    text = re.sub(r"`[^`]+`", "", text)
    return text


def is_pr_create_command(command: str) -> bool:
    """Check if the command is 'gh pr create'.

    Uses shlex.split to properly tokenize the command and check that
    the first three tokens are exactly 'gh', 'pr', 'create'.
    This avoids false positives with commands like:
    - rg "gh pr create" README.md
    - echo "gh pr create"
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        # shlex.split can fail on malformed strings
        return False

    if len(tokens) < 3:
        return False

    return tokens[0] == "gh" and tokens[1] == "pr" and tokens[2] == "create"


def extract_issue_number_from_branch(branch_name: str) -> str | None:
    """Extract issue number from branch name.

    Supports formats like:
    - feat/issue-123-description
    - fix/issue-123
    - issue-123
    - 123-feature
    """
    if not branch_name:
        return None

    # Pattern to match issue-XXX or XXX at various positions
    patterns = [
        r"issue[/-](\d+)",  # issue-123 or issue/123
        r"^(\d+)[/-]",  # 123-feature
        r"[/-](\d+)$",  # feature-123
    ]

    for pattern in patterns:
        match = re.search(pattern, branch_name, re.IGNORECASE)
        if match:
            return match.group(1)

    return None


def fetch_issue_acceptance_criteria(
    issue_number: str,
) -> tuple[bool, str, list[tuple[bool, str]]]:
    """Fetch issue and extract acceptance criteria (checkbox items).

    Args:
        issue_number: The issue number.

    Returns:
        Tuple of (success, title, criteria).
        criteria is a list of (is_completed, text) tuples.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                issue_number,
                "--json",
                "title,body,state",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            return (False, "", [])

        data = json.loads(result.stdout)
        title = data.get("title") or ""
        body = data.get("body") or ""
        state = data.get("state") or ""

        # Skip closed Issues
        if state == "CLOSED":
            return (False, "", [])

        # Strip code blocks before extracting checkboxes
        body_without_code = strip_code_blocks(body)

        # Extract checkbox items: - [ ] or - [x] or * [ ] format
        # Issue #823: Treat strikethrough checkboxes (- [ ] ~~text~~) as completed
        criteria = []
        pattern = r"^[\s]*[-*]\s*\[([ xX])\]\s*(.+)$"
        strikethrough_pattern = re.compile(r"^~~.+?~~")

        for line in body_without_code.split("\n"):
            match = re.match(pattern, line)
            if match:
                checkbox_mark = match.group(1).lower()
                criteria_text = match.group(2).strip()
                is_strikethrough = bool(strikethrough_pattern.match(criteria_text))
                is_completed = checkbox_mark == "x" or is_strikethrough
                criteria.append((is_completed, criteria_text))

        return (True, title, criteria)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        # Handle expected errors:
        # - TimeoutExpired: gh command took too long
        # - JSONDecodeError: Invalid JSON response
        # - OSError: Process execution errors
        return (False, "", [])


def check_acceptance_criteria(issue_number: str) -> dict | None:
    """Check if an issue has incomplete acceptance criteria.

    Args:
        issue_number: The issue number to check.

    Returns:
        Dict with issue info if there are incomplete criteria, None otherwise.
    """
    success, title, criteria = fetch_issue_acceptance_criteria(issue_number)
    if not success or not criteria:
        return None

    incomplete_items = [text for is_completed, text in criteria if not is_completed]
    total_count = len(criteria)
    completed_count = total_count - len(incomplete_items)

    if incomplete_items:
        return {
            "issue_number": issue_number,
            "title": title,
            "total_count": total_count,
            "completed_count": completed_count,
            "incomplete_items": incomplete_items,
        }

    return None


def format_warning_message(issue_info: dict) -> str:
    """Format a warning message for incomplete acceptance criteria."""
    issue_num = issue_info["issue_number"]
    title = issue_info["title"]
    completed = issue_info["completed_count"]
    total = issue_info["total_count"]
    incomplete_items = issue_info["incomplete_items"]

    items_display = "\n".join(f"  - [ ] {item}" for item in incomplete_items[:5])
    if len(incomplete_items) > 5:
        items_display += f"\n  ...他{len(incomplete_items) - 5}件"

    return (
        f"⚠️ Issue #{issue_num} ({title}) の受け入れ条件が未完了です\n"
        f"   進捗: {completed}/{total} 完了\n"
        f"   未完了の条件:\n{items_display}\n\n"
        "   PR作成後、Issueのチェックボックスを更新してください。\n"
        "   そうしないと、マージ時にブロックされます。"
    )


def main() -> None:
    """Main entry point."""
    hook_input = parse_hook_input()
    if not hook_input:
        print(json.dumps({"continue": True}))
        return

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})

    # Only process Bash tool calls
    if tool_name != "Bash":
        print(json.dumps({"continue": True}))
        return

    command = tool_input.get("command", "")

    # Check if this is a PR create command
    if not is_pr_create_command(command):
        print(json.dumps({"continue": True}))
        return

    # Get current branch
    branch = get_current_branch()
    if not branch:
        print(json.dumps({"continue": True}))
        return

    # Extract issue number from branch
    issue_number = extract_issue_number_from_branch(branch)
    if not issue_number:
        print(json.dumps({"continue": True}))
        return

    # Check acceptance criteria
    issue_info = check_acceptance_criteria(issue_number)

    if issue_info:
        # Log the reminder
        log_hook_execution(
            hook_name="acceptance-criteria-reminder",
            decision="approve",  # Don't block, just warn
            reason=f"Issue #{issue_number} has incomplete acceptance criteria",
            details={
                "issue_number": issue_number,
                "completed": issue_info["completed_count"],
                "total": issue_info["total_count"],
                "incomplete_count": len(issue_info["incomplete_items"]),
            },
        )

        # Print warning message
        warning = format_warning_message(issue_info)
        print(warning, file=sys.stderr)

    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
