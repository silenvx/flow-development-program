#!/usr/bin/env python3
"""gh issue close時にAIレビューへの対応状況を確認してブロック。

Why:
    Issue作成後のAIレビューで改善提案が出ても、対応せずにクローズすると
    Issue品質が低下する。レビュー対応を強制することでIssue品質を維持する。

What:
    - gh issue closeコマンドを検出
    - IssueにAIレビューコメント（🤖 AI Review）があるか確認
    - レビュー後にIssue本文が更新されていなければブロック
    - スキップ環境変数（SKIP_REVIEW_RESPONSE）で回避可能

Remarks:
    - ブロック型フック
    - issue-ai-review.pyはレビュー実行、本フックはレビュー対応確認
    - コメントでの対応理由説明も有効な対応として扱う

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#1024: SKIP_REVIEW_RESPONSE環境変数サポート
"""

import json
import os
import re
import subprocess
from datetime import datetime

from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.results import make_block_result, print_continue_and_log_skip
from lib.session import create_hook_context, parse_hook_input
from lib.strings import extract_inline_skip_env, is_skip_env_enabled, strip_quoted_strings

SKIP_REVIEW_RESPONSE_ENV = "SKIP_REVIEW_RESPONSE"


def extract_issue_number(command: str) -> str | None:
    """Extract issue number from gh issue close command."""
    # Remove quoted strings to avoid false positives
    cmd = strip_quoted_strings(command)

    # Check if this is a gh issue close command
    if not re.search(r"gh\s+issue\s+close\b", cmd):
        return None

    # Extract all arguments after "gh issue close"
    match = re.search(r"gh\s+issue\s+close\s+(.+)", cmd)
    if not match:
        return None

    args = match.group(1)

    # Find issue number (with or without #) among the arguments
    for part in args.split():
        if part.startswith("-"):
            continue
        num_match = re.match(r"#?(\d+)$", part)
        if num_match:
            return num_match.group(1)

    return None


def get_ai_review_comment_time(issue_number: str) -> datetime | None:
    """Get the timestamp of AI Review comment if exists.

    Returns:
        datetime of AI Review comment, or None if not found.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                issue_number,
                "--json",
                "comments",
                "--jq",
                '.comments[] | select(.body | contains("🤖 AI Review")) | .createdAt',
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )

        if result.returncode != 0 or not result.stdout.strip():
            return None

        # Get the latest (newest) AI Review comment to ensure
        # edits happened after the most recent review
        timestamps = result.stdout.strip().split("\n")
        if timestamps:
            # Parse ISO format: 2025-12-20T12:05:59Z
            # Use the last timestamp (newest review)
            return datetime.fromisoformat(timestamps[-1].replace("Z", "+00:00"))

        return None

    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


def was_issue_edited_after(issue_number: str, after_time: datetime) -> bool:
    """Check if issue was updated after the given time.

    Uses issue's updated_at field to detect any activity (body edits,
    comments, label changes, etc.) after the AI Review.

    Note: This intentionally treats comments as valid responses,
    since the hook allows "explaining why a suggestion is not needed"
    via comments as a valid way to address AI Review feedback.
    """
    try:
        # Get issue updated_at timestamp directly
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/:owner/:repo/issues/{issue_number}",
                "--jq",
                ".updated_at",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )

        if result.returncode != 0:
            # API error, don't block
            return True

        if not result.stdout.strip():
            # No updated_at found, don't block
            return True

        try:
            updated_at = datetime.fromisoformat(result.stdout.strip().replace("Z", "+00:00"))
            # Issue was updated after AI Review comment
            return updated_at > after_time
        except ValueError:
            # Parse error, don't block
            return True

    except (subprocess.TimeoutExpired, OSError):
        # On error, don't block
        return True


def get_ai_review_suggestions(issue_number: str) -> list[str]:
    """Extract bullet point suggestions from AI Review comment.

    Parses the AI Review comment body and extracts lines starting with
    '- ' or '* ' as improvement suggestions. Returns up to 3 suggestions,
    each truncated to 100 characters.

    Note: This extracts bullet points as a heuristic for finding actionable
    suggestions. Not all bullet points may be actual suggestions, but this
    provides helpful context for the user.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                issue_number,
                "--json",
                "comments",
                "--jq",
                '.comments[] | select(.body | contains("🤖 AI Review")) | .body',
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )

        if result.returncode != 0 or not result.stdout.strip():
            return []

        body = result.stdout.strip()

        # Extract improvement suggestions (lines starting with - or *)
        suggestions = []
        for line in body.split("\n"):
            line = line.strip()
            if line.startswith("- ") or line.startswith("* "):
                # Skip very short suggestions (< 10 chars are likely not meaningful)
                if len(line) > 10:
                    suggestions.append(line[:100])  # Truncate long lines
                    if len(suggestions) >= 3:
                        break

        return suggestions

    except (subprocess.TimeoutExpired, OSError):
        return []


