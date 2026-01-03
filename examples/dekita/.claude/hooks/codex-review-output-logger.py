#!/usr/bin/env python3
"""Codex CLIレビュー出力をパースしてレビューコメントを記録する。

Why:
    Codex CLIレビューの結果を分析することで、レビュー品質の追跡や
    よくある指摘パターンの特定ができる。

What:
    - codex review出力からレビューコメントを抽出
    - 個別コメントをreview-quality.jsonlに記録
    - 実行メタデータをcodex-reviews.jsonlに記録
    - コメント数ゼロならpass、あればfailとして記録

State:
    - writes: .claude/logs/metrics/review-quality.jsonl
    - writes: .claude/logs/metrics/codex-reviews.jsonl

Remarks:
    - 記録型フック（ブロックしない、メトリクス記録）
    - PostToolUse:Bashで発火（codex reviewコマンド）
    - JSON出力/行単位出力の両方をパース対応
    - exit_code != 0の場合もerrorとして記録

Changelog:
    - silenvx/dekita#610: レビュー品質追跡システム
    - silenvx/dekita#1233: 実行メタデータのログ記録
    - silenvx/dekita#2607: セッションID対応
"""

import json
import re
import sys
import time
from typing import Any

from common import log_review_comment
from lib.execution import log_hook_execution
from lib.git import get_current_branch
from lib.github import get_pr_number_for_branch
from lib.hook_input import get_tool_result
from lib.results import print_continue_and_log_skip
from lib.review import estimate_category, log_codex_review_execution
from lib.session import create_hook_context, parse_hook_input
from lib.strings import strip_quoted_strings


def is_codex_review_command(command: str) -> bool:
    """Check if command is a codex review command."""
    if not command.strip():
        return False
    stripped_command = strip_quoted_strings(command)
    return bool(re.search(r"codex\s+review\b", stripped_command))


def extract_tokens_used(output: str) -> int | None:
    """Extract tokens used count from Codex CLI output.

    Looks for patterns like:
    - "tokens used: 6,356"
    - "tokens used: 1234"

    Args:
        output: The stdout from codex review command

    Returns:
        Number of tokens used, or None if not found
    """
    # Match "tokens used: X,XXX" or "tokens used: XXX" (with optional commas)
    match = re.search(r"tokens\s+used:\s*([\d,]+)", output, re.IGNORECASE)
    if match:
        # Remove commas and convert to int
        tokens_str = match.group(1).replace(",", "")
        try:
            return int(tokens_str)
        except ValueError:
            return None
    return None


def extract_base_branch(command: str) -> str | None:
    """Extract base branch from codex review command.

    Looks for patterns like:
    - "codex review --base main"
    - "codex review --base origin/main"

    Args:
        command: The codex review command

    Returns:
        Base branch name, or None if not found
    """
    match = re.search(r"--base\s+(\S+)", command)
    if match:
        return match.group(1)
    return None


def parse_file_line_comment(line: str) -> dict[str, Any] | None:
    """Parse a line that may contain file:line: comment format.

    Supported formats:
    - file.ts:10: message
    - file.ts:10:5: message (with column)
    - src/path/file.tsx (line 25): message
    - file.ts#L10: message
    """
    # Format: file:line: message or file:line:column: message
    match = re.match(r"^([^:]+):(\d+)(?::\d+)?:\s*(.+)$", line)
    if match:
        return {
            "file_path": match.group(1).strip(),
            "line_number": int(match.group(2)),
            "body": match.group(3).strip(),
        }

    # Format: file (line N): message
    match = re.match(r"^(.+?)\s*\(line\s+(\d+)\):\s*(.+)$", line, re.IGNORECASE)
    if match:
        return {
            "file_path": match.group(1).strip(),
            "line_number": int(match.group(2)),
            "body": match.group(3).strip(),
        }

    # Format: file#LN: message
    match = re.match(r"^(.+?)#L(\d+):\s*(.+)$", line)
    if match:
        return {
            "file_path": match.group(1).strip(),
            "line_number": int(match.group(2)),
            "body": match.group(3).strip(),
        }

    return None


def _extract_comment_body(item: dict[str, Any]) -> str | None:
    """Extract comment body from various possible keys."""
    for key in ("body", "message", "comment", "text"):
        if key in item and item[key]:
            return str(item[key])
    return None


def _has_comment_content(item: dict[str, Any]) -> bool:
    """Check if item has comment content in any supported key."""
    return _extract_comment_body(item) is not None


