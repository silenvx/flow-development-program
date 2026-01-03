#!/usr/bin/env python3
"""gh issue close時に未完了チェックボックスを検出してブロック。

Why:
    Issue本文にタスクリスト（チェックボックス）がある場合、
    未完了項目があるままクローズすると作業漏れが発生する。
    部分完了でのクローズを防止する。

What:
    - gh issue closeコマンドを検出
    - Issue本文からチェックボックスを解析
    - 未チェック項目があればブロック
    - スキップ環境変数（SKIP_INCOMPLETE_CHECK）で回避可能

Remarks:
    - ブロック型フック
    - issue-review-response-checkはAIレビュー対応確認、本フックはタスク完了確認

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#956: スキップ環境変数の値検証を統一
"""

import json
import os
import re
import subprocess

from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.results import make_block_result, print_continue_and_log_skip
from lib.session import create_hook_context, parse_hook_input
from lib.strings import extract_inline_skip_env, is_skip_env_enabled, strip_quoted_strings


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


def get_issue_body(issue_number: str) -> str | None:
    """Fetch issue body from GitHub."""
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                issue_number,
                "--json",
                "body",
                "--jq",
                ".body",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )

        if result.returncode != 0:
            return None

        return result.stdout

    except (subprocess.TimeoutExpired, OSError):
        return None


def parse_checkboxes(body: str) -> tuple[list[str], list[str]]:
    """Parse checkboxes from issue body.

    Returns:
        Tuple of (checked_items, unchecked_items)
    """
    checked: list[str] = []
    unchecked: list[str] = []

    # Match checkbox patterns: - [ ] or - [x] or - [X]
    # Also match * [ ] variants
    checkbox_pattern = re.compile(r"^[\s]*[-*]\s+\[([ xX])\]\s+(.+)$", re.MULTILINE)

    for match in checkbox_pattern.finditer(body):
        state = match.group(1)
        text = match.group(2).strip()

        # Truncate long text
        if len(text) > 80:
            text = text[:77] + "..."

        if state.lower() == "x":
            checked.append(text)
        else:
            unchecked.append(text)

    return checked, unchecked


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
                "issue-incomplete-close-check", f"not Bash: {tool_name}", ctx=ctx
            )
            return

        tool_input = input_data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Check for skip environment variable (Issue #956: consistent value validation)
        # Supports both exported and inline env vars: SKIP_INCOMPLETE_CHECK=1 gh issue close
        skip_env_name = "SKIP_INCOMPLETE_CHECK"
        if is_skip_env_enabled(os.environ.get(skip_env_name)):
            log_hook_execution(
                "issue-incomplete-close-check",
                "approve",
                "SKIP_INCOMPLETE_CHECK でスキップ（環境変数）",
            )
            print(json.dumps(result))
            return

        inline_value = extract_inline_skip_env(command, skip_env_name)
        if is_skip_env_enabled(inline_value):
            log_hook_execution(
                "issue-incomplete-close-check",
                "approve",
                "SKIP_INCOMPLETE_CHECK でスキップ（インライン）",
            )
            print(json.dumps(result))
            return

        # Check if this is a gh issue close command
        issue_number = extract_issue_number(command)
        if not issue_number:
            print_continue_and_log_skip(
                "issue-incomplete-close-check", "no issue number found", ctx=ctx
            )
            return

        # Fetch issue body
        body = get_issue_body(issue_number)
        if not body:
            # Can't fetch body, don't block
            log_hook_execution(
                "issue-incomplete-close-check",
                "approve",
                f"Issue #{issue_number} の本文取得失敗",
            )
            print(json.dumps(result))
            return

        # Parse checkboxes
        checked, unchecked = parse_checkboxes(body)

        # No checkboxes at all - let it through
        if not checked and not unchecked:
            log_hook_execution(
                "issue-incomplete-close-check",
                "approve",
                f"Issue #{issue_number} にチェックボックスなし",
            )
            print(json.dumps(result))
            return

        # All checkboxes are checked - let it through
        if not unchecked:
            log_hook_execution(
                "issue-incomplete-close-check",
                "approve",
                f"Issue #{issue_number} の全項目完了 ({len(checked)}件)",
            )
            print(json.dumps(result))
            return

        # There are unchecked items - block
        unchecked_list = "\n".join(f"- [ ] {item}" for item in unchecked[:5])
        if len(unchecked) > 5:
            unchecked_list += f"\n... 他 {len(unchecked) - 5} 件"

        reason = (
            f"Issue #{issue_number} に未完了項目があります。\n\n"
            f"**未完了 ({len(unchecked)}件):**\n{unchecked_list}\n\n"
            f"**完了済み ({len(checked)}件)**\n\n"
            f"**対応方法:**\n"
            f"1. 残り項目を完了してからクローズ\n"
            f"2. 別Issueに分割: 残り項目を新Issueとして作成してからクローズ\n"
            f"3. 対応不要: コメントで理由を説明してからクローズ\n\n"
            f"**スキップ方法（確認済みの場合）:**\n"
            f"```\nSKIP_INCOMPLETE_CHECK=1 gh issue close {issue_number}\n```"
        )
        result = make_block_result("issue-incomplete-close-check", reason)

        log_hook_execution(
            "issue-incomplete-close-check",
            "block",
            f"Issue #{issue_number} に未完了項目 {len(unchecked)}件",
        )

    except Exception as e:
        # Don't block on errors - reset to approve
        result = {"decision": "approve"}
        log_hook_execution(
            "issue-incomplete-close-check",
            "error",
            f"フックエラー: {e}",
        )

    print(json.dumps(result))


if __name__ == "__main__":
    main()
