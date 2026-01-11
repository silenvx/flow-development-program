#!/usr/bin/env python3
"""Skill呼び出し失敗を検出して調査・Issue化を促す。

Why:
    Skillツールが失敗した場合（ファイル不在等）、手動で回避するだけでは
    根本問題が解決されない。失敗を検出して問題のIssue化を強制する。

What:
    - Skillツール実行後（PostToolUse:Skill）に発火
    - ツール結果からエラーパターンを検出
    - 失敗検出時は警告メッセージを表示し、Issue作成を促す
    - worktree削除後の失敗ケースへのヒントも提供

Remarks:
    - 警告型（ブロックせず、情報提供と行動促進）
    - エラーパターンは _is_skill_failure() で定義
    - 問題を手動回避せず、必ずIssue化することを要求

Changelog:
    - silenvx/dekita#2417: フック追加（Skill失敗時の自動検出）
"""

import json
import re
from typing import Any

from lib.execution import log_hook_execution
from lib.hook_input import get_tool_result
from lib.session import parse_hook_input


def _is_skill_failure(tool_result: dict | str | Any | None) -> tuple[bool, str]:
    """Check if the Skill tool result indicates a failure.

    Returns:
        Tuple of (is_failure, failure_reason).
    """
    if not isinstance(tool_result, dict):
        return False, ""

    # Check for common error patterns in Skill results
    result_text = str(tool_result)

    error_patterns = [
        (r"File does not exist", "ファイルが見つかりません"),
        (r"Directory does not exist", "ディレクトリが見つかりません"),
        (r"tool_use_error", "ツール実行エラー"),
        (r"error.*reading file", "ファイル読み込みエラー"),
        (r"No such file or directory", "ファイル/ディレクトリが存在しません"),
    ]

    for pattern, reason in error_patterns:
        if re.search(pattern, result_text, re.IGNORECASE):
            return True, reason

    return False, ""


def main():
    """Detect Skill failures and alert for investigation.

    Issue #2417: Ensures problems are automatically detected and Issue-ized,
    rather than being silently worked around.
    """
    result = {"continue": True}

    try:
        input_data = parse_hook_input()
        tool_name = input_data.get("tool_name", "")

        if tool_name != "Skill":
            print(json.dumps(result))
            return

        tool_result = get_tool_result(input_data) or {}
        tool_input = input_data.get("tool_input", {})
        skill_name = tool_input.get("skill", "")

        is_failure, failure_reason = _is_skill_failure(tool_result)

        if is_failure:
            log_hook_execution(
                "skill-failure-detector",
                "block",
                f"Skill '{skill_name}' failed: {failure_reason}",
                {"skill": skill_name, "reason": failure_reason},
            )

            message = (
                f"⚠️ **Skill呼び出しが失敗しました**\n\n"
                f"- Skill: `{skill_name}`\n"
                f"- 原因: {failure_reason}\n\n"
                "**必須アクション**:\n"
                "1. 失敗の根本原因を調査してください\n"
                "2. 問題をIssue化してください（手動で回避しないでください）\n"
                "3. Issueを作成してから、代替手段で作業を続行してください\n\n"
                "💡 ヒント: worktree削除後にSkillが失敗する場合は、\n"
                "   オリジナルリポジトリに移動してから再試行してください。"
            )

            result = {
                "decision": "block",
                "continue": True,  # Don't stop, but force investigation
                "reason": message,
                "systemMessage": message,
            }
            print(json.dumps(result))
            return

    except Exception as e:
        log_hook_execution(
            "skill-failure-detector",
            "error",
            f"Hook error: {e}",
        )

    print(json.dumps(result))


if __name__ == "__main__":
    main()
