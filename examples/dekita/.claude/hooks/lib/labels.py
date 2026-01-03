#!/usr/bin/env python3
"""gh CLIコマンドからのラベル抽出・分析ユーティリティを提供する。

Why:
    Issue/PR作成時のラベル検証・優先度チェックのため、
    コマンドからラベルを正確に抽出する必要がある。

What:
    - extract_labels_from_command(): --labelオプションからラベル抽出
    - has_priority_label(): P0-P3優先度ラベルの有無を判定
    - suggest_labels_from_text(): タイトル/本文からラベルを提案

Remarks:
    - shlex.split使用で堅牢なパース（クォート、エスケープ対応）
    - カンマ区切りラベル（--label="bug,P1"）にも対応
    - priority:P0形式も認識

Changelog:
    - silenvx/dekita#1957: 複数実装からDRY原則で統合
"""

import re
import shlex


def extract_labels_from_command(command: str) -> list[str]:
    """Extract all label values from gh issue/pr create command.

    Uses shlex.split() for robust parsing of command-line arguments.
    This handles edge cases better than regex:
    - Properly handles quoted strings with spaces
    - Handles escaped characters
    - Handles --label=value format

    Args:
        command: The command string (e.g., "gh issue create --label bug --label P1")

    Returns:
        List of label values. Empty list if no labels or parse error.
        Labels are returned as raw values (not split by comma).

    Examples:
        >>> extract_labels_from_command('gh issue create --label "bug"')
        ['bug']
        >>> extract_labels_from_command('gh issue create --label bug --label P1')
        ['bug', 'P1']
        >>> extract_labels_from_command('gh issue create --label="enhancement,P2"')
        ['enhancement,P2']
    """
    labels = []
    try:
        tokens = shlex.split(command)
    except ValueError:
        return labels

    i = 0
    while i < len(tokens):
        token = tokens[i]

        # --label value or -l value
        if token in ("--label", "-l") and i + 1 < len(tokens):
            labels.append(tokens[i + 1])
            i += 2
            continue

        # --label=value
        if token.startswith("--label="):
            labels.append(token[len("--label=") :])
            i += 1
            continue

        # -l=value
        if token.startswith("-l="):
            labels.append(token[len("-l=") :])
            i += 1
            continue

        i += 1

    return labels


def split_comma_separated_labels(labels: list[str]) -> list[str]:
    """Split comma-separated labels into individual labels.

    Args:
        labels: List of label values (may contain comma-separated values)

    Returns:
        List of individual labels with whitespace stripped.

    Examples:
        >>> split_comma_separated_labels(['bug,P1', 'enhancement'])
        ['bug', 'P1', 'enhancement']
        >>> split_comma_separated_labels(['tracking, backend'])
        ['tracking', 'backend']
    """
    result = []
    for label_value in labels:
        for label in label_value.split(","):
            label = label.strip()
            if label:
                result.append(label)
    return result


# CUSTOMIZE: Default priority labels (modify if your project uses different priority labels)
DEFAULT_PRIORITY_LABELS = {"P0", "P1", "P2", "P3"}


def extract_priority_from_labels(
    labels: list[str],
    priority_labels: set[str] | None = None,
) -> str | None:
    """Extract highest priority label from a list of labels.

    Checks labels (including comma-separated values) for priority labels.
    Also recognizes "priority:P0" format.

    Note: This function is designed for P0-P3 priority system with fixed ordering.
    The priority_labels parameter is used to filter which of P0-P3 to check,
    not to define entirely custom priority labels.

    Args:
        labels: List of label values from command.
        priority_labels: Subset of P0-P3 to check. Defaults to {"P0", "P1", "P2", "P3"}.
            Use to filter which priorities are relevant (e.g., {"P0", "P1", "P2"}
            to ignore P3).

    Returns:
        Highest priority found ("P0" > "P1" > "P2" > "P3"), or None if no priority label.
        Returns None if the found priority is not in P0-P3.

    Examples:
        >>> extract_priority_from_labels(['bug', 'P1'])
        'P1'
        >>> extract_priority_from_labels(['bug,P0', 'P2'])
        'P0'
        >>> extract_priority_from_labels(['priority:P1'])
        'P1'
        >>> extract_priority_from_labels(['P2'], priority_labels={'P0', 'P1'}) is None
        True
    """
    if priority_labels is None:
        priority_labels = DEFAULT_PRIORITY_LABELS

    # Track found priorities
    found_priorities = set()

    for label_value in labels:
        # Split by comma for combined labels
        for label in label_value.split(","):
            label = label.strip().upper()

            # Direct match (P0, P1, etc.)
            if label in {p.upper() for p in priority_labels}:
                found_priorities.add(label)

            # priority:P0 format
            if label.startswith("PRIORITY:"):
                priority_part = label[len("PRIORITY:") :]
                if priority_part in {p.upper() for p in priority_labels}:
                    found_priorities.add(priority_part)

    # Return highest priority (P0 > P1 > P2 > P3)
    for priority in ["P0", "P1", "P2", "P3"]:
        if priority in found_priorities:
            return priority

    return None


