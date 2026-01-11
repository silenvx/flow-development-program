#!/usr/bin/env python3
"""git commit後にCodexレビューマーカーを更新する。

Why:
    レビュー済みブランチで追加コミットをした場合、マーカーを更新しないと
    codex-review-checkが「レビュー後にコミットあり」と誤検知する。
    コミット後にマーカーを更新することで、不要な再レビューを防ぐ。

What:
    - git commitコマンドを検出
    - 既存マーカーファイルがあれば新しいコミット情報で更新
    - main/masterブランチでは更新しない
    - HEADが変わっていない場合はスキップ

State:
    - reads: .claude/logs/markers/codex-review-{branch}.done
    - writes: .claude/logs/markers/codex-review-{branch}.done

Remarks:
    - 記録型フック（ブロックしない、マーカー更新）
    - PostToolUse:Bashで発火（git commitコマンド）
    - 既存マーカーがない場合は何もしない（新規作成しない）
    - codex-review-loggerとの役割分担: loggerはcodex review時、本フックはgit commit時

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import json
import re
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))
from common import MARKERS_LOG_DIR
from lib.execution import log_hook_execution
from lib.git import get_current_branch, get_diff_hash, get_head_commit
from lib.session import parse_hook_input
from lib.strings import sanitize_branch_name, split_command_chain, strip_quoted_strings


def is_git_commit_command(command: str) -> bool:
    """Check if command contains git commit.

    Handles command chains like:
    - git add && git commit -m "msg"
    - git status; git commit

    Also handles quoted strings to avoid false positives like:
    - echo "git commit"
    """
    stripped = strip_quoted_strings(command)
    subcommands = split_command_chain(stripped)
    for subcmd in subcommands:
        if re.search(r"^git\s+commit(\s|$)", subcmd):
            return True
    return False


def update_marker(branch: str, commit: str, diff_hash: str) -> bool:
    """Update marker file if it exists.

    Only updates existing marker files. Does NOT create new markers.
    This ensures that branches that haven't been reviewed yet don't
    get marker files created automatically.

    Args:
        branch: The git branch name.
        commit: The new HEAD commit hash.
        diff_hash: The new diff hash.

    Returns:
        True if marker was updated, False if marker didn't exist.
    """
    safe_branch = sanitize_branch_name(branch)
    marker_file = MARKERS_LOG_DIR / f"codex-review-{safe_branch}.done"

    if not marker_file.exists():
        return False

    content = f"{branch}:{commit}:{diff_hash}"
    marker_file.write_text(content)
    return True


def main():
    """PostToolUse hook for Bash commands.

    Updates Codex review marker after successful git commit.
    """
    hook_input = parse_hook_input()
    if not hook_input:
        print(json.dumps({"continue": True}))
        return

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})

    # Only process Bash tool
    if tool_name != "Bash":
        print(json.dumps({"continue": True}))
        return

    command = tool_input.get("command", "")

    # Only process git commit commands
    if not is_git_commit_command(command):
        print(json.dumps({"continue": True}))
        return

    # Skip main/master
    branch = get_current_branch()
    if not branch or branch in ("main", "master"):
        print(json.dumps({"continue": True}))
        return

    # Get current HEAD - check if it changed since marker was created
    commit = get_head_commit()
    diff_hash = get_diff_hash()

    if not commit or not diff_hash:
        print(json.dumps({"continue": True}))
        return

    # Check if marker exists
    safe_branch = sanitize_branch_name(branch)
    marker_file = MARKERS_LOG_DIR / f"codex-review-{safe_branch}.done"

    if not marker_file.exists():
        log_hook_execution(
            "commit-marker-update",
            "skip",
            f"No marker file for branch: {branch}",
        )
        print(json.dumps({"continue": True}))
        return

    # Read marker and check if HEAD changed
    # This handles chained commands like "git commit && git push" where
    # push might fail but commit succeeded (HEAD changed)
    marker_content = marker_file.read_text().strip()
    marker_parts = marker_content.split(":")
    if len(marker_parts) >= 2:
        marker_commit = marker_parts[1]
        if marker_commit == commit:
            # HEAD hasn't changed, no new commit
            log_hook_execution(
                "commit-marker-update",
                "skip",
                f"HEAD unchanged: {commit[:8]}",
            )
            print(json.dumps({"continue": True}))
            return

    # HEAD changed, update marker
    updated = update_marker(branch, commit, diff_hash)
    if updated:
        log_hook_execution(
            "commit-marker-update",
            "success",
            f"Marker updated: {branch}:{commit[:8]}:{diff_hash[:8]}",
        )

    print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
