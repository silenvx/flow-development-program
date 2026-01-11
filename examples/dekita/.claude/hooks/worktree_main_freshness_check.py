#!/usr/bin/env python3
"""worktree作成前にmainブランチが最新か確認。

Why:
    PRマージ後にローカルmainをpullし忘れると、古いコードをベースに
    worktreeが作成される。事前チェックで最新化を強制する。

What:
    - git worktree add ... main コマンド実行前（PreToolUse:Bash）に発火
    - worktree内からのworktree作成を検出してブロック（ネスト防止）
    - origin/mainをfetchしてローカルmainと比較
    - 遅れている場合は自動pull、失敗時はブロック

Remarks:
    - ブロック型フック（mainが古い場合はブロック）
    - git -Cオプションをサポート（worktree内からメインリポジトリ指定可）
    - 自動pullを試行、失敗時のみ手動対応を要求

Changelog:
    - silenvx/dekita#755: フック追加
    - silenvx/dekita#822: ネストworktree防止追加
    - silenvx/dekita#845: 自動pull機能追加
    - silenvx/dekita#1398: --no-rebase追加
    - silenvx/dekita#1405: git -Cオプションサポート
"""

import json
import shlex
import subprocess
import sys
from pathlib import Path

from lib.constants import CONTINUATION_HINT, TIMEOUT_LIGHT, TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.results import make_approve_result, make_block_result
from lib.session import parse_hook_input

# =============================================================================
# RELATED FUNCTIONS: git_c_directory parameter
# =============================================================================
# The following functions all accept git_c_directory parameter to support
# running git commands in a different directory (git -C option).
# When modifying one of these functions, consider if the same change is
# needed in the others:
#
# - is_cwd_inside_worktree() - Checks if directory is inside worktree
# - fetch_origin_main() - Fetches origin/main
# - get_commit_hash() - Gets commit hash for a ref
# - get_behind_count() - Gets how many commits behind
# - get_current_branch() - Gets current branch name
# - try_auto_pull_main() - Tries to auto-update main
#
# Note: extract_git_c_directory() is related but it *extracts* the -C value
# from a command string rather than *accepting* it as a parameter.
#
# Issue #1306: This comment was added to prevent related function changes
# from being overlooked when modifying one function.
# =============================================================================


