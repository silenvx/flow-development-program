#!/usr/bin/env python3
"""Dependabot PR操作時にdevelopment-workflowスキルの参照を促す。

Why:
    Dependabot PRのマージはリスク順序・E2Eテストなど特別な手順が必要。
    スキルを参照せずに操作すると、本番障害につながる可能性がある。

What:
    - gh pr merge/rebase/checkout等のDependabot PR操作を検出
    - development-workflowスキルの参照を促す警告を表示
    - ブロックはせず、情報提供のみ

Remarks:
    - 警告型フック（ブロックしない、stderrで警告）
    - PreToolUse:Bashで発火（gh prコマンド）
    - gh api呼び出しでDependabot PRを判定
    - parse_gh_pr_commandでグローバルフラグ付きコマンドも検出

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import json
import subprocess
import sys

from lib.execution import log_hook_execution
from lib.github import parse_gh_pr_command
from lib.results import make_approve_result
from lib.session import parse_hook_input

# Dependabot PR操作として検出するサブコマンド
# マージ、リベース、チェックアウトなど
DEPENDABOT_OP_SUBCOMMANDS = {"merge", "rebase", "checkout"}


def is_dependabot_pr(pr_number: str) -> bool:
    """PRがDependabotによるものか判定

    Args:
        pr_number: PR番号

    Returns:
        Dependabot PRの場合True
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "view", pr_number, "--json", "author,headRefName"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False

        data = json.loads(result.stdout)
        author = data.get("author", {}).get("login", "")
        branch = data.get("headRefName", "")

        return author == "dependabot[bot]" or branch.startswith("dependabot/")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return False


def is_dependabot_operation(command: str) -> tuple[bool, str | None]:
    """コマンドがDependabot PR操作かどうかを判定

    parse_gh_pr_commandを使用してグローバルフラグ付きコマンドも検出。
    例: gh -R owner/repo pr merge 123

    Args:
        command: 実行するコマンド

    Returns:
        Tuple of (is_dependabot_op, pr_number)
    """
    subcommand, pr_number = parse_gh_pr_command(command)
    if subcommand and subcommand in DEPENDABOT_OP_SUBCOMMANDS:
        return True, pr_number
    return False, None


def main():
    """フックのエントリポイント"""
    try:
        input_json = parse_hook_input()
        tool_input = input_json.get("tool_input") or {}
        command = tool_input.get("command") or ""

        # Dependabot PR操作でなければapprove
        is_dep_op, pr_number = is_dependabot_operation(command)
        if not is_dep_op:
            result = make_approve_result("dependabot-skill-reminder")
            log_hook_execution("dependabot-skill-reminder", "approve")
            print(json.dumps(result))
            return

        # PR番号がなければapprove
        if not pr_number:
            result = make_approve_result("dependabot-skill-reminder")
            log_hook_execution("dependabot-skill-reminder", "approve", "no PR number")
            print(json.dumps(result))
            return

        # Dependabot PRかどうか確認
        if not is_dependabot_pr(pr_number):
            result = make_approve_result("dependabot-skill-reminder")
            log_hook_execution("dependabot-skill-reminder", "approve", "not a Dependabot PR")
            print(json.dumps(result))
            return

        # Dependabot PRへの操作 -> 警告を出してapprove
        message = f"""[dependabot-skill-reminder] Dependabot PR #{pr_number} への操作を検出しました。

development-workflow スキルを参照してください:

Skill development-workflow

スキルには以下の手順が含まれています:
  - BEHIND検知時の対応手順
  - 複数PR処理のベストプラクティス
  - ci-monitor.py の活用方法

効率的なDependabot PR処理には、スキルの手順に従うことを推奨します。
"""

        # 警告を出力（ブロックはしない）
        print(message, file=sys.stderr)

        result = make_approve_result("dependabot-skill-reminder")
        log_hook_execution(
            "dependabot-skill-reminder",
            "approve",
            f"Dependabot PR warning shown for PR #{pr_number}",
        )
        print(json.dumps(result))

    except Exception as e:
        # エラー時はapprove
        print(f"[dependabot-skill-reminder] Hook error: {e}", file=sys.stderr)
        result = make_approve_result("dependabot-skill-reminder", f"Hook error: {e}")
        log_hook_execution("dependabot-skill-reminder", "approve", f"Hook error: {e}")
        print(json.dumps(result))


if __name__ == "__main__":
    main()
