#!/usr/bin/env python3
"""セッション終了時にマージ/クローズ済みPRのworktreeクリーンアップを提案。

Why:
    PRがマージ/クローズされた後もworktreeが残ると管理が煩雑になる。
    セッション終了時に提案することで、不要なworktreeの蓄積を防ぐ。

What:
    - セッション終了時（Stop）に発火
    - cwdがworktree内かを確認
    - 関連PRの状態（MERGED/CLOSED）をチェック
    - マージ/クローズ済みならクリーンアップ手順を提案

Remarks:
    - 提案型フック（ブロックせず、systemMessageで案内）
    - ロック中のworktreeはスキップ（別セッション使用中の可能性）
    - worktree-auto-cleanupはマージ直後、本フックはセッション終了時

Changelog:
    - silenvx/dekita#739: フック追加
"""

import json
import shlex
import subprocess
import sys
from pathlib import Path

from lib.constants import TIMEOUT_LIGHT
from lib.execution import log_hook_execution
from lib.session import parse_hook_input


def get_current_worktree_info() -> dict | None:
    """
    Get information about the current worktree if we're inside one.

    Returns:
        Dict with worktree info if inside a worktree, None otherwise.
        {
            "path": "/path/to/worktree",
            "branch": "feat/issue-123-xxx",
            "is_main": False,
            "main_repo": "/path/to/main/repo"
        }
    """
    try:
        # Get the current directory
        cwd = Path.cwd()

        # Get the list of worktrees
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode != 0:
            return None

        worktrees = []
        current_worktree = {}
        for line in result.stdout.strip().split("\n"):
            if line.startswith("worktree "):
                if current_worktree:
                    worktrees.append(current_worktree)
                current_worktree = {"path": line[9:]}
            elif line.startswith("branch refs/heads/"):
                current_worktree["branch"] = line[18:]
            elif line.startswith("HEAD "):
                current_worktree["head"] = line[5:]
        if current_worktree:
            worktrees.append(current_worktree)

        if not worktrees:
            return None

        # The first worktree is always the main one
        main_worktree_path = Path(worktrees[0]["path"])

        # Find the MOST SPECIFIC worktree that contains cwd
        # (worktrees can be nested inside the main repo)
        cwd_resolved = cwd.resolve()
        best_match = None
        best_match_len = -1
        best_match_index = -1

        for i, wt in enumerate(worktrees):
            wt_path = Path(wt["path"]).resolve()
            try:
                cwd_resolved.relative_to(wt_path)
                # cwd is inside this worktree - check if it's more specific
                path_len = len(str(wt_path))
                if path_len > best_match_len:
                    best_match = wt
                    best_match_len = path_len
                    best_match_index = i
            except ValueError:
                continue

        if best_match:
            return {
                "path": str(Path(best_match["path"]).resolve()),
                "branch": best_match.get("branch", "unknown"),
                "is_main": best_match_index == 0,
                "main_repo": str(main_worktree_path.resolve()),
            }

        return None

    except (subprocess.TimeoutExpired, OSError):
        return None


def get_pr_state_for_branch(branch: str) -> str | None:
    """
    Get the PR state for a given branch.

    Returns:
        "MERGED", "CLOSED", "OPEN", or None if no PR found.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "all",
                "--head",
                branch,
                "--json",
                "state",
                "--jq",
                ".[0].state // empty",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None
    except (subprocess.TimeoutExpired, OSError):
        return None


def check_worktree_locked(worktree_path: str) -> bool:
    """
    Check if a worktree is locked.

    Returns:
        True if locked, False otherwise.
    """
    try:
        lock_file = Path(worktree_path) / ".git"
        if lock_file.is_file():
            # It's a file pointing to the real .git directory
            # Check if there's a lock file in the git worktree directory
            git_dir_content = lock_file.read_text().strip()
            if git_dir_content.startswith("gitdir: "):
                gitdir_path = git_dir_content[8:]
                git_worktree_dir = Path(gitdir_path)
                # If gitdir is relative, resolve it from the .git file's parent
                if not git_worktree_dir.is_absolute():
                    git_worktree_dir = lock_file.parent / git_worktree_dir
                git_worktree_dir = git_worktree_dir.resolve()
                lock_path = git_worktree_dir / "locked"
                return lock_path.exists()
        return False
    except OSError:
        return False


def generate_cleanup_suggestion(worktree_info: dict, pr_state: str) -> str:
    """
    Generate a cleanup suggestion message.
    """
    worktree_path = worktree_info["path"]
    main_repo = worktree_info["main_repo"]
    worktree_name = Path(worktree_path).name

    # Quote paths for shell safety (handles spaces and special characters)
    quoted_main_repo = shlex.quote(main_repo)
    quoted_worktree_path = shlex.quote(worktree_path)

    # Build the cleanup command (one command per line for clarity)
    cleanup_cmd = f"cd {quoted_main_repo}\ngit worktree unlock {quoted_worktree_path}\ngit worktree remove {quoted_worktree_path}"

    return f"""
