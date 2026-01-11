#!/usr/bin/env python3
"""セッション開始時にworktree内のコミット整合性をチェック。

Why:
    セッション引き継ぎ時、worktreeに複数Issueの変更が混在していると
    状態が混乱する。開始時にチェックして問題を早期に警告する。

What:
    - セッション開始時（SessionStart）に発火
    - main..HEADのコミット履歴を取得
    - コミットメッセージからIssue番号を抽出
    - 複数Issue混在やマージ済みコミットがあれば警告

State:
    - writes: .claude/logs/flow/worktree-integrity-*.jsonl

Remarks:
    - 警告型フック（ブロックしない）
    - cwdがworktree外ならスキップ
    - session-worktree-statusは一般警告、本フックはコミット内容分析

Changelog:
    - silenvx/dekita#1691: フック追加
    - silenvx/dekita#1840: セッション別ログファイル移行
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from common import FLOW_LOG_DIR
from lib.constants import TIMEOUT_LIGHT, TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.logging import log_to_session_file
from lib.session import create_hook_context, parse_hook_input
from lib.timestamp import get_local_timestamp

# Worktrees directory name
WORKTREES_DIR = ".worktrees"

# Pattern to extract Issue numbers from commit messages
ISSUE_PATTERN = re.compile(r"#(\d+)")


def is_in_worktree() -> tuple[bool, str | None]:
    """Check if CWD is inside a worktree.

    Returns:
        Tuple of (is_in_worktree, worktree_name or None)
    """
    try:
        cwd = Path.cwd()
    except OSError:
        return False, None

    parts = cwd.parts
    for i, part in enumerate(parts):
        if part == WORKTREES_DIR:
            if i + 1 < len(parts):
                return True, parts[i + 1]
    return False, None


def get_commits_since_main() -> tuple[list[dict[str, str]], str | None]:
    """Get list of commits since diverging from main.

    Returns:
        Tuple of (commits list, error message or None).
        - On success: (commits, None)
        - On git error: ([], error_message)
    """
    try:
        # Get commit hashes and subjects
        result = subprocess.run(
            ["git", "log", "--oneline", "main..HEAD"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode != 0:
            # Git command failed - could be missing main ref or other error
            error_msg = result.stderr.strip() or "git log failed"
            return [], error_msg

        commits = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split(" ", 1)
            if len(parts) >= 2:
                commit_hash, subject = parts[0], parts[1]
            else:
                commit_hash, subject = parts[0], ""

            # Get full commit message body for Issue extraction
            body_result = subprocess.run(
                ["git", "log", "-1", "--format=%b", commit_hash],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_LIGHT,
            )
            body = body_result.stdout.strip() if body_result.returncode == 0 else ""

            commits.append({"hash": commit_hash, "subject": subject, "body": body})

        return commits, None
    except subprocess.TimeoutExpired:
        return [], "git command timed out"
    except OSError as e:
        return [], f"OS error: {e}"


def extract_issue_numbers(commits: list[dict[str, str]]) -> dict[int, list[str]]:
    """Extract Issue numbers from commits.

    Returns:
        Dict mapping Issue number to list of commit hashes referencing it.
    """
    issue_to_commits: dict[int, list[str]] = {}

    for commit in commits:
        # Search in both subject and body
        text = f"{commit['subject']} {commit['body']}"
        matches = ISSUE_PATTERN.findall(text)
        for match in matches:
            issue_num = int(match)
            if issue_num not in issue_to_commits:
                issue_to_commits[issue_num] = []
            if commit["hash"] not in issue_to_commits[issue_num]:
                issue_to_commits[issue_num].append(commit["hash"])

    return issue_to_commits


def check_merged_commits(commits: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Check if any commits are already merged to main.

    Returns:
        List of commits that are already merged.
    """
    merged = []
    for commit in commits:
        try:
            # Check if commit is in main
            result = subprocess.run(
                ["git", "branch", "--contains", commit["hash"], "-r"],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_LIGHT,
            )
            if result.returncode == 0 and "origin/main" in result.stdout:
                merged.append(commit)
        except (subprocess.TimeoutExpired, OSError):
            continue
    return merged


