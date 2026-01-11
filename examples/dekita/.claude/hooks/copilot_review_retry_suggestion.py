#!/usr/bin/env python3
"""Copilot reviewの繰り返しエラー時にPR作り直しを提案する。

Why:
    Copilot reviewは特定の状況でエラーを返し続けることがあり、
    PRを作り直すことで解決する場合がある。無駄なリトライを防ぐ。

What:
    - Copilot reviewエラーを検出・カウント
    - 閾値を超えたらPR作り直しを提案
    - PR切り替え時にカウンタをリセット

State:
    - writes: {TMPDIR}/claude-hooks/copilot-review-errors-{session}.json

Remarks:
    - 提案型フック（ブロックしない、systemMessageで提案）
    - PostToolUse:Bashで発火
    - エラー閾値は3回（ERROR_THRESHOLD）
    - PR切り替え時にカウンタ自動リセット
    - 成功時もカウンタリセット

Changelog:
    - silenvx/dekita#544: フック追加
    - silenvx/dekita#563: セッションID取得をctx経由に統一
"""

import json
import re
import tempfile
from pathlib import Path

from lib.execution import log_hook_execution
from lib.hook_input import get_tool_result
from lib.results import print_continue_and_log_skip
from lib.session import HookContext, create_hook_context, parse_hook_input

# Tracking directory for session files (consistent with other hooks)
TRACKING_DIR = Path(tempfile.gettempdir()) / "claude-hooks"
ERROR_THRESHOLD = 3  # Suggest after this many consecutive errors


def get_error_tracking_file(ctx: HookContext) -> Path:
    """Get the error tracking file path for the current session.

    Uses ctx.get_session_id() for consistent session identification
    across all hooks (Issue #563).
    """
    session_id = ctx.get_session_id()
    return TRACKING_DIR / f"copilot-review-errors-{session_id}.json"


def is_copilot_review_check(command: str, stdout: str) -> bool:
    """Check if command is checking Copilot review status.

    Args:
        command: The bash command string.
        stdout: The command output.

    Returns:
        True if this is a Copilot review status check.
    """
    # Check for gh pr checks or gh api commands related to reviews
    if re.search(r"gh\s+pr\s+checks\b", command):
        return True
    if re.search(r"gh\s+api.*pulls.*reviews", command):
        return True
    if re.search(r"gh\s+api.*requested_reviewers", command):
        return True
    # ci-monitor.py output containing Copilot status (both error and success)
    if "Copilot" in stdout:
        return True
    return False


def has_copilot_review_error(stdout: str, stderr: str) -> bool:
    """Check if output indicates Copilot review error.

    Args:
        stdout: Command stdout.
        stderr: Command stderr.

    Returns:
        True if Copilot review error is detected.
    """
    combined = stdout + stderr
    # Known error patterns
    error_patterns = [
        r"Copilot encountered an error",
        r"Copilot.*unable to review",
        r"review.*error.*Copilot",
        r"Copilot.*failed",
    ]
    for pattern in error_patterns:
        if re.search(pattern, combined, re.IGNORECASE):
            return True
    return False


def load_error_count(ctx: HookContext) -> dict:
    """Load error tracking data from session file."""
    try:
        tracking_file = get_error_tracking_file(ctx)
        if tracking_file.exists():
            return json.loads(tracking_file.read_text())
    except Exception:
        # Silently ignore file read/parse errors and return default
        pass
    return {"count": 0, "last_pr": None}


def save_error_count(ctx: HookContext, data: dict) -> None:
    """Save error tracking data to session file."""
    try:
        TRACKING_DIR.mkdir(parents=True, exist_ok=True)
        tracking_file = get_error_tracking_file(ctx)
        tracking_file.write_text(json.dumps(data))
    except Exception:
        # Silently ignore file write errors (non-critical)
        pass


def extract_pr_number(command: str) -> str | None:
    """Extract PR number from command if present."""
    # Match patterns like: pulls/123, pull/123, pr 123, pr checks 123
    # Also handles spaceless patterns like pull123 (edge case)
    match = re.search(r"(?:pulls?[/\s]?|pr\s+(?:checks\s+)?)(\d+)", command, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def main():
    """
    PostToolUse hook for Bash commands.

    Tracks Copilot review errors and suggests PR recreation after repeated failures.
    """
    result = {}

    try:
        input_data = parse_hook_input()

        ctx = create_hook_context(input_data)
        tool_name = input_data.get("tool_name", "")

        if tool_name != "Bash":
            print_continue_and_log_skip(
                "copilot-review-retry-suggestion", f"not Bash: {tool_name}", ctx=ctx
            )
            return

        tool_input = input_data.get("tool_input", {})
        tool_result = get_tool_result(input_data) or {}
        command = tool_input.get("command", "")
        stdout = tool_result.get("stdout", "")
        stderr = tool_result.get("stderr", "")

        # Check if this is a Copilot review check
        if not is_copilot_review_check(command, stdout):
            print_continue_and_log_skip(
                "copilot-review-retry-suggestion", "not a Copilot review check", ctx=ctx
            )
            return

        # Check if there's a Copilot review error
        if has_copilot_review_error(stdout, stderr):
            # Track the error
            data = load_error_count(ctx)
            pr_num = extract_pr_number(command)

            # Reset counter if switching to a different PR
            if pr_num and data.get("last_pr") and pr_num != data["last_pr"]:
                data["count"] = 0

            data["count"] += 1
            if pr_num:
                data["last_pr"] = pr_num
            save_error_count(ctx, data)

            log_hook_execution(
                "copilot-review-retry-suggestion",
                "approve",
                f"Copilotレビューエラー検出: {data['count']}回目",
            )

            # Suggest PR recreation after threshold
            if data["count"] >= ERROR_THRESHOLD:
                pr_close_cmd = (
                    f"gh pr close {data['last_pr']}"
                    if data.get("last_pr")
                    else "gh pr close <PR番号>"
                )
                result["systemMessage"] = (
                    f"⚠️ **Copilot reviewが{data['count']}回連続でエラーを返しています**\n\n"
                    "このエラーはPRを作り直すことで解決する場合があります:\n\n"
                    "```bash\n"
                    "# 1. 現在のPRをクローズ\n"
                    f"{pr_close_cmd}\n\n"
                    "# 2. 新しいPRを作成（同じブランチから）\n"
                    'gh pr create --title "..." --body "..."\n'
                    "```\n\n"
                    "💡 PR作り直し後、Copilot reviewが正常に動作することがあります。"
                )
        else:
            # Reset counter on successful check (no error)
            data = load_error_count(ctx)
            if data["count"] > 0:
                data["count"] = 0
                data["last_pr"] = None
                save_error_count(ctx, data)

    except Exception as e:
        log_hook_execution("copilot-review-retry-suggestion", "error", f"フックエラー: {e}")

    print(json.dumps(result))


if __name__ == "__main__":
    main()
