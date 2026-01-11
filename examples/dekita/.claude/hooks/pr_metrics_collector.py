#!/usr/bin/env python3
"""
PRメトリクス自動収集フック（PostToolUse）

PRがマージされたときに自動でメトリクスを収集・記録する。

トリガー条件:
- `gh pr merge` コマンドが成功した場合

記録内容:
- PRのサイクルタイム
- レビュー数（AI/人間）
- CI結果
- コード変更量
"""

# SRP: PRマージ時のメトリクス収集のみを担当（単一責任）
# 既存フックとの重複なし（新規機能）
# ブロックなし（情報収集のみのため）

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# 共通モジュール
HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))
from lib.constants import TIMEOUT_EXTENDED, TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.github import extract_pr_number
from lib.hook_input import get_exit_code, get_tool_result
from lib.session import parse_hook_input

SCRIPT_DIR = HOOKS_DIR.parent / "scripts"


def get_current_branch_pr() -> int | None:
    """現在のブランチに関連するPR番号を取得"""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", "--json", "number"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("number")
    except Exception:
        pass  # PR番号取得失敗時はNoneを返す
    return None


def collect_pr_metrics(pr_number: int) -> bool:
    """PRメトリクスを収集"""
    collect_script = SCRIPT_DIR / "collect_pr_metrics.py"
    if not collect_script.exists():
        return False

    try:
        result = subprocess.run(
            ["python3", str(collect_script), str(pr_number)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_EXTENDED,
        )
        return result.returncode == 0
    except Exception:
        return False


def main():
    """Collect PR metrics after merge operations."""
    hook_input = parse_hook_input()
    if not hook_input:
        print(json.dumps({"continue": True}))
        return

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})
    tool_result = get_tool_result(hook_input) or {}

    # Bashツール以外はスキップ
    if tool_name != "Bash":
        print(json.dumps({"continue": True}))
        return

    command = tool_input.get("command", "")

    # gh pr merge コマンドかチェック
    if "gh pr merge" not in command:
        print(json.dumps({"continue": True}))
        return

    # コマンドが成功したかチェック
    # Issue #2203: Use get_exit_code() for consistent default value
    exit_code = get_exit_code(tool_result)

    # マージ成功の判定
    if exit_code != 0:
        log_hook_execution(
            "pr_metrics_collector",
            "approve",
            "Merge command failed, skipping metrics collection",
        )
        print(json.dumps({"continue": True}))
        return

    # PR番号を取得
    pr_number_str = extract_pr_number(command)
    pr_number: int | None = int(pr_number_str) if pr_number_str else None
    if pr_number is None:
        # 現在のブランチから取得を試みる
        pr_number = get_current_branch_pr()

    if pr_number is None:
        log_hook_execution(
            "pr_metrics_collector",
            "approve",
            "Could not determine PR number",
        )
        print(json.dumps({"continue": True}))
        return

    # メトリクス収集
    success = collect_pr_metrics(pr_number)

    log_hook_execution(
        "pr_metrics_collector",
        "approve",
        f"PR #{pr_number} metrics {'collected' if success else 'collection failed'}",
        {"pr_number": pr_number, "success": success},
    )

    # メトリクス収集の成否に関わらずapprove
    message = f"PR #{pr_number} メトリクスを記録しました" if success else ""
    result = {"continue": True}
    if message:
        result["systemMessage"] = message

    print(json.dumps(result))


if __name__ == "__main__":
    main()
