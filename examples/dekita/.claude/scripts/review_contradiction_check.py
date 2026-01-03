#!/usr/bin/env python3
"""AIレビューコメントの矛盾可能性を検出する。

Why:
    同一ファイルの近接行に複数コメントがある場合、
    矛盾の可能性を警告しレビュー品質を向上させるため。

What:
    - detect_potential_contradictions(): 矛盾候補を検出

Remarks:
    - 10行以内の近接コメントを矛盾候補として検出
    - 意味解析は行わず、人間のレビュー用にフラグのみ
    - ci-monitor.pyから呼び出される

Changelog:
    - silenvx/dekita#1399: 矛盾コメント検出機能を追加
    - silenvx/dekita#1596: 同一バッチ内の近接検出を追加
"""

from __future__ import annotations

from typing import Any

# Distance threshold for "close" lines (comments less than this many lines apart may contradict)
PROXIMITY_THRESHOLD = 10


def detect_potential_contradictions(
    new_comments: list[dict[str, Any]],
    previous_comments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect potential contradictions between new and previous review comments.

    Checks for comments on the same file within close line proximity.
    Does NOT attempt semantic analysis - only flags for human review.

    Issue #1596: Always detects proximity within new_comments (same-batch),
    and additionally checks against previous_comments if provided (cross-batch).

    Args:
        new_comments: List of new review comments with 'path', 'line', 'body' keys.
        previous_comments: List of previous review comments with same structure.

    Returns:
        List of potential contradiction warnings, each containing:
        - file: The file path
        - prev_line: Line number of previous comment (or first comment in batch)
        - new_line: Line number of new comment (or second comment in batch)
        - prev_body: Truncated body of previous comment (max 100 chars)
        - new_body: Truncated body of new comment (max 100 chars)
        - prev_truncated: True if prev_body was truncated
        - new_truncated: True if new_body was truncated
        - same_batch: True if both comments are from the same batch (first review)
    """
    warnings: list[dict[str, Any]] = []

    # Issue #1596: Always check for proximity within the current batch
    warnings.extend(_detect_within_batch(new_comments))

    # If no previous comments, skip cross-batch check
    if not previous_comments:
        return warnings

    for new in new_comments:
        new_path = new.get("path")
        new_line = new.get("line")
        new_body = new.get("body", "")

        if not new_path:
            continue

        for prev in previous_comments:
            prev_path = prev.get("path")
            prev_line = prev.get("line")
            prev_body = prev.get("body", "")

            if prev_path != new_path:
                continue

            # Check line proximity
            if new_line is not None and prev_line is not None:
                distance = abs(new_line - prev_line)
                if distance < PROXIMITY_THRESHOLD:
                    warnings.append(
                        {
                            "file": new_path,
                            "prev_line": prev_line,
                            "new_line": new_line,
                            "prev_body": prev_body[:100],
                            "new_body": new_body[:100],
                            "prev_truncated": len(prev_body) > 100,
                            "new_truncated": len(new_body) > 100,
                            "same_batch": False,
                        }
                    )

    return warnings


def _detect_within_batch(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect potential contradictions within a single batch of comments.

    Issue #1596: For first review batch where previous_comments is empty,
    check if multiple comments target the same file at close lines.

    Args:
        comments: List of comments to check for internal proximity.

    Returns:
        List of warnings for comments at close lines within the same file.
    """
    warnings: list[dict[str, Any]] = []

    # Compare each pair of comments (avoid duplicates by using i < j)
    for i, first in enumerate(comments):
        first_path = first.get("path")
        first_line = first.get("line")
        first_body = first.get("body", "")

        if not first_path or first_line is None:
            continue

        for j in range(i + 1, len(comments)):
            second = comments[j]
            second_path = second.get("path")
            second_line = second.get("line")
            second_body = second.get("body", "")

            if second_path != first_path or second_line is None:
                continue

            distance = abs(first_line - second_line)
            if distance < PROXIMITY_THRESHOLD:
                warnings.append(
                    {
                        "file": first_path,
                        "prev_line": first_line,
                        "new_line": second_line,
                        "prev_body": first_body[:100],
                        "new_body": second_body[:100],
                        "prev_truncated": len(first_body) > 100,
                        "new_truncated": len(second_body) > 100,
                        "same_batch": True,
                    }
                )

    return warnings


def format_contradiction_warnings(warnings: list[dict[str, Any]]) -> str:
    """Format contradiction warnings for display.

    Args:
        warnings: List of warning dicts from detect_potential_contradictions.

    Returns:
        Formatted warning message string, or empty string if no warnings.
    """
    if not warnings:
        return ""

    lines = ["⚠️ 同一ファイル・近接行への複数指摘を検出:"]

    for warning in warnings:
        prev_body = warning["prev_body"]
        new_body = warning["new_body"]
        # Only add ellipsis if the body was actually truncated
        prev_suffix = "..." if warning.get("prev_truncated", False) else ""
        new_suffix = "..." if warning.get("new_truncated", False) else ""
        same_batch = warning.get("same_batch", False)

        lines.append(f"   ファイル: {warning['file']}")
        if same_batch:
            # Issue #1596: First review batch - both comments are new
            lines.append(f'   指摘1 (line {warning["prev_line"]}): "{prev_body}{prev_suffix}"')
            lines.append(f'   指摘2 (line {warning["new_line"]}): "{new_body}{new_suffix}"')
            lines.append("   → 同一バッチ内で近接行に複数指摘。整合性を確認してください。")
        else:
            lines.append(f'   前回指摘 (line {warning["prev_line"]}): "{prev_body}{prev_suffix}"')
            lines.append(f'   今回指摘 (line {warning["new_line"]}): "{new_body}{new_suffix}"')
            lines.append("   → 矛盾の可能性あり。人間の判断を優先してください。")
        lines.append("")

    return "\n".join(lines)