def extract_git_c_directory(command: str) -> str | None:
    """Extract directory from 'git -C /path' option.

    Issue #1405: Support git -C option to allow worktree creation from
    inside another worktree by specifying the main repository path.

    Args:
        command: The git command string.

    Returns:
        The directory path specified with -C, or None if not present.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        # Invalid shell syntax (e.g., unclosed quotes)
        return None

    # Find 'git' command and look for -C option
    # Note: This handles single git commands. Chained commands (&&, ||, ;) are not
    # fully supported - only the first git command is processed.
    git_index = -1
    for i, token in enumerate(tokens):
        # Support both 'git' and full path like '/usr/bin/git'
        if Path(token).name == "git":
            git_index = i
            break

    if git_index == -1:
        return None

    # Look for -C option after 'git'
    # Skip options that take arguments (like -c key=value, --git-dir=path)
    flags_with_args = {"-c", "-C", "--git-dir", "--work-tree", "--namespace"}
    i = git_index + 1
    while i < len(tokens):
        token = tokens[i]
        if token == "-C" and i + 1 < len(tokens):
            return tokens[i + 1]
        # Handle -C/path format (no space)
        if token.startswith("-C") and len(token) > 2:
            return token[2:]
        # Skip options that take arguments
        if token in flags_with_args and token != "-C":
            i += 2  # Skip the flag and its argument
            continue
        # Handle --option=value format
        if token.startswith("--") and "=" in token:
            i += 1
            continue
        # Stop at subcommand (non-option argument)
        if not token.startswith("-"):
            break
        i += 1

    return None


def is_cwd_inside_worktree(git_c_directory: str | None = None) -> tuple[bool, Path | None]:
    """Check if current working directory (or -C directory) is inside a git worktree.

    Issue #822: Prevent creating nested worktrees.
    Issue #1405: Support git -C option to check a specific directory.

    Uses git rev-parse --show-toplevel to find the repository root,
    then checks if it's a worktree (even when run from subdirectories).

    Args:
        git_c_directory: Optional directory to check instead of cwd.
            If provided, uses `git -C <directory>` to run commands.

    Returns:
        Tuple of (is_inside_worktree, main_repo_path).
        main_repo_path is the path to the main repository if inside a worktree.
    """
    # First, find the repository root using git
    try:
        # Issue #1405: Use -C option if git_c_directory is specified
        if git_c_directory:
            cmd = ["git", "-C", git_c_directory, "rev-parse", "--show-toplevel"]
        else:
            cmd = ["git", "rev-parse", "--show-toplevel"]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode != 0:
            return False, None
        repo_root = Path(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, None

    git_path = repo_root / ".git"

    # In a worktree, .git is a file (not a directory) containing:
    # gitdir: /path/to/main/.git/worktrees/xxx
    if git_path.is_file():
        try:
            content = git_path.read_text().strip()
            if content.startswith("gitdir:"):
                # Extract main repo path from gitdir
                # Format: gitdir: /path/to/main/.git/worktrees/xxx
                gitdir_path = content.split(":", 1)[1].strip()
                gitdir = Path(gitdir_path)
                # Go up from .git/worktrees/xxx to get main repo
                # /path/to/main/.git/worktrees/xxx -> /path/to/main
                # Use Path.parts for cross-platform compatibility (Windows uses \)
                if "worktrees" in gitdir.parts:
                    worktrees_index = gitdir.parts.index("worktrees")
                    main_git_dir = Path(*gitdir.parts[:worktrees_index])
                    main_repo = main_git_dir.parent
                    return True, main_repo
        except (OSError, IndexError):
            # `.git`ファイルの読み込みやパースに失敗した場合は、
            # ワークツリーではないものとして扱う（フェイルオープン）
            pass

    return False, None


def is_worktree_add_from_main(command: str) -> bool:
    """Check if command is 'git worktree add' using main as base."""
    # Patterns to match:
    # - git worktree add .worktrees/xxx -b branch main
    # - git worktree add .worktrees/xxx main
    # - SKIP_PLAN=1 git worktree add ... main
    if "worktree add" not in command:
        return False

    # Check if 'main' or 'origin/main' appears as base branch
    # Split by spaces and check for 'main' or 'origin/main' as a standalone word
    parts = command.split()
    for i, part in enumerate(parts):
        if part == "main" or part == "origin/main":
            # Make sure it's the base branch, not part of a path
            # It should not be immediately after -b (which would be the new branch name)
            if i > 0 and parts[i - 1] == "-b":
                continue  # This is the new branch name, not base
            return True
    return False


def fetch_origin_main(git_c_directory: str | None = None) -> bool:
    """Fetch origin/main to get latest remote state.

    Args:
        git_c_directory: Optional directory to run git command in (git -C).
    """
    try:
        cmd = ["git"]
        if git_c_directory:
            cmd.extend(["-C", git_c_directory])
        cmd.extend(["fetch", "origin", "main"])
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def get_commit_hash(ref: str, git_c_directory: str | None = None) -> str | None:
    """Get commit hash for a ref.

    Args:
        ref: Git reference (branch, tag, commit hash).
        git_c_directory: Optional directory to run git command in (git -C).
    """
    try:
        cmd = ["git"]
        if git_c_directory:
            cmd.extend(["-C", git_c_directory])
        cmd.extend(["rev-parse", ref])
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # rev-parseの失敗時はNoneを返し、呼び出し元でフェイルオープン処理を行う
        pass
    return None


def get_behind_count(git_c_directory: str | None = None) -> int:
    """Get how many commits main is behind origin/main.

    Args:
        git_c_directory: Optional directory to run git command in (git -C).
    """
    try:
        cmd = ["git"]
        if git_c_directory:
            cmd.extend(["-C", git_c_directory])
        cmd.extend(["rev-list", "--count", "main..origin/main"])
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        # rev-listの失敗時はbehind数が取得できないが、フック全体をブロックしないため0扱いにする
        pass
    return 0


def get_current_branch(git_c_directory: str | None = None) -> str | None:
    """Get the currently checked-out branch name.

    Args:
        git_c_directory: Optional directory to run git command in (git -C).

    Returns:
        Branch name, or None if not on a branch (detached HEAD) or error.
    """
    try:
        cmd = ["git"]
        if git_c_directory:
            cmd.extend(["-C", git_c_directory])
        cmd.extend(["rev-parse", "--abbrev-ref", "HEAD"])
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            # "HEAD" is returned for detached HEAD state
            return branch if branch != "HEAD" else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # ブランチ名取得に失敗した場合は、docstring どおり None を返す
        # （フック全体をブロックしないフェイルオープン戦略）
        pass
    return None


def try_auto_pull_main(git_c_directory: str | None = None) -> tuple[bool, str]:
    """Try to update local main branch ref from origin/main.

    Issue #845: Automatically update main instead of blocking.

    Strategy:
    - If on main branch: use `git pull --ff-only origin main` (updates working tree)
    - If not on main: use `git fetch origin main:main` (updates ref only)

    Args:
        git_c_directory: Optional directory to run git command in (git -C).

    Returns:
        Tuple of (success, message).
        success is True if update succeeded, False otherwise.
        message contains details about the result.
    """
    try:
        current_branch = get_current_branch(git_c_directory)

        if current_branch == "main":
            # On main branch - use pull to update both ref and working tree
            # Issue #1398: Use --no-rebase to override user's pull.rebase config
            cmd = ["git"]
            if git_c_directory:
                cmd.extend(["-C", git_c_directory])
            cmd.extend(["pull", "--ff-only", "--no-rebase", "origin", "main"])
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_MEDIUM,
            )
        else:
            # Not on main (or detached HEAD/error where current_branch is None)
            # Use fetch with refspec to update only the ref
            # This is safe because it only updates the main ref when fast-forward is possible
            cmd = ["git"]
            if git_c_directory:
                cmd.extend(["-C", git_c_directory])
            cmd.extend(["fetch", "origin", "main:main"])
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_MEDIUM,
            )

        if result.returncode == 0:
            return True, "mainブランチを自動更新しました"
        else:
            # Update failed (likely due to non-fast-forward or conflicts)
            error_msg = result.stderr.strip() if result.stderr else result.stdout.strip()
            return False, f"自動更新に失敗: {error_msg}"
    except subprocess.TimeoutExpired:
        return False, "自動更新がタイムアウトしました"
    except FileNotFoundError:
        return False, "gitコマンドが見つかりません"


def main():
    """PreToolUse hook for Bash commands."""
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Only check git worktree add commands that use main as base
        # Log and skip silently if not a target command (no output per design principle)
        if not is_worktree_add_from_main(command):
            log_hook_execution(
                "worktree-main-freshness-check", "skip", "Not a worktree add from main"
            )
            sys.exit(0)

        # Issue #1405: Extract git -C directory from command
        git_c_dir = extract_git_c_directory(command)

        # Issue #822: Check if cwd or the directory specified by -C is inside a worktree
        # Issue #1405: If git -C points to the main repo (not a worktree), allow
        # worktree creation even from a worktree session
        inside_worktree, main_repo = is_cwd_inside_worktree(git_c_dir)
        if inside_worktree:
            # The target directory (git_c_dir if specified, otherwise cwd) is inside
            # a worktree. Block the command and suggest using git -C to main repo.
            reason = (
                "worktree内からworktreeを作成しようとしています。\n\n"
                "これによりネストされたworktreeが作成され、管理が複雑になります。\n\n"
                "メインリポジトリを指定してworktreeを作成してください:\n\n"
                "```bash\n"
                f"git -C {main_repo} worktree add ...\n"
                "```" + CONTINUATION_HINT
            )
            log_hook_execution(
                "worktree-main-freshness-check",
                "block",
                f"Attempted to create worktree from inside worktree "
                f"(cwd: {Path.cwd()}, git_c_dir: {git_c_dir})",
            )
            result = make_block_result("worktree-main-freshness-check", reason)
            print(json.dumps(result))
            sys.exit(0)

        # Fetch latest origin/main
        # Issue #1405: Use git_c_dir to fetch from the correct repository
        if not fetch_origin_main(git_c_dir):
            # Failed to fetch, approve anyway (fail open, log only)
            log_hook_execution(
                "worktree-main-freshness-check",
                "approve",
                "Failed to fetch origin/main, allowing command",
            )
            sys.exit(0)

        # Compare local main with origin/main
        # Issue #1405: Use git_c_dir to get commit hashes from the correct repository
        local_hash = get_commit_hash("main", git_c_dir)
        remote_hash = get_commit_hash("origin/main", git_c_dir)

        if not local_hash or not remote_hash:
            # Can't compare, approve anyway (fail open, log only)
            log_hook_execution(
                "worktree-main-freshness-check",
                "approve",
                "Could not get commit hashes, allowing command",
            )
            sys.exit(0)

        # Check if main is behind origin/main
        # Issue #1405: Use git_c_dir to check the correct repository
        behind_count = get_behind_count(git_c_dir)

        if behind_count == 0:
            # main is up to date or ahead of origin/main - allow silently
            log_hook_execution(
                "worktree-main-freshness-check",
                "approve",
                "main is up to date or ahead of origin/main",
            )
            sys.exit(0)

        # main is behind origin/main (behind_count > 0)
        # Issue #845: Try to auto-pull instead of blocking
        # Issue #1405: Use git_c_dir to update the correct repository
        pull_success, pull_message = try_auto_pull_main(git_c_dir)

        if pull_success:
            # Auto-pull succeeded, approve the command
            log_hook_execution(
                "worktree-main-freshness-check",
                "approve",
                f"Auto-pulled main ({behind_count} commits): {pull_message}",
            )
            result = make_approve_result(
                "worktree-main-freshness-check",
                f"✅ {pull_message}（{behind_count}コミット更新）",
            )
            print(json.dumps(result))
            sys.exit(0)

        # Auto-pull failed, block with helpful message
        reason = (
            f"mainブランチが古いです（{behind_count}コミット遅れ）。\n\n"
            f"ローカル main: {local_hash[:8]}\n"
            f"リモート main: {remote_hash[:8]}\n\n"
            f"自動更新を試みましたが失敗しました:\n{pull_message}\n\n"
            "手動で以下のコマンドを実行してください:\n\n"
            "```bash\n"
            "git pull origin main\n"
            "```\n\n"
            "その後、再度worktreeを作成してください。" + CONTINUATION_HINT
        )
        log_hook_execution(
            "worktree-main-freshness-check",
            "block",
            f"main is {behind_count} commits behind, auto-pull failed: {pull_message}",
        )
        result = make_block_result("worktree-main-freshness-check", reason)
        print(json.dumps(result))
        sys.exit(0)

    except Exception as e:
        # On error, approve to avoid blocking (fail open)
        error_msg = f"Hook error: {e}"
        print(f"[worktree-main-freshness-check] {error_msg}", file=sys.stderr)
        result = make_approve_result("worktree-main-freshness-check", error_msg)
        print(json.dumps(result))
        sys.exit(0)


if __name__ == "__main__":
    main()