def parse_json_output(output: str) -> list[dict[str, Any]]:
    """Try to parse output as JSON containing review comments."""
    comments = []
    try:
        data = json.loads(output)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and _has_comment_content(item):
                    comments.append(
                        {
                            "file_path": item.get("file", item.get("path")),
                            "line_number": item.get("line"),
                            "body": _extract_comment_body(item) or "",
                        }
                    )
        elif isinstance(data, dict):
            # Handle single comment or nested structure
            if "comments" in data and isinstance(data["comments"], list):
                return parse_json_output(json.dumps(data["comments"]))
            elif _has_comment_content(data):
                comments.append(
                    {
                        "file_path": data.get("file", data.get("path")),
                        "line_number": data.get("line"),
                        "body": _extract_comment_body(data) or "",
                    }
                )
    except json.JSONDecodeError:
        # Not JSON format - will fall back to line-by-line parsing
        pass
    return comments


def parse_codex_review_output(output: str) -> list[dict[str, Any]]:
    """Parse Codex CLI review output to extract comments.

    Args:
        output: The stdout from codex review command

    Returns:
        List of dicts with file_path, line_number, body keys
    """
    comments = []

    # First, try JSON parsing
    json_comments = parse_json_output(output)
    if json_comments:
        return json_comments

    # Fall back to line-by-line parsing
    current_comment: dict[str, Any] | None = None

    for line in output.split("\n"):
        line = line.strip()
        if not line:
            # Empty line may end a multi-line comment
            if current_comment and current_comment.get("body"):
                comments.append(current_comment)
                current_comment = None
            continue

        # Try to parse as a new comment
        parsed = parse_file_line_comment(line)
        if parsed:
            # Save previous comment if exists
            if current_comment and current_comment.get("body"):
                comments.append(current_comment)
            current_comment = parsed
        elif current_comment:
            # Append to current comment body (multi-line comment)
            current_comment["body"] = current_comment.get("body", "") + " " + line

    # Don't forget the last comment
    if current_comment and current_comment.get("body"):
        comments.append(current_comment)

    return comments


def main():
    """PostToolUse hook for Bash commands.

    Parses codex review output and logs:
    1. Individual comments to review-quality.jsonl (if any)
    2. Review execution metadata to codex-reviews.jsonl (always)
    """
    result = {"continue": True}

    try:
        input_data = parse_hook_input()
        # Issue #2607: Create context for session_id logging
        ctx = create_hook_context(input_data)
        tool_input = input_data.get("tool_input", {})
        tool_result = get_tool_result(input_data) or {}

        command = tool_input.get("command", "")

        # Only process codex review commands
        if not is_codex_review_command(command):
            print_continue_and_log_skip(
                "codex-review-output-logger", "not a codex review command", ctx=ctx
            )
            return

        stdout = tool_result.get("stdout", "")
        exit_code = tool_result.get("exit_code", 0)

        # Extract metadata from command and output
        base_branch = extract_base_branch(command)
        tokens_used = extract_tokens_used(stdout)
        branch = get_current_branch()

        # Don't process further if command failed, but still log the execution
        if exit_code != 0:
            log_codex_review_execution(
                branch=branch,
                base=base_branch,
                verdict="error",
                comment_count=0,
                tokens_used=tokens_used,
                exit_code=exit_code,
            )
            log_hook_execution("codex-review-output-logger", "approve")
            print(json.dumps(result))
            return

        # Parse the output for comments
        comments = parse_codex_review_output(stdout)

        # Determine verdict: pass if no comments, fail if comments found
        verdict = "pass" if not comments else "fail"

        # Always log review execution (Issue #1233)
        log_codex_review_execution(
            branch=branch,
            base=base_branch,
            verdict=verdict,
            comment_count=len(comments),
            tokens_used=tokens_used,
            exit_code=exit_code,
        )

        # Log individual comments to review-quality.jsonl
        if comments:
            pr_number = None
            if branch:
                pr_number = get_pr_number_for_branch(branch)

            # Log each comment with unique ID per execution (nanosecond precision)
            execution_ts = time.time_ns()
            for i, comment in enumerate(comments):
                body = comment.get("body", "")
                category = estimate_category(body)
                log_review_comment(
                    pr_number=pr_number or "unknown",
                    comment_id=f"codex-cli-{execution_ts}-{i}",  # Unique ID per execution
                    reviewer="codex_cli",
                    category=category,
                    file_path=comment.get("file_path"),
                    line_number=comment.get("line_number"),
                    body_preview=body[:200] if body else None,
                )

            result["systemMessage"] = f"Codex CLI review: {len(comments)} comment(s) logged"
        else:
            result["systemMessage"] = "Codex CLI review: pass (no issues found)"

    except Exception as e:
        # Hook failure should not block Claude Code
        # Log error but continue
        print(f"[codex-review-output-logger] Error: {e}", file=sys.stderr)

    log_hook_execution("codex-review-output-logger", "approve")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
