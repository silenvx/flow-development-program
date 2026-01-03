#!/usr/bin/env python3
"""スクリプト作成・変更時に実データでのテストを促す（Dogfooding）。

Why:
    データ処理スクリプトをテストなしでコミットすると、実データで初めて
    バグが発覚する。自分で使って問題を体験してから完了とする習慣を促進。

What:
    - .claude/scripts/*.pyへのWrite/Editを検出
    - データ処理パターン（subprocess, json.loads等）を含む場合に警告
    - Dogfoodingチェックリストを表示

State:
    - writes: .claude/logs/dogfooding/reminded-{session}.txt

Remarks:
    - リマインド型フック（ブロックしない、systemMessageで提案）
    - PreToolUse:Write/Editで発火
    - .claude/scripts/*.pyが対象（tests/は除外）
    - データ処理パターン（subprocess, json.loads等）を含む場合のみ

Changelog:
    - silenvx/dekita#1937: 発端となった問題（テストなしでのスクリプト作成）
    - silenvx/dekita#1942: フック追加
"""

import json
import os
from pathlib import Path

from lib.execution import log_hook_execution
from lib.results import print_continue_and_log_skip
from lib.session import HookContext, create_hook_context, parse_hook_input

# Directory for session-based tracking files
_TRACKING_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")) / ".claude" / "logs" / "dogfooding"


def is_new_script(file_path: str, tool_name: str, old_string: str) -> bool:
    """Check if this is a new script creation.

    Args:
        file_path: Path to the file
        tool_name: Name of the tool (Write or Edit)
        old_string: Old content for Edit tool

    Returns:
        True if this appears to be a new script creation
    """
    if tool_name == "Write":
        # Write tool always creates/overwrites a file
        # Check if file didn't exist before
        return not Path(file_path).exists()

    # For Edit tool, if old_string is empty or very short, it might be initial content
    return len(old_string.strip()) < 50


def has_data_processing_patterns(content: str) -> bool:
    """Check if the script contains data processing patterns.

    Args:
        content: Script content

    Returns:
        True if the script appears to process external data
    """
    patterns = [
        # API/HTTP calls
        "requests.",
        "httpx.",
        "urllib",
        "fetch(",
        # Subprocess/command execution
        "subprocess.",
        "run_gh_command",
        "run_git_command",
        # JSON/data parsing
        "json.loads",
        "json.load",
        ".split(",
        ".parse(",
        # File reading
        "open(",
        "Path(",
        "read_text(",
        "read_bytes(",
    ]
    return any(pattern in content for pattern in patterns)


def _get_session_tracking_file(ctx: HookContext) -> Path:
    """Get the session-specific tracking file path.

    Args:
        ctx: HookContext for session information.

    Returns:
        Path to the session tracking file
    """
    session_id = ctx.get_session_id()
    # Sanitize session_id to prevent path traversal attacks
    safe_session_id = Path(session_id).name
    return _TRACKING_DIR / f"reminded-{safe_session_id}.txt"


def was_already_reminded(ctx: HookContext, file_path: str) -> bool:
    """Check if we already showed a reminder for this file in this session.

    Uses a session-based file for tracking since environment variables
    don't persist across separate hook process invocations.

    Args:
        ctx: HookContext for session information.
        file_path: Path to the file

    Returns:
        True if already reminded
    """
    tracking_file = _get_session_tracking_file(ctx)
    if not tracking_file.exists():
        return False
    try:
        reminded_files = tracking_file.read_text().strip().split("\n")
        return file_path in reminded_files
    except OSError:
        return False


def mark_as_reminded(ctx: HookContext, file_path: str) -> None:
    """Mark a file as reminded for this session.

    Uses a session-based file for tracking since environment variables
    don't persist across separate hook process invocations.

    Args:
        ctx: HookContext for session information.
        file_path: Path to the file
    """
    tracking_file = _get_session_tracking_file(ctx)
    try:
        _TRACKING_DIR.mkdir(parents=True, exist_ok=True)
        # Append to the file (create if doesn't exist)
        with tracking_file.open("a") as f:
            f.write(f"{file_path}\n")
    except OSError:
        pass  # Silently fail - reminder deduplication is best-effort


def build_reminder_message(file_path: str, is_new: bool) -> str:
    """Build the Dogfooding reminder message.

    Args:
        file_path: Path to the script
        is_new: Whether this is a new script

    Returns:
        Formatted reminder message
    """
    action = "新規スクリプト作成" if is_new else "スクリプト変更"
    return f"""💡 [{action}] Dogfoodingチェックリスト

ファイル: {file_path}

コミット前に以下を確認してください:
□ 実際のデータで動作確認しましたか？
□ エッジケース（空、改行含む、大量データ）をテストしましたか？
□ 対応するテストファイルを作成/更新しましたか？

ヒント: このスクリプトが解決する問題を、自分で再現・体験してから完了としてください。
参考: Issue #1942, AGENTS.md「Dogfooding原則」"""


def main() -> None:
    """Main entry point for the hook."""
    result: dict = {"continue": True}

    try:
        input_data = parse_hook_input()

        ctx = create_hook_context(input_data)
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        # Only target Write and Edit tools
        if tool_name not in ("Write", "Edit"):
            print_continue_and_log_skip(
                "dogfooding-reminder", f"not Write/Edit: {tool_name}", ctx=ctx
            )
            return

        file_path = tool_input.get("file_path", "")

        # Only target .claude/scripts/*.py files
        if ".claude/scripts/" not in file_path or not file_path.endswith(".py"):
            print_continue_and_log_skip("dogfooding-reminder", "not a script file", ctx=ctx)
            return

        # Exclude files in tests directory
        if "/tests/" in file_path:
            print_continue_and_log_skip("dogfooding-reminder", "test file excluded", ctx=ctx)
            return

        # Check if already reminded for this file
        if was_already_reminded(ctx, file_path):
            print_continue_and_log_skip("dogfooding-reminder", "already reminded", ctx=ctx)
            return

        # Get content to check for data processing patterns
        content = tool_input.get("content", "") or tool_input.get("new_string", "")
        old_string = tool_input.get("old_string", "")

        # Only show reminder for scripts with data processing patterns
        if not has_data_processing_patterns(content):
            print_continue_and_log_skip(
                "dogfooding-reminder", "no data processing patterns", ctx=ctx
            )
            return

        # Determine if this is a new script
        is_new = is_new_script(file_path, tool_name, old_string)

        # Build and set reminder message
        result["systemMessage"] = build_reminder_message(file_path, is_new)

        # Mark as reminded
        mark_as_reminded(ctx, file_path)

        log_hook_execution(
            "dogfooding-reminder",
            "remind",
            f"{'New' if is_new else 'Modified'} script: {file_path}",
            {"file": file_path, "is_new": is_new},
        )

    except Exception:
        # Never fail the hook - just skip reminder
        pass

    print(json.dumps(result))


if __name__ == "__main__":
    main()
