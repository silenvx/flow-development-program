#!/usr/bin/env python3
"""worktree状態管理のユーティリティモジュール。

Why:
    locked-worktree-guard等の複数フックでworktree操作が必要。
    共通のworktree操作関数を提供することで、重複実装を避ける。

What:
    - worktree一覧取得（パス、ブランチ、ロック状態）
    - セッション所有権チェック（SESSION_MARKER_FILE）
    - アクティブな作業の兆候検出（未コミット変更、最近のコミット）
    - rm対象のworktree検出

Remarks:
    - フックではなくユーティリティモジュール
    - locked-worktree-guard, worktree-auto-cleanup等から使用
    - fail-open設計（エラー時はブロックしない）

Changelog:
    - silenvx/dekita#xxx: モジュール追加
    - silenvx/dekita#317: get_current_worktree()のcwd引数追加
    - silenvx/dekita#682: is_cwd_inside_worktree()にcd検出追加
    - silenvx/dekita#795: get_orphan_worktree_directories()追加
    - silenvx/dekita#1400: is_self_session_worktree()追加
    - silenvx/dekita#2496: session_idパラメータ追加
"""

import subprocess
from pathlib import Path

from command_parser import extract_rm_paths
from lib.constants import SESSION_MARKER_FILE, TIMEOUT_LIGHT, TIMEOUT_MEDIUM
from lib.cwd import extract_cd_target_from_command, get_effective_cwd
from lib.git import check_recent_commits, check_uncommitted_changes


def is_self_session_worktree(worktree_path: Path, session_id: str | None = None) -> bool:
    """Check if worktree was created by current session.

    Issue #1400: Allow operations on worktrees created by the same session,
    even when cwd is not inside the worktree. This prevents blocking when
    a session creates a worktree, leaves it, and tries to operate on its PR
    from the main repository.

    Issue #2496: Added session_id parameter to avoid global state.

    Args:
        worktree_path: Path to the worktree directory.
        session_id: The current session ID for comparison.

    Returns:
        True if the worktree was created by the current session, False otherwise.
    """
    if session_id is None:
        # Without session_id, cannot determine ownership
        return False
    marker_path = worktree_path / SESSION_MARKER_FILE
    try:
        if marker_path.exists():
            return marker_path.read_text().strip() == session_id
    except OSError:
        # File access errors are treated as "not self session" to fail-safe
        pass
    return False


def get_worktree_for_branch(branch: str, base_dir: str | None = None) -> Path | None:
    """Get the worktree path for a given branch.

    Args:
        branch: Branch name to look up.
        base_dir: Optional directory to run git command in.

    Returns:
        Path to the worktree, or None if not found.
    """
    try:
        cmd = ["git"]
        if base_dir:
            cmd.extend(["-C", base_dir])
        cmd.extend(["worktree", "list", "--porcelain"])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )
        if result.returncode != 0:
            return None

        current_worktree = None

        for line in result.stdout.split("\n"):
            if line.startswith("worktree "):
                current_worktree = line[9:]  # Remove "worktree " prefix
            elif line.startswith("branch refs/heads/"):
                current_branch = line[18:]  # Remove "branch refs/heads/" prefix
                if current_branch == branch and current_worktree:
                    return Path(current_worktree)

    except Exception:
        # On any error (timeout, OS error, etc.), treat as "not found" to fail open
        pass
    return None


