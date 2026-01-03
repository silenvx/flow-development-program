#!/usr/bin/env python3
"""セッション開始時に作業中（未マージ）のworktree一覧を表示する。

Why:
    複数セッション間で同じIssueへの重複着手を防止するため、
    既存の作業状況を把握する必要がある。

What:
    - 作業中のworktree（PRがOPEN/未作成）を検出
    - ブランチ名、PR状態、最終コミット情報を表示
    - 情報提供のみ（ブロックしない）

Remarks:
    - 情報提供型フック（ブロックしない、systemMessageで通知）
    - worktree-session-guardはブロック、本フックは情報提供
    - session-worktree-statusは現在のworktree、本フックは全worktree

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import json
import os
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


def get_worktree_last_commit(worktree_path: Path) -> str | None:
    """Get the last commit info of a worktree."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(worktree_path),
                "log",
                "-1",
                "--format=%h %s",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()[:60]  # Truncate long messages
    except (subprocess.TimeoutExpired, OSError):
        # Git unavailable or timeout - skip this worktree
        pass
    return None


def check_pr_status(branch: str) -> dict | None:
    """Check the PR status for the given branch.

    Returns dict with PR info if exists (any state), None if no PR.
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
                "number,title,state",
                "--limit",
                "1",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            prs = json.loads(result.stdout)
            if prs:
                return prs[0]
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        # gh CLI unavailable, timeout, or invalid response - skip
        pass
    return None


def find_active_worktrees(repo_root: Path) -> list[dict]:
    """Find worktrees that are actively being worked on (not merged).

    Returns list of dicts with worktree info.
    """
    worktrees_dir = repo_root / ".worktrees"
    if not worktrees_dir.exists():
        return []

    active = []

    for item in sorted(worktrees_dir.iterdir(), key=lambda p: p.name):
        if not item.is_dir():
            continue

        branch = get_worktree_branch(item)
        if not branch:
            continue

        pr_info = check_pr_status(branch)

        # Skip merged PRs (handled by merged-worktree-check.py)
        if pr_info and pr_info.get("state") == "MERGED":
            continue

        last_commit = get_worktree_last_commit(item)

        active.append(
            {
                "name": item.name,
                "branch": branch,
                "pr_number": pr_info["number"] if pr_info else None,
                "pr_state": pr_info.get("state") if pr_info else None,
                "last_commit": last_commit,
            }
        )

    return active


def main():
    """PreToolUse hook for Bash commands."""
    # Set session_id for proper logging
    parse_hook_input()

    result = {"decision": "approve"}

    try:
        if check_and_update_session_marker("active-worktree-check"):
            project_dir_str = os.environ.get("CLAUDE_PROJECT_DIR", "")
            if project_dir_str:
                project_dir = Path(project_dir_str)
                repo_root = get_repo_root(project_dir)

                if repo_root:
                    active = find_active_worktrees(repo_root)

                    if active:
                        lines = []
                        # PR状態の日本語マッピング
                        state_ja = {"OPEN": "レビュー中", "CLOSED": "クローズ"}
                        for w in active:
                            if w["pr_number"]:
                                state_display = state_ja.get(w["pr_state"], w["pr_state"])
                                pr_status = f"PR #{w['pr_number']}: {state_display}"
                            else:
                                pr_status = "PRなし"
                            commit_info = f" - {w['last_commit']}" if w["last_commit"] else ""
                            lines.append(
                                f"  - .worktrees/{w['name']} "
                                f"(branch: {w['branch']}, {pr_status}){commit_info}"
                            )
                        active_list = "\n".join(lines)

                        message = (
                            f"📋 **作業中のworktreeがあります**:\n"
                            f"{active_list}\n\n"
                            f"重複着手を避けるため、既存のworktreeを確認してください。"
                        )
                        result["systemMessage"] = message

    except Exception as e:
        print(f"[active-worktree-check] Error: {e}", file=sys.stderr)

    log_hook_execution(
        "active-worktree-check",
        result.get("decision", "approve"),
        result.get("systemMessage"),
    )
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
