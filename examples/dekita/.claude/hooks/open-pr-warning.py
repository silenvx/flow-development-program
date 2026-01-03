#!/usr/bin/env python3
"""セッション開始時にオープンPRと関連worktreeを表示し介入を防止する。

Why:
    別セッションが作業中のPR/Issueに介入するとコンフリクトや
    重複作業が発生する。オープンPRを表示し介入を防止する。

What:
    - オープンPRの一覧を取得
    - 各PRに関連するworktreeを特定
    - ロックされたworktree（PRなし）も検出
    - セッション開始時に警告メッセージを表示

Remarks:
    - 警告型フック（ブロックしない、判断はエージェントに委ねる）
    - SessionStartで発火
    - session-handoff-readerは前回セッション引き継ぎ（補完関係）
    - active-worktree-checkはPreToolUseでの確認（タイミング違い）

Changelog:
    - silenvx/dekita#673: フック追加
    - silenvx/dekita#1095: PRに関連しないロックworktreeも表示
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# 共通モジュール
HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))
from lib.execution import log_hook_execution
from lib.session import parse_hook_input


def get_open_prs() -> tuple[list[dict[str, Any]], str | None]:
    """オープンPRを取得

    Returns:
        Tuple of (prs_list, error_message).
        If successful, error_message is None.
        If failed, prs_list is empty and error_message describes the failure.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--json",
                "number,title,headRefName,author",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return json.loads(result.stdout), None
        return [], f"gh pr list failed: {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return [], "gh pr list timed out"
    except json.JSONDecodeError as e:
        return [], f"Failed to parse PR list: {e}"
    except OSError as e:
        return [], f"Failed to run gh command: {e}"


def get_worktrees() -> list[dict[str, str]]:
    """worktree一覧を取得"""
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []

        output = result.stdout.strip()
        if not output:
            return []

        worktrees = []
        current: dict[str, str] = {}

        for line in output.split("\n"):
            if line.startswith("worktree "):
                if current:
                    worktrees.append(current)
                current = {"path": line[9:]}
            elif line.startswith("branch "):
                current["branch"] = line[7:]
            elif line == "locked" or line.startswith("locked "):
                current["locked"] = "true"

        if current:
            worktrees.append(current)

        return worktrees
    except (subprocess.TimeoutExpired, OSError):
        return []