def get_branch_for_pr(pr_number: str) -> str | None:
    """Get the branch name for a PR.

    Args:
        pr_number: PR number as string.

    Returns:
        Branch name, or None if not found.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                pr_number,
                "--json",
                "headRefName",
                "--jq",
                ".headRefName",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,  # Allow extra time for gh pr view
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        # On any error (timeout, OS error, etc.), treat as "not found" to fail open
        pass
    return None


def check_active_work_signs(worktree_path: Path) -> list[str]:
    """Check for signs of active work in a worktree.

    Args:
        worktree_path: Path to the worktree.

    Returns:
        List of warning messages for detected active work signs.
    """
    warnings: list[str] = []

    has_recent, recent_info = check_recent_commits(worktree_path)
    if has_recent:
        warnings.append(f"最新コミット（1時間以内）: {recent_info}")

    has_changes, change_count = check_uncommitted_changes(worktree_path)
    if has_changes:
        if change_count < 0:  # Timeout case
            warnings.append("未コミット変更: (確認タイムアウト)")
        else:
            warnings.append(f"未コミット変更: {change_count}件")

    return warnings


def get_locked_worktrees() -> list[tuple[Path, str]]:
    """Get list of locked worktrees with their branch names.

    Returns:
        List of tuples: (worktree_path, branch_name)
    """
    locked_worktrees = []

    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return []

        current_worktree = None
        current_branch = None
        is_locked = False

        for line in result.stdout.split("\n"):
            if line.startswith("worktree "):
                # Save previous worktree if it was locked
                if current_worktree and is_locked and current_branch:
                    locked_worktrees.append((Path(current_worktree), current_branch))

                current_worktree = line[9:]  # Remove "worktree " prefix
                current_branch = None
                is_locked = False

            elif line.startswith("branch refs/heads/"):
                current_branch = line[18:]  # Remove "branch refs/heads/" prefix

            elif line == "locked" or line.startswith("locked "):
                # Handle both "locked" and "locked <reason>" formats
                is_locked = True

        # Don't forget the last worktree
        if current_worktree and is_locked and current_branch:
            locked_worktrees.append((Path(current_worktree), current_branch))

    except Exception:
        # Fail open: return empty list on error to avoid blocking
        pass

    return locked_worktrees


def get_pr_for_branch(branch: str) -> str | None:
    """Get PR number for a branch.

    Args:
        branch: Branch name

    Returns:
        PR number as string, or None if no PR exists
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--head",
                branch,
                "--json",
                "number",
                "--jq",
                ".[0].number",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        # Fail open: return None on error
        pass
    return None


def get_current_worktree(cwd: str | None = None) -> Path | None:
    """Get the current worktree path.

    This fixes Issue #317: Hook runs in main repository,
    get_current_worktree() fails to detect correct worktree

    Args:
        cwd: Working directory to run git command in. If None, uses the
             current working directory of the hook process (which may be
             the main repository, not the actual worktree).
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
            cwd=cwd,  # Use the cwd from hook input to get correct worktree
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:
        # Fail open: return None on error
        pass
    return None


def get_current_branch_name(cwd: str | None = None) -> str | None:
    """Get the current branch name.

    Args:
        cwd: Working directory to run git command in.

    Returns:
        Branch name, or None if not on a branch or on error.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
            cwd=cwd,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            branch = result.stdout.strip()
            # HEAD means detached state
            if branch == "HEAD":
                return None
            return branch
    except Exception:
        # Any error while resolving the current branch is treated as "no branch";
        # callers rely on this function returning None instead of raising.
        pass
    return None


def get_all_locked_worktree_paths(base_dir: str | None = None) -> list[Path]:
    """Get list of all locked worktree paths.

    Args:
        base_dir: Optional directory to run git command in (for -C flag support)
    """
    locked_paths = []

    try:
        cmd = ["git"]
        if base_dir:
            cmd.extend(["-C", base_dir])
        cmd.extend(["worktree", "list", "--porcelain"])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return []

        current_worktree = None
        is_locked = False

        for line in result.stdout.split("\n"):
            if line.startswith("worktree "):
                # Save previous worktree if it was locked
                if current_worktree and is_locked:
                    locked_paths.append(Path(current_worktree))

                current_worktree = line[9:]  # Remove "worktree " prefix
                is_locked = False

            elif line == "locked" or line.startswith("locked "):
                is_locked = True

        # Don't forget the last worktree
        if current_worktree and is_locked:
            locked_paths.append(Path(current_worktree))

    except Exception:
        # Fail open: return empty list on error to avoid blocking
        pass

    return locked_paths


