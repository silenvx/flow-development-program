#!/usr/bin/env python3
"""worktree作成成功後にsetup-worktree.shを自動実行。

Why:
    worktree作成後に依存インストールを忘れると、pre-pushフック等が失敗する。
    自動実行することで、依存インストール漏れを防ぐ。

What:
    - git worktree add成功後（PostToolUse:Bash）に発火
    - コマンドからworktreeパスを抽出
    - .claude/scripts/setup-worktree.shを実行
    - 結果をsystemMessageで通知

Remarks:
    - 自動化型フック（worktree作成成功後に即座に実行）
    - setup-worktree.shがプロジェクト種別を自動検出（pnpm/npm等）
    - 失敗時は警告のみ（ブロックしない）

Changelog:
    - silenvx/dekita#1299: フック追加（pre-pushフック失敗防止）
    - silenvx/dekita#2607: HookContextパターン移行
"""

import json
import os
import re
import subprocess
from pathlib import Path

from lib.constants import TIMEOUT_EXTENDED
from lib.cwd import get_effective_cwd
from lib.execution import log_hook_execution
from lib.hook_input import get_tool_result
from lib.results import print_continue_and_log_skip
from lib.session import create_hook_context, parse_hook_input


def extract_worktree_path(command: str, cwd: Path) -> Path | None:
    """Extract worktree path from git worktree add command.

    Args:
        command: The command string containing git worktree add
        cwd: Current working directory

    Returns:
        Absolute path to worktree if found, None otherwise
    """
    # Match .worktrees/ pattern which is our convention
    worktree_match = re.search(r"\.worktrees/([^\s]+)", command)
    if worktree_match:
        worktree_rel = f".worktrees/{worktree_match.group(1)}"
        worktree_path = cwd / worktree_rel
        if worktree_path.exists():
            return worktree_path.resolve()

    return None


def run_setup_worktree(worktree_path: Path) -> tuple[bool, str]:
    """Run setup-worktree.sh for the given worktree.

    Args:
        worktree_path: Absolute path to the worktree

    Returns:
        Tuple of (success, message)
    """
    # Find setup-worktree.sh script
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return False, "CLAUDE_PROJECT_DIR not set"

    script_path = Path(project_dir) / ".claude" / "scripts" / "setup-worktree.sh"
    if not script_path.exists():
        return False, f"setup-worktree.sh not found at {script_path}"

    try:
        result = subprocess.run(
            ["bash", str(script_path), str(worktree_path)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_EXTENDED,  # 60 seconds for pnpm install
            cwd=project_dir,
        )

        if result.returncode == 0:
            return True, "Dependencies installed successfully"
        error_output = result.stderr.strip() or result.stdout.strip() or "Unknown error"
        return False, f"setup-worktree.sh failed: {error_output[:200]}"

    except subprocess.TimeoutExpired:
        return False, "setup-worktree.sh timed out"
    except Exception as e:
        return False, f"Failed to run setup-worktree.sh: {e}"


def main():
    """PostToolUse hook for Bash commands.

    Automatically run setup-worktree.sh after successful worktree creation.
    """
    result = {"continue": True}

    try:
        input_data = parse_hook_input()
        # Issue #2607: Create context for session_id logging
        ctx = create_hook_context(input_data)
        tool_input = input_data.get("tool_input", {})
        # Use standardized utility for tool_result/tool_response/tool_output handling
        tool_result = get_tool_result(input_data) or {}

        command = tool_input.get("command", "")
        # Default to 0 (success) if exit_code not provided
        exit_code = tool_result.get("exit_code", tool_result.get("exitCode", 0))

        # Check if this is a git worktree add command
        if not re.search(r"\bgit\s+worktree\s+add\b", command):
            print_continue_and_log_skip(
                "worktree-auto-setup", "not a git worktree add command", ctx=ctx
            )
            return

        # Only run on success
        if exit_code != 0:
            print_continue_and_log_skip(
                "worktree-auto-setup", f"command failed: exit_code={exit_code}", ctx=ctx
            )
            return

        # Get effective cwd
        cwd = get_effective_cwd(command)

        # Extract worktree path
        worktree_path = extract_worktree_path(command, cwd)
        if not worktree_path:
            log_hook_execution(
                "worktree-auto-setup",
                "skip",
                "Could not extract worktree path from command",
                {"command": command[:100]},
            )
            print(json.dumps(result))
            return

        # Run setup-worktree.sh
        # Note: setup-worktree.sh handles project type detection (package.json, pyproject.toml)
        # so we don't duplicate that check here
        success, message = run_setup_worktree(worktree_path)

        if success:
            result["systemMessage"] = (
                f"✅ worktree自動セットアップ完了: {worktree_path.name}\n"
                f"   node_modules がインストールされました。"
            )
            log_hook_execution(
                "worktree-auto-setup",
                "approve",
                message,
                {"worktree": str(worktree_path)},
            )
        else:
            # Don't block, just warn
            result["systemMessage"] = (
                f"⚠️ worktree自動セットアップ失敗: {worktree_path.name}\n"
                f"   {message}\n"
                f"   手動で実行してください: .claude/scripts/setup-worktree.sh .worktrees/{worktree_path.name}"
            )
            log_hook_execution(
                "worktree-auto-setup",
                "warn",
                message,
                {"worktree": str(worktree_path)},
            )

    except Exception as e:
        # Don't block on errors
        log_hook_execution("worktree-auto-setup", "error", str(e))

    print(json.dumps(result))


if __name__ == "__main__":
    main()
