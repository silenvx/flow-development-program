#!/usr/bin/env python3
"""セッション開始時にマージ済みPRのworktreeを検知して警告する。

Why:
    PRがマージされた後もworktreeが残っているとディスクを圧迫し、
    混乱の原因になる。マージ済みworktreeを検知し削除を促す。

What:
    - .worktrees/ディレクトリ内のworktreeを列挙
    - 各worktreeのブランチに関連するPRがマージ済みか確認
    - マージ済みworktreeがあればsystemMessageで警告

Remarks:
    - 警告型フック（ブロックしない、systemMessageで情報提供）
    - PreToolUse:Bashで発火（セッション毎に1回）
    - orphan-worktree-check.pyはgit未登録worktreeを検出（責務分離）
    - gh pr viewでマージ済み判定（リモートブランチ削除後も動作）

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#914: gh pr viewでマージ済み判定（branch削除後対応）
"""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from common import check_and_update_session_marker
from lib.constants import TIMEOUT_LIGHT, TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.repo import get_repo_root
from lib.session import parse_hook_input


def get_worktree_branch(worktree_path: Path) -> str | None:
    """Get the branch name of a worktree."""
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree_path), "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        # Git unavailable or timeout - skip this worktree
        pass
    return None


def check_pr_merged(branch: str) -> dict | None:
    """Check if there's a merged PR for the given branch.

    Returns dict with PR info if merged, None otherwise.

    Implementation note (Issue #914):
        `gh pr list --head <branch> --state merged` fails when the remote branch
        has been deleted after merge. Instead, we use `gh pr view <branch>` which
        queries by branch name in the PR database and works even after branch deletion.
    """
    try:
        # Use gh pr view to find PR for this branch (works even if remote branch deleted)
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                branch,
                "--json",
                "number,title,mergedAt",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if data.get("mergedAt"):  # mergedAt is set when PR is merged
                return {
                    "number": data.get("number"),
                    "title": data.get("title"),
                    "mergedAt": data.get("mergedAt"),
                }
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        # gh CLI unavailable, timeout, or invalid response - skip
        pass
    return None


def find_merged_worktrees(repo_root: Path) -> list[dict]:
    """Find worktrees whose PRs have been merged.

    Returns list of dicts with worktree info.
    """
    worktrees_dir = repo_root / ".worktrees"
    if not worktrees_dir.exists():
        return []

    merged = []

    for item in worktrees_dir.iterdir():
        if not item.is_dir():
            continue

        branch = get_worktree_branch(item)
        if not branch:
            continue

        pr_info = check_pr_merged(branch)
        if pr_info:
            merged.append(
                {
                    "name": item.name,
                    "branch": branch,
                    "pr_number": pr_info["number"],
                    "pr_title": pr_info.get("title", ""),
                }
            )

    return merged


def main():
    """PreToolUse hook for Bash commands."""
    # Set session_id for proper logging
    parse_hook_input()

    result = {"decision": "approve"}

    try:
        if check_and_update_session_marker("merged-worktree-check"):
            project_dir_str = os.environ.get("CLAUDE_PROJECT_DIR", "")
            if project_dir_str:
                project_dir = Path(project_dir_str)
                repo_root = get_repo_root(project_dir)

                if repo_root:
                    merged = find_merged_worktrees(repo_root)

                    if merged:
                        lines = []
                        for m in merged:
                            lines.append(
                                f"  - .worktrees/{m['name']} (PR #{m['pr_number']}: MERGED)"
                            )
                        merged_list = "\n".join(lines)

                        # Generate cleanup commands
                        cleanup_cmds = []
                        for m in merged:
                            name = shlex.quote(f".worktrees/{m['name']}")
                            cleanup_cmds.append(
                                f"git worktree unlock {name} 2>/dev/null; "
                                f"git worktree remove {name}"
                            )
                        cleanup = "\n".join(cleanup_cmds)

                        message = (
                            f"⚠️ **マージ済みPRのworktreeが残っています**:\n"
                            f"{merged_list}\n\n"
                            f"削除コマンド:\n```\n{cleanup}\n```"
                        )
                        result["systemMessage"] = message

    except Exception as e:
        print(f"[merged-worktree-check] Error: {e}", file=sys.stderr)

    log_hook_execution(
        "merged-worktree-check",
        result.get("decision", "approve"),
        result.get("systemMessage"),
    )
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