def is_cwd_inside_worktree(
    worktree_path: Path,
    cwd: Path | None = None,
    command: str | None = None,
) -> bool:
    """Check if current working directory is inside the worktree.

    Issue #682: Now uses get_effective_cwd() to detect 'cd <path> &&' patterns
    in commands, allowing worktree deletion after cd moves outside.

    Args:
        worktree_path: The worktree path to check against.
        cwd: Current working directory hint. Used as fallback if command
             doesn't contain a cd pattern.
        command: Optional command string to check for 'cd <path> &&' pattern.
                 If provided and contains cd, uses cd target as effective cwd.

    Returns:
        True if cwd is inside the worktree, False otherwise.
    """
    try:
        # Use get_effective_cwd only if command contains cd pattern
        # Otherwise use explicit cwd parameter or fall back to Path.cwd()
        # Issue #1035: Pass cwd as base_cwd for relative cd path resolution
        if command and extract_cd_target_from_command(command):
            cwd_resolved = get_effective_cwd(command, cwd)
        elif cwd is not None:
            cwd_resolved = cwd.resolve()
        else:
            cwd_resolved = Path.cwd().resolve()

        worktree_resolved = worktree_path.resolve()

        # Check if cwd is worktree or a subdirectory
        return cwd_resolved == worktree_resolved or worktree_resolved in cwd_resolved.parents
    except OSError:
        # Fail-close: If path resolution fails (e.g., symlink loop, permission denied),
        # assume we ARE inside the worktree to prevent accidental deletion.
        # This is a security-critical check - better to block unnecessarily than
        # allow deletion of our current working directory.
        return True


def get_main_repo_dir() -> Path | None:
    """Get the main repository directory (not worktree)."""
    try:
        # Get git common dir (main repo's .git)
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            git_common = Path(result.stdout.strip())
            # For regular repos: returns path to .git, parent is repo root
            # For bare repos: returns the repo path, parent is still correct
            # For worktrees: returns main repo's .git path, parent is main repo root
            # In all cases, returning git_common.parent gives us the main repo directory
            return git_common.parent
    except subprocess.TimeoutExpired:
        # Git command timed out
        pass
    except OSError:
        # OS-level error (e.g., git not found, permission denied)
        pass
    return None


def get_all_worktree_paths(base_dir: str | None = None) -> list[Path]:
    """Get list of all worktree paths (including unlocked ones).

    Args:
        base_dir: Optional directory to run git command in (for -C flag support)

    Returns:
        List of worktree paths. First element is the main worktree.
    """
    worktree_paths = []

    try:
        cmd = ["git"]
        if base_dir:
            cmd.extend(["-C", base_dir])
        cmd.extend(["worktree", "list", "--porcelain"])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            return []

        for line in result.stdout.split("\n"):
            if line.startswith("worktree "):
                worktree_paths.append(Path(line[9:]))  # Remove "worktree " prefix

    except Exception:
        # Fail open: return empty list on error to avoid blocking
        pass

    return worktree_paths


def get_orphan_worktree_directories() -> list[Path]:
    """Get directories in .worktrees/ that are NOT registered with git worktree list.

    These are "orphan" worktree directories that exist on the filesystem but are
    not tracked by git. They may contain work from another session that was
    interrupted or corrupted.

    This fixes Issue #795: Block rm -rf on orphan worktree directories.

    Returns:
        List of orphan worktree directory paths.
    """
    orphan_dirs: list[Path] = []

    try:
        # Get the main repository directory
        main_repo = get_main_repo_dir()
        if not main_repo:
            return []

        worktrees_dir = main_repo / ".worktrees"
        if not worktrees_dir.is_dir():
            return []

        # Get all registered worktree paths
        registered_paths = get_all_worktree_paths()
        registered_resolved: set[Path] = set()
        for p in registered_paths:
            try:
                registered_resolved.add(p.resolve())
            except OSError:
                registered_resolved.add(p)

        # Find directories in .worktrees/ that are NOT registered
        for entry in worktrees_dir.iterdir():
            if not entry.is_dir():
                continue

            try:
                entry_resolved = entry.resolve()
            except OSError:
                entry_resolved = entry

            # Check if this directory is NOT in registered worktrees
            if entry_resolved not in registered_resolved:
                orphan_dirs.append(entry_resolved)

    except Exception:
        # Fail open: return empty list on error to avoid blocking
        pass

    return orphan_dirs


