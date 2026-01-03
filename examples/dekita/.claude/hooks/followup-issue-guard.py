#!/usr/bin/env python3
"""Issue参照なしの「フォローアップ」発言をブロックする。

Why:
    「後で対応します」とコメントしてもIssueを作らないと、フォローアップは
    忘れられて実行されない。コメント投稿前にIssue参照を強制することで、
    約束を形骸化させないようにする。

What:
    - gh pr/issue commentコマンドを検出
    - 「後で」「フォローアップ」「スコープ外」等のキーワードを検索
    - Issue参照（#1234等）がない場合はブロック
    - Issue参照がある場合は許可

Remarks:
    - SKIP_FOLLOWUP_ISSUE_GUARD=1で無効化可能
    - 対象はコメントコマンドのみ（コミットメッセージは対象外）

Changelog:
    - silenvx/dekita#1496: フック追加
"""

import json
import re
import sys
from pathlib import Path

# Add parent directory for common module import
parent_dir = str(Path(__file__).parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from lib.execution import log_hook_execution
from lib.results import check_skip_env, make_approve_result, make_block_result
from lib.session import parse_hook_input

HOOK_NAME = "followup-issue-guard"
SKIP_ENV_VAR = "SKIP_FOLLOWUP_ISSUE_GUARD"

# Keywords that indicate a "follow up later" statement
FOLLOWUP_KEYWORDS = [
    r"後で",
    r"将来",
    r"フォローアップ",
    r"別途",
    r"今後.*対応",
    r"今後.*検討",
    r"スコープ外",
    r"scope\s*外",
    r"out\s*of\s*scope",
    r"later\b",
    r"future\b",
    r"follow[\s-]*up",
]

# Pattern to match Issue references: #1234, Issue #1234, issue-1234, etc.
ISSUE_REF_PATTERN = re.compile(
    r"(?:"
    r"#\d+"  # #1234
    r"|Issue\s*#?\d+"  # Issue #1234 or Issue 1234
    r"|issue-\d+"  # issue-1234
    r")",
    re.IGNORECASE,
)


def is_comment_command(command: str) -> bool:
    """Check if the command is a gh comment command.

    Args:
        command: The command string to check.

    Returns:
        True if it's a comment command.
    """
    # Match: gh pr comment, gh issue comment, gh api ...comments
    # Only match at the start of command or after command separators (&&, ;, |)
    # Avoid matching inside quoted strings
    patterns = [
        r"(?:^|&&|;|\|)\s*gh\s+pr\s+comment\b",
        r"(?:^|&&|;|\|)\s*gh\s+issue\s+comment\b",
        r"(?:^|&&|;|\|)\s*gh\s+api\s+.*comments",
    ]
    for pattern in patterns:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False


def extract_comment_body(command: str) -> str | None:
    """Extract comment body from gh command.

    Args:
        command: The gh command string.

    Returns:
        The extracted comment body, or None if not found.
    """
    # Match --body "..." or -b "..."
    # Note: Use separate patterns for double and single quotes
    # to avoid truncating on apostrophes (e.g., "We'll handle this later")
    patterns = [
        r"(?:--body|-b)\s+\"([^\"]+)\"",  # Double quotes
        r"(?:--body|-b)\s+'([^']+)'",  # Single quotes
        # HEREDOC pattern: --body "$(cat <<'EOF' ... EOF)"
        r'--body\s+"\$\(cat\s*<<[\'"]?EOF[\'"]?\n([\s\S]*?)\nEOF\s*\)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, command, re.DOTALL)
        if match:
            return match.group(1)
    return None


def contains_followup_keyword(text: str) -> tuple[bool, str | None]:
    """Check if text contains follow-up keywords.

    Args:
        text: The text to check.

    Returns:
        Tuple of (contains_keyword, matched_keyword).
    """
    for keyword in FOLLOWUP_KEYWORDS:
        if re.search(keyword, text, re.IGNORECASE):
            return True, keyword
    return False, None


def contains_issue_reference(text: str) -> bool:
    """Check if text contains an Issue reference.

    Args:
        text: The text to check.

    Returns:
        True if an Issue reference is found.
    """
    return bool(ISSUE_REF_PATTERN.search(text))


def main() -> None:
    """Main entry point for the hook."""
    hook_input = parse_hook_input()

    # Skip if env var is set
    if check_skip_env(HOOK_NAME, SKIP_ENV_VAR):
        print(json.dumps(make_approve_result(HOOK_NAME)))
        return

    # Only process Bash tool
    tool_name = hook_input.get("tool_name", "")
    if tool_name != "Bash":
        print(json.dumps(make_approve_result(HOOK_NAME)))
        return

    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "")

    # Only check comment commands
    if not is_comment_command(command):
        print(json.dumps(make_approve_result(HOOK_NAME)))
        return

    # Extract comment body
    comment_body = extract_comment_body(command)
    if not comment_body:
        print(json.dumps(make_approve_result(HOOK_NAME)))
        return

    # Check for follow-up keywords
    has_followup, matched_keyword = contains_followup_keyword(comment_body)
    if not has_followup:
        print(json.dumps(make_approve_result(HOOK_NAME)))
        return

    # Check for Issue reference
    if contains_issue_reference(comment_body):
        # Has Issue reference, approve
        log_hook_execution(
            HOOK_NAME,
            "approve",
            f"Follow-up comment with Issue reference: {matched_keyword}",
        )
        print(json.dumps(make_approve_result(HOOK_NAME)))
        return

    # Block: follow-up keyword without Issue reference
    reason = f"""「フォローアップ」発言にIssue参照がありません。

検出されたキーワード: {matched_keyword}

コメント本文にIssue番号が含まれていません。
「後で対応」と言う前に、必ずIssueを作成してください。

対処方法:
1. まずIssueを作成:
   gh issue create --title "<タイトル>" --body "<内容>"

2. Issue番号を含めてコメントを再投稿:
   例: "Issue #1234 を作成しました。今後のフォローアップとして対応します。"

参照: AGENTS.md「後でフォローアップ」発言時のIssue作成（必須）"""

    log_hook_execution(
        HOOK_NAME,
        "block",
        f"Follow-up comment without Issue reference: {matched_keyword}",
        {"command": command[:200], "matched_keyword": matched_keyword},
    )

    print(json.dumps(make_block_result(HOOK_NAME, reason)))


if __name__ == "__main__":
    main()
