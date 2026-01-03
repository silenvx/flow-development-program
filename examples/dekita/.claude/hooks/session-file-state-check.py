#!/usr/bin/env python3
# - 責務: セッション再開時にファイル状態（uncommitted changes）を検証
# - 重複なし: session-resume-warningは競合警告、こちらはファイル状態検証
# - 非ブロッキング: 情報表示のみ
"""
セッション再開時ファイル状態検証フック（SessionStart）

Issue #2468: セッション再開時にサマリーと実際のファイル状態が乖離していることがある。
- サマリーには「編集完了」と記載されているが、実際は未コミット
- サマリーを信頼して次のステップに進もうとすると問題が発生

このフックは:
1. `git status` でuncommitted changesを確認
2. セッション再開時（resume/compact）に未コミット変更があれば警告を表示
3. 直前のコミット内容を表示して整合性確認を促す
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))
from lib.execution import log_hook_execution
from lib.session import parse_hook_input

HOOK_NAME = "session-file-state-check"


def get_git_status() -> dict[str, list[str]]:
    """Get uncommitted changes from git status.

    Returns:
        Dict with 'staged', 'unstaged', and 'untracked' file lists.
    """
    result = {"staged": [], "unstaged": [], "untracked": []}

    try:
        status_result = subprocess.run(
            ["git", "status", "--porcelain", "-z"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if status_result.returncode != 0:
            return result

        # -z option: NUL-separated entries, handles special characters in filenames
        # Format: XY filename\0 (or XY oldname\0newname\0 for renames)
        entries = status_result.stdout.split("\0")
        i = 0
        while i < len(entries):
            entry = entries[i]
            if not entry:
                i += 1
                continue

            # Porcelain format: XY filename
            # X = index status, Y = work tree status
            index_status = entry[0] if len(entry) > 0 else " "
            worktree_status = entry[1] if len(entry) > 1 else " "
            filename = entry[3:] if len(entry) > 3 else ""

            if index_status == "?":
                result["untracked"].append(filename)
            elif index_status != " ":
                result["staged"].append(filename)
            if worktree_status not in (" ", "?"):
                result["unstaged"].append(filename)

            # Handle renames (R) and copies (C) which have a second filename
            if index_status in ("R", "C"):
                i += 1  # Skip the next entry (old filename)

            i += 1

        return result
    except Exception:
        return result


def get_last_commit_info() -> str | None:
    """Get the last commit message and affected files.

    Returns:
        Formatted string with commit info, or None if unavailable.
    """
    try:
        # Get last commit hash, message, and time
        log_result = subprocess.run(
            [
                "git",
                "log",
                "-1",
                "--format=%h %s (%ar)",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if log_result.returncode != 0 or not log_result.stdout.strip():
            return None

        commit_info = log_result.stdout.strip()

        # Get files changed in last commit (-z for NUL-separated output)
        files_result = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        files = []
        if files_result.returncode == 0:
            files = [f for f in files_result.stdout.split("\0") if f][:5]

        result = f"  {commit_info}"
        if files:
            files_str = ", ".join(files)
            if len(files) >= 5:
                files_str += ", ..."
            result += f"\n  変更ファイル: {files_str}"

        return result
    except Exception:
        return None


def format_file_state_warning(
    status: dict[str, list[str]],
    last_commit: str | None,
) -> str:
    """Format the file state warning message.

    Args:
        status: Dict with staged, unstaged, untracked file lists.
        last_commit: Last commit info string.
    """
    message_parts = [
        "⚠️ **ファイル状態の確認が必要です**\n",
        "セッション再開時に未コミットの変更が検出されました。",
        "**サマリーとファイル状態が乖離している可能性があります**。\n",
    ]

    # Show uncommitted changes
    if status["staged"]:
        message_parts.append(f"**ステージ済み** ({len(status['staged'])}件):")
        for f in status["staged"][:5]:
            message_parts.append(f"  - {f}")
        if len(status["staged"]) > 5:
            message_parts.append(f"  ... 他 {len(status['staged']) - 5}件")
        message_parts.append("")

    if status["unstaged"]:
        message_parts.append(f"**未ステージ変更** ({len(status['unstaged'])}件):")
        for f in status["unstaged"][:5]:
            message_parts.append(f"  - {f}")
        if len(status["unstaged"]) > 5:
            message_parts.append(f"  ... 他 {len(status['unstaged']) - 5}件")
        message_parts.append("")

    if status["untracked"]:
        message_parts.append(f"**未追跡ファイル** ({len(status['untracked'])}件):")
        for f in status["untracked"][:3]:
            message_parts.append(f"  - {f}")
        if len(status["untracked"]) > 3:
            message_parts.append(f"  ... 他 {len(status['untracked']) - 3}件")
        message_parts.append("")

    # Show last commit for comparison
    if last_commit:
        message_parts.append("**直前のコミット**:")
        message_parts.append(last_commit)
        message_parts.append("")

    # Add guidance
    message_parts.extend(
        [
            "📋 **確認事項**:",
            "- サマリーの「完了」項目が実際にコミット済みか確認",
            "- 未コミット変更がサマリーの作業内容と一致するか確認",
            "- 不整合がある場合、`git status` と `git diff` で詳細確認",
        ]
    )

    return "\n".join(message_parts)


def main():
    """SessionStart hookのエントリーポイント"""
    result = {"continue": True}

    try:
        hook_input = parse_hook_input()
        source = hook_input.get("source", "")

        # Only check on session resume (resume or compact)
        if source not in ("resume", "compact"):
            log_hook_execution(
                HOOK_NAME,
                "approve",
                f"Not a resume session (source={source})",
            )
            print(json.dumps(result))
            return

        # Get git status
        status = get_git_status()
        has_changes = any((status["staged"], status["unstaged"], status["untracked"]))

        # If no changes at all, nothing to warn about
        if not has_changes:
            log_hook_execution(
                HOOK_NAME,
                "approve",
                "Working tree is clean",
            )
            print(json.dumps(result))
            return

        # Get last commit info for context
        last_commit = get_last_commit_info()

        # Format and display warning
        result["message"] = format_file_state_warning(status, last_commit)

        log_hook_execution(
            HOOK_NAME,
            "approve",
            f"Uncommitted changes detected (staged={len(status['staged'])}, "
            f"unstaged={len(status['unstaged'])}, untracked={len(status['untracked'])})",
            details={
                "source": source,
                "staged_count": len(status["staged"]),
                "unstaged_count": len(status["unstaged"]),
                "untracked_count": len(status["untracked"]),
            },
        )

    except Exception as e:
        log_hook_execution(
            HOOK_NAME,
            "approve",
            f"Error: {e}",
        )

    print(json.dumps(result))


if __name__ == "__main__":
    main()
