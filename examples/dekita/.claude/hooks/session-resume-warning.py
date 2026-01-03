#!/usr/bin/env python3
"""セッション再開時に競合状況警告を表示。

Why:
    --resume/--continue/--fork-sessionでセッションを再開すると、
    元セッションと重複作業してしまうリスクがある。既存worktreeや
    オープンPRの一覧を表示して、競合を早期に認識させる。

What:
    - セッション開始時（SessionStart）に発火
    - sourceがresume/compactの場合のみ処理
    - 既存worktree一覧を取得
    - オープンPR一覧を取得
    - 競合リスクの警告メッセージを表示

Remarks:
    - 非ブロック型（情報表示のみ）
    - session-handoff-readerは引き継ぎ情報、本フックは競合警告
    - fork-session判定はClaudeがコンテキスト内で実施

Changelog:
    - silenvx/dekita#1979: フック追加（再開時の重複作業防止）
    - silenvx/dekita#2239: worktree/PR一覧の自動表示
    - silenvx/dekita#2363: fork-session判定をClaude側に移行
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))
from lib.execution import log_hook_execution
from lib.session import parse_hook_input

HOOK_NAME = "session-resume-warning"


def get_worktree_list() -> list[str]:
    """Get list of existing worktrees (excluding main).

    Returns worktrees in .worktrees directory, including detached HEAD state.
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if result.returncode != 0:
            return []

        output = result.stdout.strip()
        if not output:
            return []

        worktrees = []
        current_worktree = None
        current_branch = None

        for line in output.split("\n"):
            if line.startswith("worktree "):
                # Save previous worktree if it was in .worktrees/
                if current_worktree and ".worktrees/" in current_worktree:
                    worktree_name = Path(current_worktree).name
                    branch_info = current_branch if current_branch else "HEAD detached"
                    worktrees.append(f"  - {worktree_name} ({branch_info})")
                # Start tracking new worktree
                current_worktree = line[9:]
                current_branch = None
            elif line.startswith("branch refs/heads/"):
                # refs/heads/ プレフィックスを除去してブランチ名のみを取得
                current_branch = line[18:]

        # Handle last worktree
        if current_worktree and ".worktrees/" in current_worktree:
            worktree_name = Path(current_worktree).name
            branch_info = current_branch if current_branch else "HEAD detached"
            worktrees.append(f"  - {worktree_name} ({branch_info})")

        return worktrees
    except Exception:
        return []


def get_open_prs() -> list[str]:
    """Get list of open PRs."""
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--json",
                "number,headRefName,title",
                "--jq",
                '.[] | "  - #\\(.number) \\(.headRefName): \\(.title)"',
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode != 0:
            return []

        return [line for line in result.stdout.strip().split("\n") if line]
    except Exception:
        return []


def format_resume_session_message(worktrees: list[str], open_prs: list[str]) -> str:
    """Format the session resume warning message with context.

    Args:
        worktrees: List of existing worktrees.
        open_prs: List of open PRs.
    """
    # Issue #2363: fork-session判定はClaudeがコンテキスト内で行う
    # （SessionStartとUserPromptSubmitのsession_idを比較）
    message_parts = [
        "🔄 **セッション再開検出**\n",
        "このセッションは以前の会話から再開されました。",
        "**作業開始前に競合状況を確認してください**:\n",
    ]

    # Add worktree information
    if worktrees:
        message_parts.append("**既存Worktree** (別セッションが作業中の可能性):")
        message_parts.extend(worktrees)
        message_parts.append("")
    else:
        message_parts.append("**既存Worktree**: なし")
        message_parts.append("")

    # Add open PR information
    if open_prs:
        message_parts.append("**オープンPR** (介入禁止):")
        message_parts.extend(open_prs)
        message_parts.append("")
    else:
        message_parts.append("**オープンPR**: なし")
        message_parts.append("")

    # Add reminder
    message_parts.extend(
        [
            "⚠️ **AGENTS.md原則**:",
            "- Issue作業開始前に既存worktree/PRを確認",
            "- オープンPRがあるIssueには介入禁止",
            "- 競合リスクがある場合はユーザーに確認",
        ]
    )

    return "\n".join(message_parts)


def main():
    """SessionStart hookのエントリーポイント"""
    result = {"continue": True}

    try:
        hook_input = parse_hook_input()
        source = hook_input.get("source", "")

        # source が "resume" または "compact" の場合に警告を表示
        # - resume: --resume, --continue, --fork-session のいずれかで起動
        # - compact: コンテキスト圧縮からの再開
        # Issue #2363: fork-session判定はClaudeがコンテキスト内で行う
        if source in ("resume", "compact"):
            # Get context information
            worktrees = get_worktree_list()
            open_prs = get_open_prs()

            result["message"] = format_resume_session_message(worktrees, open_prs)

            log_hook_execution(
                HOOK_NAME,
                "approve",
                f"resume warning displayed (worktrees={len(worktrees)}, prs={len(open_prs)})",
                details={
                    "source": source,
                    "worktree_count": len(worktrees),
                    "open_pr_count": len(open_prs),
                },
            )
        else:
            log_hook_execution(
                HOOK_NAME,
                "approve",
                f"Not a resume session (source={source})",
            )

    except Exception as e:
        log_hook_execution(
            HOOK_NAME,
            "approve",
            f"Error: {e}",
        )

    print(json.dumps(result))


if __name__ == "__main__":
    main()