def main():
    """PreToolUse hook for Bash commands."""
    result = {"decision": "approve"}

    try:
        input_data = parse_hook_input()
        # Issue #2607: Create context for session_id logging
        ctx = create_hook_context(input_data)
        tool_name = input_data.get("tool_name", "")

        if tool_name != "Bash":
            print_continue_and_log_skip(
                "issue-review-response-check", f"not Bash: {tool_name}", ctx=ctx
            )
            return

        tool_input = input_data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Check if this is a gh issue close command
        issue_number = extract_issue_number(command)

        # Issue #1024: SKIP_REVIEW_RESPONSE environment variable support
        # Allows bypassing AI review response check for confirmed cases
        # Supports both exported env var and inline (SKIP_REVIEW_RESPONSE=1 gh issue close)
        if issue_number:
            # Check exported environment variable
            if is_skip_env_enabled(os.environ.get(SKIP_REVIEW_RESPONSE_ENV)):
                log_hook_execution(
                    "issue-review-response-check",
                    "skip",
                    f"SKIP_REVIEW_RESPONSE=1: Issue #{issue_number} のAIレビュー対応チェックをスキップ",
                )
                print(json.dumps(result))
                return
            # Check inline environment variable
            inline_value = extract_inline_skip_env(command, SKIP_REVIEW_RESPONSE_ENV)
            if is_skip_env_enabled(inline_value):
                log_hook_execution(
                    "issue-review-response-check",
                    "skip",
                    f"SKIP_REVIEW_RESPONSE=1: Issue #{issue_number} のAIレビュー対応チェックをスキップ（インライン）",
                )
                print(json.dumps(result))
                return
        if not issue_number:
            print_continue_and_log_skip(
                "issue-review-response-check", "no issue number found", ctx=ctx
            )
            return

        # Check for AI Review comment
        ai_review_time = get_ai_review_comment_time(issue_number)

        if not ai_review_time:
            # No AI Review, let it through
            log_hook_execution(
                "issue-review-response-check",
                "approve",
                f"AIレビューなし: Issue #{issue_number}",
            )
            print(json.dumps(result))
            return

        # Check if issue was edited after AI Review
        if was_issue_edited_after(issue_number, ai_review_time):
            log_hook_execution(
                "issue-review-response-check",
                "approve",
                f"AIレビュー後に編集あり: Issue #{issue_number}",
            )
            print(json.dumps(result))
            return

        # Issue has AI Review but was not edited - block
        suggestions = get_ai_review_suggestions(issue_number)
        suggestion_text = ""
        if suggestions:
            suggestion_text = "\n\n**AIレビューの改善提案例:**\n" + "\n".join(suggestions)

        reason = (
            f"⚠️ Issue #{issue_number} にはAIレビューコメントがありますが、"
            f"レビュー後にIssue本文が更新されていません。\n\n"
            f"**対応方法:**\n"
            f"1. `gh issue view {issue_number} --comments` でAIレビューを確認\n"
            f"2. 改善提案をIssue本文に反映（`gh issue edit {issue_number}`）\n"
            f"3. 対応不要な提案は、その理由をコメントに記載\n"
            f"4. その後、再度クローズを実行"
            f"{suggestion_text}"
        )
        result = make_block_result("issue-review-response-check", reason)

        log_hook_execution(
            "issue-review-response-check",
            "block",
            f"AIレビュー未対応: Issue #{issue_number}",
        )

    except Exception as e:
        # Don't block on errors - reset to approve
        result = {"decision": "approve"}
        log_hook_execution(
            "issue-review-response-check",
            "error",
            f"フックエラー: {e}",
        )

    print(json.dumps(result))


if __name__ == "__main__":
    main()
