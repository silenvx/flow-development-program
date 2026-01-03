#!/usr/bin/env python3
"""merge-checkフックモジュール群の共通ユーティリティ。

Why:
    merge-check関連の複数モジュール（review_checker、ai_review_checker等）で
    共通して使用する機能を一箇所にまとめ、重複を排除する。

What:
    - テキスト処理（truncation、code block stripping）
    - リポジトリ情報取得
    - 共通パターン（Issue参照、PRボディ品質）

Remarks:
    - 使用モジュール: review_checker.py, ai_review_checker.py, fix_verification_checker.py等
    - strip_code_blocksはコード例での誤検知防止に使用

Changelog:
    - silenvx/dekita#797: strip_code_blocks追加（コード例での誤検知防止）
    - silenvx/dekita#2406: PRボディ品質チェック関数追加
    - silenvx/dekita#2608: 段階的移行PRチェック追加
"""

import re
import subprocess

from lib.constants import TIMEOUT_MEDIUM


def truncate_body(body: str, max_length: int = 100) -> str:
    """Truncate body text for display.

    Args:
        body: The text to truncate.
        max_length: Maximum length before truncation. Default is 100.

    Returns:
        Truncated text with "..." suffix if longer than max_length.
    """
    if len(body) > max_length:
        return body[:max_length] + "..."
    return body


# Pattern to match fenced code blocks (```...```) and inline code (`...`)
# Used to strip code content before keyword detection to avoid false positives.
# See Issue #797: Copilot suggestions containing "-- Claude Code" in code examples
# were incorrectly detected as Claude Code comments.
CODE_BLOCK_PATTERN = re.compile(
    r"```[\s\S]*?```"  # Fenced code blocks (multiline)
    r"|"
    r"`[^`\n]+`",  # Inline code (single backticks, non-greedy, no newlines)
    re.MULTILINE,
)


def strip_code_blocks(text: str) -> str:
    """Remove code blocks and inline code from text.

    This prevents false positives when checking for keywords like "-- Claude Code"
    or "False positive" that may appear in code examples or suggestions.

    Args:
        text: The text to process.

    Returns:
        Text with code blocks and inline code removed.
    """
    return CODE_BLOCK_PATTERN.sub("", text)