def has_priority_label(
    labels: list[str],
    priority_labels: set[str] | None = None,
) -> bool:
    """Check if any label contains a priority label.

    Args:
        labels: List of label values from command.
        priority_labels: Set of valid priority labels. Defaults to {"P0", "P1", "P2", "P3"}.

    Returns:
        True if a priority label is found.
    """
    return extract_priority_from_labels(labels, priority_labels) is not None


# CUSTOMIZE: Label suggestion patterns (modify based on your project's labels)
# Each entry: (pattern_keywords, suggested_label, description)
# Note: Avoid overly broad keywords like "問題" (could be general task), "追加" (could be doc/test),
#       "整理" (could be non-code). Use specific compound words instead.
LABEL_SUGGESTION_PATTERNS: list[tuple[list[str], str, str]] = [
    # Bug-related (removed "問題" - too broad)
    (["バグ", "bug", "エラー", "error", "不具合", "動かない", "fix"], "bug", "バグ報告"),
    # Enhancement/Feature (removed "追加" - too broad, added "機能追加", "新機能")
    (
        ["機能", "機能追加", "新機能", "新規", "feature", "feat", "enhancement", "改善", "拡張"],
        "enhancement",
        "新機能・改善",
    ),
    # Documentation (removed "doc" - matches "production", "docker" etc. Use longer forms)
    (
        ["ドキュメント", "document", "readme", "説明", "文書", "documentation"],
        "documentation",
        "ドキュメント",
    ),
    # Refactoring (replaced "整理" with "コード整理", "コードの整理", "実装の整理")
    (
        [
            "リファクタ",
            "refactor",
            "cleanup",
            "リファクタリング",
            "コード整理",
            "コードの整理",
            "実装の整理",
        ],
        "refactor",
        "リファクタリング",
    ),
]


def suggest_labels_from_text(
    title: str,
    body: str | None = None,
) -> list[tuple[str, str]]:
    """Suggest labels based on issue title and body content.

    Analyzes the text for keywords and suggests appropriate labels.
    Uses regex for performance when searching multiple keywords.

    Args:
        title: Issue title.
        body: Issue body (optional).

    Returns:
        List of (label, description) tuples for suggested labels.
        Empty list if no suggestions.

    Examples:
        >>> suggest_labels_from_text("fix: バグを修正")
        [('bug', 'バグ報告')]
        >>> suggest_labels_from_text("feat: 新機能追加", "ドキュメントも更新")
        [('enhancement', '新機能・改善'), ('documentation', 'ドキュメント')]
    """
    combined_text = (title or "").lower()
    if body:
        combined_text += " " + body.lower()

    suggestions: list[tuple[str, str]] = []
    seen_labels: set[str] = set()

    for keywords, label, description in LABEL_SUGGESTION_PATTERNS:
        if label in seen_labels:
            continue

        # パフォーマンス向上のため、キーワードを正規表現にまとめて検索
        pattern = "|".join(re.escape(k.lower()) for k in keywords)
        if re.search(pattern, combined_text):
            suggestions.append((label, description))
            seen_labels.add(label)

    return suggestions


def _extract_arg_from_command(command: str, long_opt: str, short_opt: str) -> str | None:
    """Extract argument value from a command string.

    Args:
        command: The command string.
        long_opt: Long option name (e.g., "--title").
        short_opt: Short option name (e.g., "-t").

    Returns:
        Argument value or None if not found.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None

    i = 0
    while i < len(tokens):
        token = tokens[i]

        if token in (long_opt, short_opt) and i + 1 < len(tokens):
            return tokens[i + 1]

        if token.startswith(f"{long_opt}="):
            return token[len(f"{long_opt}=") :]

        if token.startswith(f"{short_opt}="):
            return token[len(f"{short_opt}=") :]

        i += 1

    return None


def extract_title_from_command(command: str) -> str | None:
    """Extract --title value from gh issue create command.

    Args:
        command: The command string.

    Returns:
        Title value or None if not found.
    """
    return _extract_arg_from_command(command, "--title", "-t")


def extract_body_from_command(command: str) -> str | None:
    """Extract --body value from gh issue create command.

    Args:
        command: The command string.

    Returns:
        Body value or None if not found.
    """
    return _extract_arg_from_command(command, "--body", "-b")
