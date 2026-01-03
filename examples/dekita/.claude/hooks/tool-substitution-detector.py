#!/usr/bin/env python3
"""パッケージマネージャの実行とツール代替パターンを追跡。

Why:
    ツールが失敗した際に、原因調査せずに別ツールに切り替えると根本問題が
    解決されない。パターンを記録して振り返りで分析できるようにする。

What:
    - パッケージマネージャコマンド実行後（PostToolUse:Bash）に発火
    - uvx/npm/pip/brew/cargo等のコマンドを検出
    - 実行結果（成功/失敗）をセッション別ログに記録
    - 振り返り時に代替パターンを分析可能に

State:
    - writes: .claude/logs/metrics/tool-substitution-*.jsonl

Remarks:
    - 非ブロック型（記録のみ、振り返りで分析）
    - 検出対象はTOOL_PATTERNSで定義

Changelog:
    - silenvx/dekita#1887: フック追加
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))
from common import METRICS_LOG_DIR
from lib.hook_input import get_tool_result
from lib.logging import log_to_session_file
from lib.results import make_approve_result
from lib.session import create_hook_context, parse_hook_input

# Hook name for result messages
HOOK_NAME = "tool-substitution-detector"

# Tool name patterns to track
TOOL_PATTERNS = {
    "uvx": r"\buvx\s+(\S+)",
    "uv": r"\buv\s+(?:pip\s+install|add)\s+(\S+)",
    "npm": r"\bnpm\s+(?:install|i|add)\s+(\S+)",
    "pip": r"\bpip\s+install\s+(\S+)",
    "brew": r"\bbrew\s+install\s+(\S+)",
    "cargo": r"\bcargo\s+(?:install|add)\s+(\S+)",
    "go": r"\bgo\s+(?:install|get)\s+(\S+)",
}


def extract_tool_info(command: str) -> tuple[str | None, str | None]:
    """Extract tool manager and package name from command.

    Returns:
        (tool_manager, package_name) or (None, None) if not a tool command
    """
    for tool_manager, pattern in TOOL_PATTERNS.items():
        match = re.search(pattern, command)
        if match:
            package = match.group(1)
            # Clean up package name (remove version specifiers)
            package = re.sub(r"[@=<>].*", "", package)
            return tool_manager, package
    return None, None


def main() -> None:
    """Main entry point for the hook."""
    data = parse_hook_input()

    ctx = create_hook_context(data)

    # Only process Bash PostToolUse
    if data.get("tool_name") != "Bash":
        print(json.dumps(make_approve_result(HOOK_NAME)))
        return

    # Use get_tool_result() utility for consistent handling
    tool_result = get_tool_result(data)
    tool_input = data.get("tool_input", {})
    command = tool_input.get("command", "")
    session_id = ctx.get_session_id()

    # Extract tool info from command
    tool_manager, package_name = extract_tool_info(command)

    if not tool_manager or not package_name:
        # Not a tool installation command
        print(json.dumps(make_approve_result(HOOK_NAME)))
        return

    # Handle non-dict tool_result (can be None, str, or other types)
    if not isinstance(tool_result, dict):
        tool_result = {}

    # Check if command failed
    stderr = tool_result.get("stderr", "")
    stdout = tool_result.get("stdout", "")

    # Detect failure patterns
    is_failure = False
    failure_reason = ""

    # Check exit_code field directly (most reliable)
    exit_code = tool_result.get("exit_code")
    if exit_code is not None and exit_code != 0:
        is_failure = True
        failure_reason = f"non-zero exit code: {exit_code}"
    elif "error" in stderr.lower() or "failed" in stderr.lower():
        is_failure = True
        failure_reason = "stderr contains error"
    elif "not found" in stderr.lower() or "no such" in stderr.lower():
        is_failure = True
        failure_reason = "package/command not found"
    # Also check stdout for error patterns (some tools output errors to stdout)
    elif "error" in stdout.lower() and "not found" in stdout.lower():
        is_failure = True
        failure_reason = "stdout contains error"

    # Log tool execution
    log_entry = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "session_id": session_id,
        "type": "tool_execution",
        "tool_manager": tool_manager,
        "package": package_name,
        "command_preview": command[:100],
        "is_failure": is_failure,
        "failure_reason": failure_reason if is_failure else None,
    }

    log_to_session_file(METRICS_LOG_DIR, "tool-substitution", session_id, log_entry)

    print(json.dumps(make_approve_result(HOOK_NAME)))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Ensure hook returns approve response even on unexpected errors
        print(json.dumps({"hook": HOOK_NAME, "error": str(e)}), file=sys.stderr)
        print(json.dumps(make_approve_result(HOOK_NAME)))
