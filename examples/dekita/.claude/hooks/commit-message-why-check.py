#!/usr/bin/env python3
"""コミットメッセージに「なぜ」の背景が含まれているかをチェックする。

Why:
    コードの差分は「何を」変更したかを示すが、「なぜ」その変更が必要だったかは
    時間とともに失われる。git blameで追跡できるよう背景の記録を強制する。

What:
    - コミットメッセージから「なぜ」を示すキーワードを検索
    - Issue参照があればコンテキストありと判定
    - 不足の場合はコミットをブロック

Remarks:
    - lefthook経由でcommit-msgとして実行
    - Claude Codeフックではなく、Gitフック
    - merge/revert/WIP/fixup等は自動スキップ
    - commit-message-template.pyとセットで使用

Changelog:
    - silenvx/dekita#1896: フック追加
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Keywords indicating "Why" context is present (case-insensitive)
WHY_KEYWORDS_JA = [
    "なぜ",
    "理由",
    "原因",
    "背景",
    "目的",
    "ため",  # 〜のため
    "必要",  # 〜が必要
]

WHY_KEYWORDS_EN = [
    "why",
    "reason",
    "because",
    "background",
    "purpose",
    "motivation",
    "in order to",
    "so that",
    "to fix",
    "to prevent",
    "to avoid",
    "to enable",
    "to support",
]

# Section headers that indicate structured context
SECTION_HEADERS = [
    "## 背景",
    "## Background",
    "## Why",
    "## Motivation",
    "## 理由",
    "## Summary",  # Summary often contains motivation
]

# Issue/PR references (context is in the linked issue)
ISSUE_PATTERNS = [
    r"(?:closes?|fixes?|resolves?)\s*#\d+",  # Closes #123
    r"#\d+",  # Any issue reference
]


def get_subject_line(content: str) -> str:
    """Extract the subject line (first non-comment, non-empty line).

    Args:
        content: Full commit message file content.

    Returns:
        Subject line or empty string.
    """
    for line in content.split("\n"):
        if is_git_comment(line):
            continue
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def should_skip_check(content: str) -> tuple[bool, str]:
    """Determine if the why-check should be skipped.

    Args:
        content: Full commit message content.

    Returns:
        Tuple of (should_skip, reason).
    """
    subject = get_subject_line(content)

    # Merge commits
    if subject.startswith("Merge "):
        return True, "merge commit"

    # Revert commits
    if subject.startswith("Revert "):
        return True, "revert commit"

    # WIP commits
    if subject.lower().startswith("wip"):
        return True, "WIP commit"

    # fixup! commits
    if subject.startswith("fixup!") or subject.startswith("squash!"):
        return True, "fixup/squash commit"

    # Very short subject (likely incomplete)
    if len(subject) < 10:
        return True, "subject too short"

    return False, ""


def has_issue_reference(content: str) -> bool:
    """Check if the message references an issue (which has context).

    Args:
        content: Commit message content (without comments).

    Returns:
        True if issue reference found.
    """
    content_lower = content.lower()
    for pattern in ISSUE_PATTERNS:
        if re.search(pattern, content_lower):
            return True
    return False


def has_why_context(content: str) -> bool:
    """Check if the message includes "Why" context.

    Args:
        content: Commit message content (without comments).

    Returns:
        True if why context is present.
    """
    content_lower = content.lower()

    # Check section headers
    for header in SECTION_HEADERS:
        if header.lower() in content_lower:
            return True

    # Check Japanese keywords
    for keyword in WHY_KEYWORDS_JA:
        if keyword in content:  # Japanese is case-insensitive anyway
            return True

    # Check English keywords
    for keyword in WHY_KEYWORDS_EN:
        if keyword in content_lower:
            return True

    return False


def is_git_comment(line: str) -> bool:
    """Check if a line is a Git comment (not a Markdown header).

    Git comments start with "# " (hash + space) or are just "#".
    Markdown headers start with "##" or more hashes.

    Args:
        line: A single line from the commit message.

    Returns:
        True if the line is a Git comment.
    """
    # Empty comment line
    if line == "#":
        return True
    # Git comment: "# " followed by anything
    if line.startswith("# "):
        return True
    return False


def strip_comments(content: str) -> str:
    """Remove Git comment lines from content, preserving Markdown headers.

    Args:
        content: Full commit message content.

    Returns:
        Content without Git comment lines.
    """
    lines = [line for line in content.split("\n") if not is_git_comment(line)]
    return "\n".join(lines)


def check_commit_message(filepath: str) -> tuple[bool, str]:
    """Check if commit message has adequate "Why" context.

    Args:
        filepath: Path to the commit message file.

    Returns:
        Tuple of (is_valid, error_message).
    """
    path = Path(filepath)

    try:
        content = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as e:
        # If we can't read the file, allow the commit (fail-open for this check)
        return True, f"Could not read file: {e}"

    # Check skip conditions
    should_skip, reason = should_skip_check(content)
    if should_skip:
        return True, f"Skipped: {reason}"

    # Remove comments for analysis
    clean_content = strip_comments(content)

    # Issue reference provides context
    if has_issue_reference(clean_content):
        return True, "Has issue reference"

    # Check for why context
    if has_why_context(clean_content):
        return True, "Has why context"

    # No context found
    return False, ""


def format_error_message() -> str:
    """Generate helpful error message for missing context.

    Returns:
        Formatted error message.
    """
    return """
コミットメッセージに「なぜ」の説明がありません。

コードの差分は「何を」変更したかを示しますが、「なぜ」その変更が
必要だったかは時間とともに失われます。git blameで追跡できるよう、
背景を記録してください。

以下のいずれかを追加してください:
- 背景/理由の説明（「〜のため」「〜が原因」など）
- Issue参照（Closes #123）
- セクションヘッダー（## 背景, ## Background）

例:
  fix: セッション切れ時の無限リダイレクトを修正

  原因: トークン更新失敗時にリトライが無限ループしていた
  対応: 最大3回のリトライ制限を追加

  Fixes #123

詳細: coding-standards Skill の「コミットメッセージ」セクションを参照
""".strip()


def main() -> int:
    """Main entry point for the hook.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    if len(sys.argv) < 2:
        # No arguments - nothing to check
        return 0

    msg_file = sys.argv[1]

    is_valid, _ = check_commit_message(msg_file)

    if is_valid:
        return 0

    # Print error and block commit
    print(format_error_message(), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
