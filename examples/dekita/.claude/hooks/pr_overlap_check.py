#!/usr/bin/env python3
"""git push/gh pr create時に他PRとのファイル重複を警告。

Why:
    複数PRが同じファイルを変更するとマージ時にコンフリクトが発生する。
    push/PR作成時点で重複を検知し、早期に調整できるようにする。

What:
    - git push / gh pr create コマンドを検出
    - 現在のブランチで変更されたファイルを取得
    - オープン中の他PRの変更ファイルと比較
    - 重複があればsystemMessageで警告

Remarks:
    - 非ブロック型（警告のみ）
    - gh CLI 2.35.0+が必要（files取得のため）
    - 最大50件のPRをチェック

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import json
import re
import subprocess
import sys
from functools import lru_cache

from lib.constants import TIMEOUT_EXTENDED, TIMEOUT_HEAVY
from lib.execution import log_hook_execution
from lib.git import get_current_branch
from lib.session import parse_hook_input
from lib.strings import strip_quoted_strings


def is_push_or_pr_create(command: str) -> bool:
    """Check if command is git push or gh pr create.

    Returns False for:
    - Commands inside quoted strings (e.g., echo 'git push')
    - Empty commands
    """
    if not command.strip():
        return False

    stripped = strip_quoted_strings(command)

    # Check for git push
    if re.search(r"\bgit\s+push\b", stripped):
        return True

    # Check for gh pr create
    if re.search(r"\bgh\s+pr\s+create\b", stripped):
        return True

    return False


@lru_cache(maxsize=1)
def get_current_branch_files() -> set[str]:
    """Get files changed in current branch compared to origin/main."""
    try:
        # Get changed files compared to origin/main
        result = subprocess.run(
            ["git", "diff", "--name-only", "origin/main...HEAD"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_HEAVY,
        )
        if result.returncode != 0:
            return set()

        files = set(result.stdout.strip().split("\n")) if result.stdout.strip() else set()
        return files
    except Exception:
        return set()


def get_open_pr_files() -> dict[str, list[str]]:
    """Get files changed in each open PR.

    Note: This uses `gh pr list --json files` which requires gh CLI 2.35.0+.
    On older versions, the command will fail and return empty dict (no warnings).

    Returns:
        Dict mapping PR number (e.g., "#123") to list of changed files.
    """
    try:
        # Get list of open PRs with their changed files
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--json",
                "number,headRefName,files",
                "--limit",
                "50",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_EXTENDED,
        )
        if result.returncode != 0:
            return {}

        prs = json.loads(result.stdout)
        current_branch = get_current_branch()

        pr_files: dict[str, list[str]] = {}
        for pr in prs:
            # Skip current branch's PR
            if pr.get("headRefName") == current_branch:
                continue

            pr_number = f"#{pr.get('number')}"
            files = [f.get("path", "") for f in pr.get("files", [])]
            if files:
                pr_files[pr_number] = files

        return pr_files
    except Exception:
        return {}


def find_overlapping_files(
    current_files: set[str], pr_files: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Find files that overlap between current branch and other PRs.

    Returns:
        Dict mapping PR number to list of overlapping files.
    """
    overlaps: dict[str, list[str]] = {}

    for pr_number, files in pr_files.items():
        overlapping = current_files.intersection(files)
        if overlapping:
            overlaps[pr_number] = sorted(overlapping)

    return overlaps


def format_warning(overlaps: dict[str, list[str]]) -> str:
    """Format the overlap warning message."""
    lines = ["⚠️ File overlap detected with other open PRs:\n"]

    for pr_number, files in sorted(overlaps.items()):
        lines.append(f"  {pr_number}:")
        for f in files[:5]:  # Limit to 5 files per PR
            lines.append(f"    - {f}")
        if len(files) > 5:
            lines.append(f"    ... and {len(files) - 5} more files")
        lines.append("")

    lines.append("Consider coordinating with these PRs to avoid merge conflicts.")
    lines.append("Tip: Merge or rebase frequently to minimize conflict scope.")

    return "\n".join(lines)


def main():
    """PreToolUse hook for Bash commands.

    Warns when pushing or creating PR if files overlap with other open PRs.
    """
    result = {"decision": "approve"}

    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Only check push/PR create commands
        if not is_push_or_pr_create(command):
            log_hook_execution("pr-overlap-check", "approve", "Not push/PR create")
            print(json.dumps(result))
            sys.exit(0)

        # Get current branch's changed files
        current_files = get_current_branch_files()
        if not current_files:
            log_hook_execution("pr-overlap-check", "approve", "No changed files")
            print(json.dumps(result))
            sys.exit(0)

        # Get other PRs' changed files
        pr_files = get_open_pr_files()
        if not pr_files:
            log_hook_execution("pr-overlap-check", "approve", "No other PRs")
            print(json.dumps(result))
            sys.exit(0)

        # Find overlaps
        overlaps = find_overlapping_files(current_files, pr_files)

        if overlaps:
            warning = format_warning(overlaps)
            result["systemMessage"] = warning

            overlap_count = sum(len(files) for files in overlaps.values())
            log_hook_execution(
                "pr-overlap-check",
                "approve",
                f"Warning: {overlap_count} overlapping file(s) in {len(overlaps)} PR(s)",
                {"overlaps": overlaps},
            )
        else:
            log_hook_execution("pr-overlap-check", "approve", "No overlaps")

    except Exception as e:
        error_msg = f"Hook error: {e}"
        print(f"[pr-overlap-check] {error_msg}", file=sys.stderr)
        log_hook_execution("pr-overlap-check", "approve", error_msg)

    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
