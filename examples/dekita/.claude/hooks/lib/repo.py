#!/usr/bin/env python3
"""リポジトリ関連のユーティリティ関数を提供する。

Why:
    worktreeのルート検出やマージ成功判定など、リポジトリ操作で
    共通して必要な機能を一元化する。

What:
    - get_repo_root(): worktreeを考慮したリポジトリルート取得
    - is_merge_success(): gh pr mergeの成功判定

Remarks:
    - worktreeでは.gitがファイル（ディレクトリではない）
    - マージ成功判定は複数のエッジケースを考慮
    - --delete-branchオプション使用時の特殊処理あり

Changelog:
    - silenvx/dekita#1556: 複数フックから統合
    - silenvx/dekita#1758: common.pyから分離
    - silenvx/dekita#2099: ブランチ削除失敗検出を追加
    - silenvx/dekita#2228: ブランチ削除失敗時の成功パターン不要化
"""

import os
import re
import subprocess
from pathlib import Path


def _get_project_dir() -> Path:
    """Get project directory, detecting git root as fallback."""
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_dir:
        return Path(env_dir)

    # Fallback: find git root from current directory
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:
        pass  # Fall through to cwd fallback on any error

    # Last resort: use cwd
    return Path.cwd()


def get_repo_root(project_dir: Path | None = None) -> Path | None:
    """Get the root repository directory, handling worktree case.

    If the current directory (or provided project_dir) is a worktree,
    resolves to the parent repository root.

    Worktrees have a .git file (not directory) containing 'gitdir: ...'
    pointing to .git/worktrees/<name> in the parent repo.

    Issue #1556: Consolidated from multiple hook files into common.py.

    Args:
        project_dir: Optional path to check. If None, uses CLAUDE_PROJECT_DIR
                     environment variable or the current working directory.

    Returns:
        Path to repository root, or None if not a git repository.
    """
    if project_dir is None:
        # Use environment variable or detect project directory
        env_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
        if env_dir:
            project_dir = Path(env_dir)
        else:
            project_dir = _get_project_dir()

    # Return None if no project directory could be determined
    if project_dir is None:
        return None

    git_path = project_dir / ".git"

    if not git_path.exists():
        return None

    # If .git is a directory, this is the main repo
    if git_path.is_dir():
        return project_dir

    # If .git is a file, this is a worktree - parse gitdir
    try:
        content = git_path.read_text().strip()
        if content.startswith("gitdir:"):
            gitdir = content[7:].strip()
            gitdir_path = Path(gitdir)
            if not gitdir_path.is_absolute():
                gitdir_path = (project_dir / gitdir_path).resolve()

            # gitdir points to .git/worktrees/<name>
            # Go up to .git, then up again to repo root
            # e.g., /repo/.git/worktrees/foo -> /repo/.git -> /repo
            if "worktrees" in gitdir_path.parts:
                # Find the .git directory (parent of worktrees)
                git_dir = gitdir_path.parent.parent
                return git_dir.parent
    except OSError:
        # File read error - treat as no git repository
        pass

    return None


def is_merge_success(
    exit_code: int,
    stdout: str,
    command: str = "",
    *,
    stderr: str = "",
) -> bool:
    """Check if gh pr merge was successful.

    Handles various edge cases:
    - Worktree --delete-branch edge case (exit_code != 0 but success pattern)
    - Auto-merge scheduling (returns False - not an actual merge)
    - Squash merge with empty output (exit_code 0 is success)
    - Combined stdout+stderr checking
    - Branch deletion failure in worktree (merge succeeded but branch delete failed)

    Issue #1556: Consolidated from multiple hook files into common.py.
    Issue #2099: Added branch deletion failure detection for worktree edge case.

    Args:
        exit_code: Command exit code (0 typically means success)
        stdout: Standard output from the command
        command: Original command string (optional, for edge case detection)
        stderr: Standard error from the command (optional)

    Returns:
        True if the merge was successful, False otherwise.
    """
    # Skip auto-merge scheduling (not an actual merge)
    if command and "--auto" in command:
        return False

    # Success patterns to check in output
    success_patterns = [
        r"[Mm]erged\s+pull\s+request",
        r"Pull\s+request\s+.*\s+merged",
        r"was already merged",
        r"Merge completed successfully",  # locked-worktree-guard output
    ]

    # Branch deletion failure pattern (merge succeeded but delete failed)
    # This happens in worktrees where the branch is checked out
    # Combined into single pattern for performance
    branch_delete_failure_pattern = "|".join(
        [
            r"failed to delete.*branch",
            r"cannot delete.*branch",
            r"error deleting branch",
        ]
    )

    combined_output = stdout + stderr

    # If exit_code is 0, check for success indicators
    if exit_code == 0:
        # Squash merge may produce empty output - that's still success
        if not combined_output.strip():
            return True
        # Check for explicit success patterns
        for pattern in success_patterns:
            if re.search(pattern, combined_output, re.IGNORECASE):
                return True
        # exit_code 0 with output but no success pattern - be conservative
        # This could be auto-merge scheduling or other non-merge output
        return False

    # Non-zero exit code - check for worktree edge case
    # In worktrees, --delete-branch may fail but merge still succeeded
    for pattern in success_patterns:
        if re.search(pattern, combined_output, re.IGNORECASE):
            return True

    # Check for branch deletion failure pattern
    # If --delete-branch is in command and we see a delete failure,
    # the merge succeeded (gh CLI only attempts branch deletion after merge)
    # Issue #2228: Don't require success pattern - gh CLI may not output it
    # when branch deletion fails immediately after merge
    if command and "--delete-branch" in command:
        if re.search(branch_delete_failure_pattern, combined_output, re.IGNORECASE):
            # Branch deletion only happens AFTER successful merge
            # So if we see a deletion failure, the merge was successful
            return True

    return False