def get_git_status() -> str:
    """Get git status output.

    Returns:
        Git status output string.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--short"], capture_output=True, text=True, timeout=TIMEOUT_LIGHT
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, OSError):
        return ""


def log_worktree_state(
    ctx,
    worktree_name: str,
    commits: list[dict[str, str]],
    issue_map: dict[int, list[str]],
    merged_commits: list[dict[str, Any]],
    git_status: str,
) -> None:
    """Log worktree state to flow log directory.

    Issue #1840: Now writes to session-specific file.
    """
    session_id = ctx.get_session_id()
    entry = {
        "timestamp": get_local_timestamp(),
        "worktree": worktree_name,
        "commit_count": len(commits),
        "commits": [{"hash": c["hash"], "subject": c["subject"][:80]} for c in commits[:10]],
        "issue_numbers": list(issue_map.keys()),
        "multiple_issues": len(issue_map) > 1,
        "merged_commit_count": len(merged_commits),
        "merged_commits": [c["hash"] for c in merged_commits],
        "has_uncommitted_changes": bool(git_status),
        "git_status_lines": len(git_status.split("\n")) if git_status else 0,
    }

    # Issue #1840: Write to session-specific file
    log_to_session_file(FLOW_LOG_DIR, "worktree-integrity", session_id, entry)


def main():
    """SessionStart hook to check worktree commit integrity."""
    try:
        input_data = parse_hook_input()
        ctx = create_hook_context(input_data)

        # Check if in worktree
        in_worktree, worktree_name = is_in_worktree()
        if not in_worktree:
            log_hook_execution("worktree-commit-integrity-check", "skip", "Not in worktree")
            print(json.dumps({"continue": True}))
            return

        # Get commits since main
        commits, git_error = get_commits_since_main()
        if git_error:
            # Git error - warn user instead of silently approving
            log_hook_execution(
                "worktree-commit-integrity-check",
                "warn",
                f"Git error in {worktree_name}: {git_error}",
            )
            warning_msg = (
                f"[worktree-commit-integrity-check] **{worktree_name}** gitエラー\n\n"
                f"コミット履歴の取得に失敗しました: {git_error}\n\n"
                "整合性チェックが実行できません。以下を確認してください:\n"
                "- `git fetch origin main` でmainブランチを取得\n"
                "- `git log main..HEAD` が正常に動作するか確認"
            )
            print(json.dumps({"continue": True, "systemMessage": warning_msg}))
            return
        if not commits:
            log_hook_execution(
                "worktree-commit-integrity-check",
                "approve",
                f"Worktree {worktree_name}: no commits since main",
            )
            print(json.dumps({"continue": True}))
            return

        # Extract Issue numbers
        issue_map = extract_issue_numbers(commits)

        # Check for merged commits
        merged_commits = check_merged_commits(commits)

        # Get git status
        git_status = get_git_status()

        # Log state to flow logs
        log_worktree_state(
            ctx, worktree_name or "unknown", commits, issue_map, merged_commits, git_status
        )

        # Build warning message
        warnings: list[str] = []

        # Multiple Issues warning
        if len(issue_map) > 1:
            issue_list = ", ".join(f"#{num}" for num in sorted(issue_map.keys()))
            warnings.append(
                f"**複数のIssueが検出されました**: {issue_list}\n"
                "  - worktree内に複数Issueの変更が混在しています\n"
                "  - リベース時にコンフリクトが発生する可能性があります\n"
                "  - 関係のないコミットを `git rebase -i` で除外することを検討してください"
            )

        # Merged commits warning
        if merged_commits:
            merged_list = ", ".join(c["hash"] for c in merged_commits[:3])
            warnings.append(
                f"**既にマージ済みのコミットがあります**: {merged_list}\n"
                "  - これらのコミットはmainに既にマージされています\n"
                "  - `git rebase main` でコンフリクトが発生する可能性が高いです\n"
                "  - `git rebase -i main` で該当コミットをdropすることを検討してください"
            )

        if warnings:
            message_parts = [
                f"[worktree-commit-integrity-check] **{worktree_name}** の状態確認",
                "",
                f"コミット数: {len(commits)} (main..HEAD)",
            ]

            if issue_map:
                message_parts.append(
                    f"関連Issue: {', '.join(f'#{n}' for n in sorted(issue_map.keys()))}"
                )

            message_parts.append("")
            message_parts.extend(warnings)

            log_hook_execution(
                "worktree-commit-integrity-check",
                "warn",
                f"Integrity issues found in {worktree_name}",
                {
                    "commit_count": len(commits),
                    "issue_count": len(issue_map),
                    "merged_count": len(merged_commits),
                },
            )
            print(json.dumps({"continue": True, "systemMessage": "\n".join(message_parts)}))
        else:
            # No warnings but log the state
            log_hook_execution(
                "worktree-commit-integrity-check",
                "approve",
                f"Worktree {worktree_name}: {len(commits)} commits, {len(issue_map)} issues",
                {
                    "commit_count": len(commits),
                    "issue_count": len(issue_map),
                    "issues": list(issue_map.keys()),
                },
            )
            print(json.dumps({"continue": True}))

    except Exception as e:
        # Fail open - don't block on errors
        print(f"[worktree-commit-integrity-check] Error: {e}", file=sys.stderr)
        print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
