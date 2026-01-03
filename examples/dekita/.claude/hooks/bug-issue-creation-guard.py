#!/usr/bin/env python3
"""PRスコープの問題に対する別Issue作成をブロックする。

Why:
    PRで導入した問題（バグ、テスト不足、エッジケース等）は同じPRで修正すべき。
    別Issueを作成すると問題が残ったままマージされるリスクがある。

What:
    - gh issue createコマンドを検出
    - タイトルからPRスコープのパターン（fix:, test:, バグ等）を検出
    - 現在のブランチにオープンPRがある場合はブロック
    - PR内での修正を案内

Remarks:
    - ブロック型フック（PRスコープの問題Issue作成はブロック）
    - オープンPRがない場合はスキップ
    - PreToolUse:Bashで発火

Changelog:
    - silenvx/dekita#1130: フック追加
    - silenvx/dekita#1175, #1176: このルール違反の事例
    - code-review Skill「範囲内/範囲外の判断基準」参照
"""

import json
import re
import subprocess
import sys
from pathlib import Path

# Add parent directory for common module import
parent_dir = str(Path(__file__).parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from lib.constants import TIMEOUT_LIGHT, TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.results import make_approve_result, make_block_result
from lib.session import parse_hook_input

# Keywords that indicate an Issue that should be handled in the current PR
# Based on code-review Skill "範囲内/範囲外の判断基準":
# - このPRで追加した関数にバグ → 同じPRで修正
# - このPRで追加した関数のテスト不足 → 同じPRでテスト追加
# - このPRで追加した機能のエッジケース未対応 → 同じPRで対応
PR_SCOPE_ISSUE_PATTERNS = [
    # Bug-related patterns (existing)
    r"\bfix[:\(]",
    r"\bbug[:\(]",
    r"バグ",
    r"修正",
    r"不具合",
    # Test-related patterns (added for Issue #1175 case)
    r"\btests?[:\(]",  # test: or tests:
    r"テスト.*追加",
    r"テスト.*不足",
    r"テストカバレッジ",
    r"test\s*coverage",
    # Edge case patterns
    r"エッジケース",
    r"edge\s*case",
]


def extract_issue_title(command: str) -> str | None:
    """Extract Issue title from gh issue create command.

    Supports:
    - --title "title" or -t "title"
    - Quoted strings with single or double quotes

    Args:
        command: The gh issue create command string.

    Returns:
        The extracted title, or None if not found.
    """
    # Pattern for --title "..." or -t "..."
    # Handles both single and double quotes
    patterns = [
        r'(?:--title|-t)\s+["\']([^"\']+)["\']',
        r"(?:--title|-t)\s+(\S+)",  # Unquoted single word
    ]

    for pattern in patterns:
        match = re.search(pattern, command)
        if match:
            return match.group(1)

    return None


def is_pr_scope_issue(title: str) -> bool:
    """Check if the title indicates an Issue that should be handled in the PR.

    Args:
        title: The Issue title to check.

    Returns:
        True if the title matches PR-scope patterns (bugs, tests, edge cases).
    """
    # Note: We use case-insensitive search directly on the original title
    # to handle both English patterns (fix:, bug:, test:) and Japanese keywords
    # (バグ, 修正, テスト) correctly. Japanese characters are not affected
    # by case-insensitivity.
    for pattern in PR_SCOPE_ISSUE_PATTERNS:
        if re.search(pattern, title, re.IGNORECASE):
            return True
    return False


def get_current_pr() -> dict | None:
    """Get the current branch's open PR if it exists.

    Returns:
        Dict with PR info (number, title, headRefName), or None if no PR.
    """
    try:
        # Get current branch
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode != 0:
            return None

        current_branch = result.stdout.strip()
        if not current_branch or current_branch == "main":
            return None

        # Check if there's an open PR for this branch
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--head",
                current_branch,
                "--state",
                "open",
                "--json",
                "number,title,headRefName",
                "--limit",
                "1",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return None

        prs = json.loads(result.stdout)
        if prs:
            return prs[0]
        return None

    except Exception:
        return None


def main():
    """
    PreToolUse hook for gh issue create commands.

    Warns when creating PR-scope Issues while working on a PR branch.
    """
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Only check gh issue create commands - log and skip silently if not
        if not re.search(r"\bgh\s+issue\s+create\b", command):
            log_hook_execution("bug-issue-creation-guard", "skip", "Not an issue create command")
            sys.exit(0)

        # Extract title - log and skip silently if not found
        title = extract_issue_title(command)
        if not title:
            log_hook_execution("bug-issue-creation-guard", "skip", "No title found")
            sys.exit(0)

        # Check if title indicates a PR-scope issue - log and skip silently if not
        if not is_pr_scope_issue(title):
            log_hook_execution("bug-issue-creation-guard", "skip", "Not a PR-scope issue")
            sys.exit(0)

        # Check if there's an open PR for current branch - log and skip silently if not
        current_pr = get_current_pr()
        if not current_pr:
            log_hook_execution("bug-issue-creation-guard", "skip", "No open PR for current branch")
            sys.exit(0)

        # Block creating PR-scope Issue while PR is open
        pr_number = current_pr.get("number", "?")
        pr_title = current_pr.get("title", "")

        # Block with guidance message
        # Note: This hook detects by title pattern only; if truly out of scope, user can override
        block_msg = f"""🚫 PRスコープの可能性があるIssue作成をブロック

作成しようとしているIssue: "{title}"
現在のPR: #{pr_number} ({pr_title})

【検出方法】
Issueタイトルのパターン（test:, テスト追加, エッジケース等）から検出。

【code-review Skillのルール】
- このPRで導入した問題 → このPRで修正（別Issueにしない）
- 既存コードの問題 → Issue作成を続行してOK

【対応方法】
1. このPRで導入した問題の場合: PRで直接修正してください
2. 既存コードの問題の場合: ユーザーに確認してからIssue作成を続行

背景: Issue #1175, #1176 でこのルール違反が発生。
"""
        # make_block_result内でlog_hook_executionが自動呼び出しされる
        result = make_block_result(
            "bug-issue-creation-guard",
            block_msg,
        )
        print(json.dumps(result))
        sys.exit(2)

    except Exception as e:
        # On error, approve to avoid blocking
        result = make_approve_result("bug-issue-creation-guard", f"Error: {e}")

    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
