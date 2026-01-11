#!/usr/bin/env python3
"""gh pr create時にClosesキーワードの有無をチェックし、追加を提案する。

Why:
    PRボディにClosesキーワードがないと、マージ時にIssueが自動クローズされず
    手動でのクローズ忘れにつながる。

What:
    - ブランチ名からIssue番号を抽出
    - PRボディにCloses/Fixes/Resolvesキーワードがあるか確認
    - ない場合は追加を提案（ブロックしない）

Remarks:
    - 提案型フック（ブロックしない、systemMessageで提案）
    - PreToolUse:Bashで発火（gh pr createコマンド）
    - --body-file/-Fオプション使用時はスキップ（ファイル内容は検査不可）

Changelog:
    - silenvx/dekita#155: フック追加
"""

import json
import re
import sys

from lib.execution import log_hook_execution
from lib.git import get_current_branch
from lib.session import parse_hook_input
from lib.strings import strip_quoted_strings


def is_gh_pr_create_command(command: str) -> bool:
    """Check if command is a gh pr create command."""
    if not command.strip():
        return False
    stripped_command = strip_quoted_strings(command)
    return bool(re.search(r"gh\s+pr\s+create\b", stripped_command))


def extract_issue_from_branch(branch: str) -> str | None:
    """Extract Issue number from branch name.

    Supports patterns like:
    - fix/issue-123-description
    - feature/123-description
    - fix-123
    - issue-123
    - 123-description
    """
    if not branch:
        return None

    patterns = [
        r"issue[/-](\d+)",  # issue-123 or issue/123
        r"(?:fix|feat|feature|bug|hotfix|chore|refactor)[/-](\d+)",  # fix-123, feat/123
        r"(?:^|/)(\d+)(?:-|$)",  # /123- or 123- at start
    ]

    for pattern in patterns:
        match = re.search(pattern, branch, re.IGNORECASE)
        if match:
            return f"#{match.group(1)}"

    return None


def has_body_file_option(command: str) -> bool:
    """Check if command uses --body-file or -F option.

    These options load body from file/template, so we can't check the content.
    """
    stripped_command = strip_quoted_strings(command)
    return bool(re.search(r"(?:--body-file|-F)\b", stripped_command))


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


def has_closes_keyword(body: str, issue_number: str) -> bool:
    """Check if body contains a Closes keyword for the given issue.

    Recognizes GitHub keywords:
    - Closes #xxx / Closes: #xxx
    - Fixes #xxx / Fixes: #xxx
    - Resolves #xxx / Resolves: #xxx
    (case-insensitive)
    """
    if not body or not issue_number:
        return False

    # Extract just the number from #xxx
    num = issue_number.lstrip("#")

    # GitHub keywords that auto-close issues
    # Supports both "Closes #123" and "Closes: #123" formats
    pattern = rf"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?\s+#{num}\b"
    return bool(re.search(pattern, body, re.IGNORECASE))


def main():
    """
    PreToolUse hook for Bash commands.

    Suggests adding Closes keyword when missing from PR body.
    """
    result = {"decision": "approve"}

    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Only check gh pr create commands
        if not is_gh_pr_create_command(command):
            pass  # Early return case - still log at end
        else:
            # Get current branch
            branch = get_current_branch()
            if not branch:
                pass  # No branch - still log at end
            else:
                # Extract Issue number from branch
                issue_number = extract_issue_from_branch(branch)
                if not issue_number:
                    pass  # No Issue number in branch name - nothing to suggest
                elif has_body_file_option(command):
                    pass  # Skip if using --body-file or -F (can't check file content)
                else:
                    # Extract PR body - if None, body might come from template/editor
                    body = extract_pr_body(command)
                    if body is not None:
                        # Check for Closes keyword
                        if has_closes_keyword(body, issue_number):
                            result["systemMessage"] = (
                                f"✅ closes-keyword-check: Closes {issue_number} が含まれています"
                            )
                        else:
                            result["systemMessage"] = (
                                f"⚠️ closes-keyword-check: PRボディに `Closes {issue_number}` がありません\n\n"
                                f"**推奨**: PRボディに以下を追加してください:\n"
                                f"```\n"
                                f"Closes {issue_number}\n"
                                f"```\n\n"
                                f"これにより、PRマージ時にIssue {issue_number}が自動closeされます。\n"
                                f"（ブランチ名 `{branch}` から推測）"
                            )

    except Exception as e:
        print(f"[closes-keyword-check] Hook error: {e}", file=sys.stderr)
        result = {"decision": "approve"}

    # Always log execution for accurate statistics
    log_hook_execution(
        "closes-keyword-check", result.get("decision", "approve"), result.get("reason")
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