def extract_issue_number(branch_name: str) -> int | None:
    """ブランチ名からIssue番号を抽出"""
    # パターン: issue-123, feat/issue-123-xxx, fix/issue-123-yyy
    match = re.search(r"issue-(\d+)", branch_name, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def match_pr_to_worktree(
    prs: list[dict[str, Any]], worktrees: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """PRとworktreeを関連付け"""
    result = []

    for pr in prs:
        pr_branch = pr.get("headRefName", "")
        pr_issue = extract_issue_number(pr_branch)

        # worktreeを探す
        matched_worktree = None
        for wt in worktrees:
            wt_branch = wt.get("branch", "")
            wt_path = wt.get("path", "")

            # Normalize worktree branch (strip refs/heads/ prefix)
            normalized_wt_branch = wt_branch
            if wt_branch.startswith("refs/heads/"):
                normalized_wt_branch = wt_branch[len("refs/heads/") :]

            # ブランチ名が完全一致
            if normalized_wt_branch == pr_branch:
                matched_worktree = wt
                break

            # worktreeパスにIssue番号が含まれる
            if pr_issue:
                wt_issue = extract_issue_number(wt_path)
                if wt_issue == pr_issue:
                    matched_worktree = wt
                    break

        result.append(
            {
                "number": pr.get("number"),
                "title": pr.get("title", ""),
                "branch": pr_branch,
                "author": pr.get("author", {}).get("login", "unknown"),
                "worktree": matched_worktree,
            }
        )

    return result


def get_unmatched_locked_worktrees(
    worktrees: list[dict[str, str]], pr_worktree_map: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """PRに関連付けられていないロックされたworktreeを取得

    Issue #1095: PRに関連しないworktreeも競合リスクとして表示

    Args:
        worktrees: get_worktrees()から取得したworktree情報のリスト。
            各要素は path, branch, locked (optional) キーを含む。
        pr_worktree_map: match_pr_to_worktree()から取得したPR-worktree
            マッピングのリスト。各要素は worktree キーを含む可能性がある。

    Returns:
        ロックされていて、かつPRに関連付けられていないworktreeのリスト。
        メインリポジトリ（/.worktrees/を含まないパス）は除外される。
    """
    # PRにマッチしたworktreeのパスを収集
    matched_paths = set()
    for item in pr_worktree_map:
        wt = item.get("worktree")
        if wt:
            matched_paths.add(wt.get("path", ""))

    # ロックされていて、PRにマッチしていないworktreeを抽出
    unmatched_locked = []
    for wt in worktrees:
        if wt.get("locked") == "true" and wt.get("path") not in matched_paths:
            # メインリポジトリを除外（/.worktrees/を含むパスのみ対象）
            if "/.worktrees/" in wt.get("path", ""):
                unmatched_locked.append(wt)

    return unmatched_locked


def format_warning_message(
    pr_worktree_map: list[dict[str, Any]],
    unmatched_locked_worktrees: list[dict[str, str]] | None = None,
) -> str:
    """警告メッセージをMarkdown形式で組み立てて返す

    セッション開始時に表示する警告メッセージをフォーマットする。
    オープンなPRとそれに紐づくworktreeの一覧、ならびにPRに関連付けられて
    いないロックされたworktreeの一覧を警告として表示する。

    Issue #1095: PRに紐づかないロックされたworktreeも競合リスクとして表示

    Args:
        pr_worktree_map: オープンPRと関連worktree情報のリスト。
            各要素は以下のキーを含む: number, title, branch, author, worktree
        unmatched_locked_worktrees: PRに紐づかないロックworktreeのリスト。
            Noneまたは空リストの場合、このセクションは出力されない。

    Returns:
        表示用の警告メッセージ文字列。何もない場合は空文字列。
    """
    if not pr_worktree_map and not unmatched_locked_worktrees:
        return ""

    lines = []

    # オープンPRのセクション
    if pr_worktree_map:
        lines.extend(
            [
                "⚠️ **オープンPRが存在します** (介入禁止)",
                "",
                "以下のPRは別セッションが担当している可能性があります。",
                "これらのIssue/PRには一切触れないでください。",
                "",
            ]
        )

        for item in pr_worktree_map:
            pr_num = item.get("number", "?")
            title = item.get("title", "")
            branch = item.get("branch", "")
            author = item.get("author", "")
            worktree = item.get("worktree")

            lines.append(f"- **PR #{pr_num}**: {title}")
            lines.append(f"  - ブランチ: `{branch}`")
            lines.append(f"  - 作成者: {author}")

            if worktree:
                wt_path = worktree.get("path", "?")
                locked = worktree.get("locked") == "true"
                lock_status = " 🔒 ロック中" if locked else ""
                lines.append(f"  - worktree: `{wt_path}`{lock_status}")

            lines.append("")

    # ロックされたworktree（PRなし）のセクション
    if unmatched_locked_worktrees:
        # pr_worktree_mapがある場合、各PRの後に空行が追加済み（line 240）なので追加不要
        lines.extend(
            [
                "🔒 **ロックされたworktree** (PRなし)",
                "",
                "以下のworktreeは別セッションが作業中の可能性があります。",
                "",
            ]
        )

        for wt in unmatched_locked_worktrees:
            wt_path = wt.get("path", "?")
            branch = wt.get("branch", "")
            if branch.startswith("refs/heads/"):
                branch = branch[len("refs/heads/") :]
            lines.append(f"- `{wt_path}`")
            if branch:
                lines.append(f"  - ブランチ: `{branch}`")
            lines.append("")

    lines.append("---")
    lines.append("新しいタスクを始める場合は、上記以外のIssueを選んでください。")

    return "\n".join(lines)


def main():
    """SessionStart hookのエントリーポイント"""
    # Set session_id for proper logging
    parse_hook_input()

    result = {"continue": True}

    try:
        prs, pr_error = get_open_prs()
        worktrees = get_worktrees()

        if pr_error:
            # PR取得に失敗した場合は警告を表示
            warning_msg = (
                "⚠️ **オープンPRの確認に失敗しました**\n\n"
                f"エラー: {pr_error}\n\n"
                "オープンPRが存在する可能性があります。\n"
                "新しいタスクを始める前に、手動で確認してください:\n"
                "```\ngh pr list --state open\n```"
            )
            result["message"] = warning_msg
            log_hook_execution(
                "open-pr-warning",
                "approve",
                f"Failed to fetch PRs: {pr_error}",
                {"error": pr_error},
            )
        else:
            pr_worktree_map = match_pr_to_worktree(prs, worktrees) if prs else []
            # Issue #1095: PRに関連しないロックされたworktreeも検出
            unmatched_locked = get_unmatched_locked_worktrees(worktrees, pr_worktree_map)

            message = format_warning_message(pr_worktree_map, unmatched_locked)

            if message:
                result["message"] = message

            log_hook_execution(
                "open-pr-warning",
                "approve",
                f"Found {len(prs)} open PRs, {len(unmatched_locked)} locked worktrees without PR",
                {
                    "open_pr_count": len(prs),
                    "worktree_count": len(worktrees),
                    "matched_count": sum(1 for item in pr_worktree_map if item.get("worktree")),
                    "unmatched_locked_count": len(unmatched_locked),
                },
            )

    except Exception as e:
        # エラーがあっても継続
        log_hook_execution(
            "open-pr-warning",
            "approve",
            f"Error checking open PRs: {e}",
        )

    print(json.dumps(result))


if __name__ == "__main__":
    main()