## 🧹 Worktreeクリーンアップの提案

現在 worktree **{worktree_name}** 内で作業中ですが、関連PRは **{pr_state}** です。

### クリーンアップ手順

以下のコマンドでworktreeを削除できます:

```bash
{cleanup_cmd}
```

または、全てのマージ済みworktreeを一括削除:

```bash
cd {quoted_main_repo} && ./scripts/cleanup-worktrees.sh --force
```

💡 **ヒント**: 上記コマンドを実行してworktreeをクリーンアップしてください。
"""


def main():
    """
    Entry point for the worktree cleanup suggester hook.

    This is a Stop hook that suggests cleanup when:
    1. We're inside a worktree (not main repo)
    2. The associated PR is MERGED or CLOSED
    3. The worktree is not locked (by another session)
    """
    result = None
    try:
        input_json = parse_hook_input()

        # If stop_hook_active is set, approve immediately to avoid infinite retry loops
        if input_json.get("stop_hook_active"):
            result = {
                "ok": True,
                "decision": "approve",
                "reason": "stop_hook_active is set; approving to avoid infinite retry loop.",
            }
            log_hook_execution("worktree-cleanup-suggester", result["decision"], result["reason"])
            print(json.dumps(result))
            return

        # Check if we're inside a worktree
        worktree_info = get_current_worktree_info()

        if not worktree_info:
            # Not in a worktree, nothing to suggest
            result = {
                "ok": True,
                "decision": "approve",
                "reason": "Not inside a worktree.",
            }
        elif worktree_info["is_main"]:
            # Inside main repo, nothing to suggest
            result = {
                "ok": True,
                "decision": "approve",
                "reason": "Inside main repository, no cleanup needed.",
            }
        else:
            # Inside a worktree, check PR state
            branch = worktree_info["branch"]
            pr_state = get_pr_state_for_branch(branch)

            if pr_state in ("MERGED", "CLOSED"):
                # Check if locked
                is_locked = check_worktree_locked(worktree_info["path"])

                if is_locked:
                    result = {
                        "ok": True,
                        "decision": "approve",
                        "reason": "Worktree is locked (another session may be using it).",
                        "systemMessage": f"ℹ️ worktree-cleanup: {worktree_info['path']} はロック中のため、クリーンアップをスキップします。",
                    }
                else:
                    suggestion = generate_cleanup_suggestion(worktree_info, pr_state)
                    result = {
                        "ok": True,
                        "decision": "approve",
                        "reason": f"PR is {pr_state}, cleanup suggested.",
                        "systemMessage": suggestion,
                    }
            elif pr_state == "OPEN":
                result = {
                    "ok": True,
                    "decision": "approve",
                    "reason": "PR is still open, no cleanup needed.",
                }
            else:
                # No PR found or error
                result = {
                    "ok": True,
                    "decision": "approve",
                    "reason": f"No PR found for branch {branch}.",
                }

    except Exception as e:
        # On error, approve to avoid blocking, but log for debugging
        print(f"[worktree-cleanup-suggester] Hook error: {e}", file=sys.stderr)
        result = {"ok": True, "decision": "approve", "reason": f"Hook error: {e}"}

    log_hook_execution(
        "worktree-cleanup-suggester", result.get("decision", "approve"), result.get("reason")
    )
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