def get_repo_owner_and_name() -> tuple[str, str] | None:
    """Get repository owner and name from gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "owner,name", "--jq", ".owner.login,.name"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return None
        lines = result.stdout.strip().split("\n")
        if len(lines) >= 2:
            return (lines[0], lines[1])
        return None
    except Exception:
        return None


# Issue reference pattern: #123 or Issue #123 or issue作成
#
# Design note: The pattern has two parts:
# 1. "#\d+" for issue numbers (e.g., "#123", "Issue #123")
# 2. "issue\s*を?\s*作成" for Japanese "Issue作成" phrases
#
# Why include "issue作成"?
# When someone writes "後でissue作成します", they're acknowledging the need for a follow-up
# Issue. While they haven't yet created it (no #XXX), they've explicitly stated the intention
# to do so. Including this pattern reduces false positives for remaining task detection.
# The alternative - requiring an actual Issue number - would block PRs where the person
# is already aware they need to create a follow-up Issue but hasn't done so yet.
ISSUE_REFERENCE_PATTERN = re.compile(r"#\d+|issue\s*を?\s*作成", re.IGNORECASE)

# Incremental migration keywords pattern
# Issue #2608: Detect PRs describing incremental/staged migration
#
# These patterns indicate that the PR is part of a multi-step migration
# and there are remaining tasks to be completed in future PRs.
# When detected, we require an Issue reference for follow-up tasks.
INCREMENTAL_KEYWORDS_PATTERNS = [
    r"段階的",  # "staged" / "incremental"
    r"第\d+段階",  # "stage N" / "phase N"
    r"後続タスク",  # "follow-up task"
    r"将来の.*移行",  # "future ... migration"
    r"今回は.*のみ",  # "only ... this time"
    r"残りは.*(?:移行|対応)",  # "rest will be migrated/handled"
]

# Pre-compiled pattern for efficiency
INCREMENTAL_KEYWORDS_PATTERN = re.compile("|".join(INCREMENTAL_KEYWORDS_PATTERNS), re.IGNORECASE)


def has_why_section(body: str | None) -> bool:
    """Check if body contains a "Why" section.

    Recognizes:
    - ## Why / ## なぜ / ## 背景 / ## 理由 / ## Motivation / ## Background
    - Why: / なぜ: / 背景: / 理由:
    - **Why** / **なぜ** / **背景** / **理由**
    """
    if not body:
        return False

    # Section headers (## Header or **Header**)
    section_patterns = [
        r"(?:^|\n)##?\s*(?:why|なぜ|背景|理由|motivation|background)\b",
        r"\*\*(?:why|なぜ|背景|理由|motivation|background)\*\*",
        r"(?:^|\n)(?:why|なぜ|背景|理由|motivation|background)\s*[:：]",
    ]

    for pattern in section_patterns:
        if re.search(pattern, body, re.IGNORECASE):
            return True

    return False


def has_reference(body: str | None) -> bool:
    """Check if body contains Issue/PR references.

    Recognizes:
    - #123 (Issue/PR number)
    - Closes #123 / Fixes #123 / Resolves #123
    - URL links to GitHub issues/PRs
    - 参照: / Refs: / Related:
    """
    if not body:
        return False

    # Issue/PR number reference
    if re.search(r"#\d+", body):
        return True

    # GitHub URL references
    if re.search(r"github\.com/[\w-]+/[\w-]+/(?:issues|pull)/\d+", body):
        return True

    # Reference section headers
    ref_patterns = [
        r"(?:^|\n)##?\s*(?:refs?|references?|参照|関連|related)\b",
        r"(?:^|\n)(?:refs?|references?|参照|関連|related)\s*[:：]",
    ]
    for pattern in ref_patterns:
        if re.search(pattern, body, re.IGNORECASE):
            return True

    return False


def check_body_quality(body: str | None) -> tuple[bool, list[str]]:
    """Check PR body quality.

    Returns:
        Tuple of (is_valid, missing_items)
    """
    missing = []

    if not has_why_section(body):
        missing.append("「なぜ」セクション（背景・動機）")

    if not has_reference(body):
        missing.append("参照（Issue番号 #XXX または関連リンク）")

    return len(missing) == 0, missing


def has_incremental_keywords(body: str | None) -> bool:
    """Check if body contains incremental migration keywords.

    Detects patterns indicating staged/phased migration:
    - 段階的 (incremental/staged)
    - 第N段階 (phase N)
    - 後続タスク (follow-up task)
    - 将来の...移行 (future ... migration)
    - 今回は...のみ (only ... this time)
    - 残りは...移行/対応 (rest will be migrated/handled)

    Args:
        body: PR body text to check.

    Returns:
        True if incremental keywords are found.
    """
    if not body:
        return False

    # コードブロック内のキーワードによる誤検知を避けるため、
    # 検索前にコードブロックを除去する
    body_without_code = strip_code_blocks(body)
    return bool(INCREMENTAL_KEYWORDS_PATTERN.search(body_without_code))


def check_incremental_pr(body: str | None) -> tuple[bool, str | None]:
    """Check if incremental PR has follow-up Issue reference.

    When PR body contains incremental migration keywords (段階的, 第N段階, etc.),
    we require an Issue reference for remaining tasks.

    Args:
        body: PR body text to check.

    Returns:
        Tuple of (is_valid, reason). is_valid is True if:
        - No incremental keywords are found, OR
        - Incremental keywords are found AND Issue reference exists
    """
    if not body:
        return True, None

    if not has_incremental_keywords(body):
        return True, None

    # Incremental keywords found - check for Issue reference
    if has_reference(body):
        return True, None

    return False, (
        "段階的移行PRには残タスクのIssue参照が必要です。\n\n"
        "**検出されたキーワード**: 段階的/第N段階/後続タスク/今回は...のみ 等\n\n"
        "**対処方法**:\n"
        '1. 残タスク用のIssueを作成: `gh issue create --title "残タスク: ..." --body "..."`\n'
        "2. PRボディにIssue番号を追加: `関連: #XXX（残タスク）`\n"
        '3. または `gh pr edit <PR番号> --body "..."` でPRボディを更新'
    )
