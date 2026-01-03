#!/usr/bin/env python3
"""mainブランチでの編集をブロックし、worktreeでの作業を強制する。

Why:
    mainで直接編集すると競合やレビューなしの変更が発生するリスクがある。
    ロック中のworktreeは別セッションが作業中の可能性がある。

What:
    - main/masterブランチでEdit/Write時にブロック
    - ロック中worktreeでの編集時に警告
    - worktree作成手順を提示

Remarks:
    - ブロック型フック（mainでの編集はブロック）
    - .claude/plans/は例外として許可（Issue #844）
    - worktree-session-guardはセッション間競合、本フックはブランチ保護

Changelog:
    - silenvx/dekita#527: 別セッションがロック中のworktreeへの作業開始を警告
    - silenvx/dekita#844: .claude/plans/を例外に追加
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from lib.constants import TIMEOUT_LIGHT, TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.results import make_block_result
from lib.session import parse_hook_input

# Branches that should block editing (typically default branches)
PROTECTED_BRANCHES = ("main", "master")

# Paths allowed to edit even on protected branches (Issue #844)
# These are metadata/planning files, not regular code changes
ALLOWLIST_PATH_PREFIXES = (
    ".claude/plans/",  # Plan files for issue work planning
)


def is_path_in_allowlist(file_path: str, project_root: str) -> bool:
    """Check if a file path is in the allowlist for editing on protected branches.

    Args:
        file_path: The absolute file path being edited.
        project_root: The project root directory.

    Returns:
        True if the path is allowed to be edited on protected branches.
    """
    if not project_root or not file_path:
        return False

    # Normalize project_root to handle trailing slashes consistently
    project_root_norm = project_root.rstrip("/")

    # Get relative path from project root
    if file_path.startswith(project_root_norm + "/"):
        rel_path = file_path[len(project_root_norm) + 1 :]
    elif file_path == project_root_norm:
        # File path is exactly the project root (unlikely but handle it)
        return False
    else:
        return False

    # Check if path matches any allowlist prefix
    for prefix in ALLOWLIST_PATH_PREFIXES:
        if rel_path.startswith(prefix):
            return True

    return False


def get_current_branch(file_path: str) -> str:
    """Get the current git branch for the given file path.

    Can be overridden via CLAUDE_TEST_BRANCH environment variable for testing.
    Falls back to project root if the file's parent directory doesn't exist.
    Returns an empty string if the branch cannot be determined. This includes:
      - The working directory is None or not a valid directory
      - The path is not inside a git repository
      - The git command fails (e.g., git not installed)
    """
    # Allow override for testing
    test_branch = os.environ.get("CLAUDE_TEST_BRANCH")
    if test_branch is not None:
        return test_branch

    # Try to find a valid directory to run git from
    # Priority: 1. Traverse up to find first existing parent, 2. CLAUDE_PROJECT_DIR
    cwd = None
    if file_path:
        # Traverse up the directory tree to find the first existing parent
        parent = os.path.dirname(file_path)
        while parent and not os.path.isdir(parent):
            new_parent = os.path.dirname(parent)
            if new_parent == parent:  # Reached root
                break
            parent = new_parent
        if parent and os.path.isdir(parent):
            cwd = parent

    # Fall back to project root if no existing parent directory is found
    if not cwd or not os.path.isdir(cwd):
        cwd = os.environ.get("CLAUDE_PROJECT_DIR")

    if not cwd or not os.path.isdir(cwd):
        return ""

    try:
        result = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=cwd,
            text=True,
        )
        return result.strip()
    except (subprocess.CalledProcessError, OSError):
        # Git command failed (not a git repo, git not installed, etc.)
        pass
    return ""


def get_project_root(file_path: str) -> str:
    """Get the git repository root for the given file path."""
    proj = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if proj:
        return proj

    try:
        cwd = os.path.dirname(file_path) if file_path else None
        if cwd and os.path.isdir(cwd):
            result = subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                stderr=subprocess.DEVNULL,
                cwd=cwd,
            )
            return result.decode().strip()
    except (subprocess.CalledProcessError, OSError):
        pass  # Best effort - git command may fail

    return ""


def extract_worktree_root(file_path: str) -> str | None:
    """Extract the worktree root directory from a file path.

    Args:
        file_path: A file path that contains ".worktrees/" marker.

    Returns:
        The worktree root path, or None if marker not found.

    Examples:
        >>> extract_worktree_root("/project/.worktrees/feature/src/file.ts")
        '/project/.worktrees/feature'
        >>> extract_worktree_root("/project/src/file.ts")
        None
    """
    worktree_marker = ".worktrees/"
    if worktree_marker not in file_path:
        return None

    idx = file_path.find(worktree_marker)
    after_marker = file_path[idx + len(worktree_marker) :]
    if "/" in after_marker:
        worktree_name = after_marker.split("/")[0]
    else:
        worktree_name = after_marker
    return file_path[: idx + len(worktree_marker)] + worktree_name


def get_worktree_lock_info(worktree_path: str) -> tuple[bool, str | None]:
    """Check if a worktree is locked and get the lock reason.

    Args:
        worktree_path: Path to the worktree directory (absolute or relative).
            Relative paths are resolved against the current working directory.

    Returns:
        Tuple of (is_locked, lock_reason).
        lock_reason is None if not locked or no reason specified.
    """
    try:
        # Get git common dir to run worktree list from main repo
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
            cwd=worktree_path,
        )
        if result.returncode != 0:
            return False, None

        git_common = Path(result.stdout.strip())
        # Resolve relative path against worktree_path (git may return relative path)
        if not git_common.is_absolute():
            git_common = (Path(worktree_path) / git_common).resolve()
        main_repo = git_common.parent

        # List all worktrees with porcelain format
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            cwd=str(main_repo),
        )
        if result.returncode != 0:
            return False, None

        # Parse porcelain output to find this worktree
        worktree_path_resolved = str(Path(worktree_path).resolve())
        current_worktree = None
        is_locked = False
        lock_reason = None

        for line in result.stdout.split("\n"):
            if line.startswith("worktree "):
                # Save previous worktree info if it was the one we're looking for
                if current_worktree == worktree_path_resolved and is_locked:
                    return True, lock_reason

                # Start tracking new worktree
                current_worktree = line[9:]  # Remove "worktree " prefix
                try:
                    current_worktree = str(Path(current_worktree).resolve())
                except OSError:
                    # If the path cannot be resolved (e.g. missing directory),
                    # fall back to the original string value and continue parsing.
                    pass
                is_locked = False
                lock_reason = None

            elif line == "locked":
                is_locked = True
                lock_reason = None

            elif line.startswith("locked "):
                is_locked = True
                lock_reason = line[7:]  # Remove "locked " prefix

        # Check the last worktree
        if current_worktree == worktree_path_resolved and is_locked:
            return True, lock_reason

        return False, None

    except (subprocess.TimeoutExpired, OSError):
        return False, None


def main():
    """Check if editing files in wrong worktree and warn the user."""
    result = None
    file_path = ""
    try:
        data = parse_hook_input()
        file_path = data.get("tool_input", {}).get("file_path", "")

        if not file_path:
            result = {
                "decision": "approve",
                "systemMessage": "✅ worktree-warning: ファイルパスなし（スキップ）",
            }
        else:
            project_root = get_project_root(file_path)
            if not project_root:
                result = {
                    "decision": "approve",
                    "systemMessage": "✅ worktree-warning: プロジェクト外（スキップ）",
                }
            else:
                in_project = file_path.startswith(project_root)
                in_worktree = ".worktrees/" in file_path
                current_branch = get_current_branch(file_path)

                # Block editing on protected branches (main/master) within project
                # Exception: files in allowlist (e.g., .claude/plans/) are allowed (Issue #844)
                if current_branch in PROTECTED_BRANCHES and in_project:
                    if is_path_in_allowlist(file_path, project_root):
                        result = {
                            "decision": "approve",
                            "systemMessage": (
                                f"✅ worktree-warning: {current_branch}ブランチですが、"
                                "許可リスト内のファイルのため編集可能"
                            ),
                        }
                    else:
                        reason = (
                            f"🚫 {current_branch}ブランチでの編集はブロックされました。\n\n"
                            "【対処法】以下のコマンドを**1つずつ順番に**実行してください:\n\n"
                            "**Step 1**: worktreeを作成\n"
                            "```\n"
                            "git worktree add --lock .worktrees/<issue-番号> -b <branch-name>\n"
                            "```\n\n"
                            "**Step 2**: worktreeに移動\n"
                            "```\n"
                            "cd .worktrees/<issue-番号>\n"
                            "```\n\n"
                            "**Step 3**: 再度編集を実行\n\n"
                            "⚠️ 注意:\n"
                            "- `<issue-番号>` は対象のIssue番号に置き換えてください（例: issue-123）\n"
                            "- `<branch-name>` は適切なブランチ名に置き換えてください"
                        )
                        result = make_block_result("worktree-warning", reason)
                elif in_project and not in_worktree:
                    # Not on main, but not in worktree either (e.g., git checkout -b)
                    result = {
                        "decision": "approve",
                        "systemMessage": (
                            "⚠️ WARNING: オリジナルディレクトリで編集中。 "
                            "AGENTS.mdのworktreeルールを確認してください。"
                        ),
                    }
                elif in_worktree:
                    # In a worktree - check if it's locked (Issue #527)
                    # in_worktree is True means ".worktrees/" is in file_path (line 222)
                    worktree_root = extract_worktree_root(file_path) or file_path

                    is_locked, lock_reason = get_worktree_lock_info(worktree_root)

                    if is_locked:
                        # Warn about editing in a locked worktree
                        reason_msg = f"\nロック理由: {lock_reason}" if lock_reason else ""
                        result = {
                            "decision": "approve",
                            "systemMessage": (
                                f"⚠️ WARNING: このworktreeはロック中です。{reason_msg}\n"
                                "別セッションが作業中の可能性があります。\n"
                                "競合に注意して作業を続行してください。"
                            ),
                        }
                    else:
                        result = {
                            "decision": "approve",
                            "systemMessage": "✅ worktree-warning: worktree内で編集中",
                        }
                else:
                    result = {
                        "decision": "approve",
                        "systemMessage": "✅ worktree-warning: OK",
                    }

    except Exception as e:
        # Don't block on errors
        print(f"[worktree-warning] Hook error: {e}", file=sys.stderr)
        result = {"decision": "approve", "reason": f"Hook error: {e}"}

    log_hook_execution(
        "worktree-warning",
        result.get("decision", "approve"),
        result.get("reason"),
        {"file_path": file_path} if file_path else None,
    )
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