def get_rm_target_orphan_worktrees(
    command: str, hook_cwd: str | None = None
) -> list[tuple[Path, Path]]:
    """Get orphan worktree directories that would be deleted by an rm command.

    Similar to get_rm_target_worktrees() but for orphan directories.

    Args:
        command: The command string.
        hook_cwd: Current working directory from hook input.

    Returns:
        List of tuples (rm_target_path, orphan_dir_path) for each orphan
        worktree directory that would be deleted.
    """
    # Extract paths from rm commands using shared helper
    paths = extract_rm_paths(command)
    if not paths:
        return []

    # Get orphan worktree directories
    orphan_dirs = get_orphan_worktree_directories()
    if not orphan_dirs:
        return []

    # Check all rm targets against orphan directories
    target_orphans: list[tuple[Path, Path]] = []

    for path_str in paths:
        path = Path(path_str)
        path = path.expanduser()

        if not path.is_absolute():
            if hook_cwd:
                path = Path(hook_cwd) / path
            else:
                try:
                    path = Path.cwd() / path
                except OSError:
                    continue

        try:
            path = path.resolve()
        except OSError:
            continue

        # Check if path matches any orphan directory
        for orphan_path in orphan_dirs:
            try:
                # Case 1: Deleting the orphan directory itself
                if path == orphan_path:
                    target_orphans.append((path, orphan_path))
                # Case 2: Deleting a parent directory that contains the orphan
                elif path in orphan_path.parents:
                    target_orphans.append((path, orphan_path))
            except OSError:
                continue

    return target_orphans


def get_rm_target_worktrees(command: str, hook_cwd: str | None = None) -> list[tuple[Path, Path]]:
    """Get all worktrees that would be deleted by an rm command.

    Detects patterns like:
    - rm -rf .worktrees/feature-123
    - rm -rf /path/to/.worktrees/feature-123
    - rm .worktrees/feature-123 -rf
    - rm -rf .worktrees/old .worktrees/current (multiple targets)
    - rm -rf .worktrees/old && rm -rf .worktrees/current (chained commands)

    This fixes Issue #289: rm -rf deleting worktree breaks shell

    Args:
        command: The command string.
        hook_cwd: Current working directory from hook input.

    Returns:
        List of tuples (rm_target_path, worktree_path) for each worktree that
        would be deleted. rm_target_path is the actual path being deleted,
        worktree_path is the worktree root.
    """
    # Extract paths from rm commands using shared helper
    paths = extract_rm_paths(command)

    # Get all worktree paths
    worktree_paths = get_all_worktree_paths()
    # Need at least 2 worktrees: main repo (1) + at least one secondary worktree (1)
    if len(worktree_paths) < 2:
        return []

    # Check all rm targets against all worktrees
    target_worktrees: list[tuple[Path, Path]] = []

    for path_str in paths:
        path = Path(path_str)

        # Expand ~ to home directory (e.g., ~/project/.worktrees/foo)
        path = path.expanduser()

        # Resolve relative paths
        if not path.is_absolute():
            if hook_cwd:
                path = Path(hook_cwd) / path
            else:
                try:
                    path = Path.cwd() / path
                except OSError:
                    continue

        try:
            path = path.resolve()
        except OSError:
            continue

        # Check if path matches any worktree (excluding main repo which is first)
        for worktree_path in worktree_paths[1:]:  # Skip main worktree
            try:
                worktree_resolved = worktree_path.resolve()
                # Case 1: Deleting the worktree itself
                if path == worktree_resolved:
                    target_worktrees.append((path, worktree_resolved))
                # Case 2: Deleting a parent directory that contains the worktree
                elif path in worktree_resolved.parents:
                    target_worktrees.append((path, worktree_resolved))
                # Note: Deleting a subdirectory within worktree (worktree_resolved in path.parents)
                # is NOT considered worktree deletion - it's safe unless CWD is in that subdir
            except OSError:
                continue

    return target_worktrees


def is_rm_worktree_command(command: str, hook_cwd: str | None = None) -> tuple[bool, Path | None]:
    """Check if rm command targets a worktree directory.

    Note: This function returns only the first detected worktree for backward
    compatibility. For checking all targets, use get_rm_target_worktrees().

    Args:
        command: The command string.
        hook_cwd: Current working directory from hook input.

    Returns:
        Tuple of (is_rm_worktree, target_path) where target_path is the resolved
        worktree path if detected, None otherwise.
    """
    targets = get_rm_target_worktrees(command, hook_cwd)
    if targets:
        return True, targets[0][1]  # Return first worktree path
    return False, None
