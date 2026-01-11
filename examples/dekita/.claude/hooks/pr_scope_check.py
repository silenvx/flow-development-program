#!/usr/bin/env python3
"""gh pr create時に1 Issue = 1 PRルールを強制。

Why:
    複数Issueを1つのPRにまとめると、レビューが複雑になり、
    問題発生時のリバートが困難になり、変更履歴が不明確になる。

What:
    - gh pr create コマンドを検出
    - PRタイトルからIssue参照を抽出
    - 複数Issue参照がある場合はブロック
    - 単一Issue参照はOKメッセージを表示

Remarks:
    - ブロック型フック
    - -F/--body-file使用時は--titleも指定するよう警告

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import json
import re
import sys

from lib.execution import log_hook_execution
from lib.results import make_block_result
from lib.session import parse_hook_input
from lib.strings import strip_quoted_strings


def is_gh_pr_create_command(command: str) -> bool:
    """Check if command is a gh pr create command.

    Returns False for:
    - Commands inside quoted strings (e.g., echo 'gh pr create')
    - Empty commands
    """
    if not command.strip():
        return False

    # Strip quoted strings to avoid false positives
    stripped_command = strip_quoted_strings(command)

    # Check if gh pr create exists in the stripped command
    return bool(re.search(r"gh\s+pr\s+create\b", stripped_command))


def extract_pr_title(command: str) -> str | None:
    """Extract the PR title from gh pr create command.

    Handles --title "...", -t "...", --title="...", and -t"..." formats.
    """
    # Match --title or -t followed by the value
    # The value can be:
    # - Single quoted: 'value'
    # - Double quoted: "value"
    # - With equals sign: --title="value" or -t="value"
    # - Unquoted (until next space or end)

    # Try --title first (ordered by specificity)
    patterns = [
        r'--title=["\']([^"\']+)["\']',  # --title="..." or --title='...'
        r'-t=["\']([^"\']+)["\']',  # -t="..." or -t='...'
        r'--title\s+["\']([^"\']+)["\']',  # --title "..." or --title '...'
        r'-t\s+["\']([^"\']+)["\']',  # -t "..." or -t '...'
        r"--title=(\S+)",  # --title=value (unquoted with equals)
        r"-t=(\S+)",  # -t=value (unquoted with equals)
        r"--title\s+(\S+)",  # --title value (unquoted)
        r"-t\s+(\S+)",  # -t value (unquoted)
    ]

    for pattern in patterns:
        match = re.search(pattern, command)
        if match:
            return match.group(1)

    return None


def count_issue_references(text: str) -> list[str]:
    """Count and return Issue references in the text.

    Returns list of Issue numbers found (e.g., ['#123', '#456']).
    """
    # Match #xxx pattern (Issue number)
    # Exclude patterns that are clearly not Issue refs (e.g., #! for shebang)
    issues = re.findall(r"#(\d+)", text)
    return [f"#{num}" for num in issues]


def has_body_file_option(command: str) -> bool:
    """Check if command has -F or --body-file option.

    These options read the PR body from a file, which means the title
    might be entered interactively without being checked by this hook.
    """
    # Strip quoted strings to avoid false positives
    # e.g., echo '-F' should not be detected
    stripped = strip_quoted_strings(command)

    # Match -F or --body-file options
    # -F can be followed by space, =, or directly by value (e.g., -Fpr.md)
    patterns = [
        r"--body-file[\s=]",  # --body-file <file> or --body-file=<file>
        r"-F[\s=]",  # -F <file> or -F=<file>
        r"-F\S",  # -Fpr.md (attached value without space)
    ]
    for pattern in patterns:
        if re.search(pattern, stripped):
            return True
    return False


def main():
    """
    PreToolUse hook for Bash commands.

    Blocks `gh pr create` if the PR title contains multiple Issue references.
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
            # Extract PR title
            title = extract_pr_title(command)
            if not title:
                # No title specified - approve (GitHub will prompt for title)
                # Warn if -F/--body-file is used without --title
                # The title will be entered interactively and won't be checked
                if has_body_file_option(command):
                    result["systemMessage"] = (
                        "⚠️ pr-scope-check: -F/--body-file使用時は--titleも指定してください。 "
                        "対話的に入力されたタイトルはチェックされません。"
                    )
            else:
                # Check for multiple Issue references
                issues = count_issue_references(title)
                if len(issues) > 1:
                    reason = (
                        f"PRタイトルに複数のIssue参照があります: {', '.join(issues)}\n\n"
                        "**1 Issue = 1 PR ルール**\n"
                        "各Issueは独立したPRで対応してください。\n\n"
                        "理由:\n"
                        "- レビューが容易になる\n"
                        "- 問題発生時のリバートが簡単\n"
                        "- 変更履歴が明確になる\n\n"
                        "対処方法:\n"
                        "1. 現在のブランチで1つのIssueのみ対応\n"
                        "2. 他のIssueは別のworktree/ブランチで対応\n"
                        "3. 各Issue用に別々のPRを作成"
                    )
                    result = make_block_result("pr-scope-check", reason)
                elif len(issues) == 1:
                    result["systemMessage"] = f"✅ pr-scope-check: 単一Issue参照OK ({issues[0]})"

    except Exception as e:
        print(f"[pr-scope-check] Hook error: {e}", file=sys.stderr)
        result = {"decision": "approve"}

    # Always log execution for accurate statistics
    log_hook_execution("pr-scope-check", result.get("decision", "approve"), result.get("reason"))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
