#!/usr/bin/env python3
"""選択肢をテキストで列挙するパターンを検出し、AskUserQuestionツールの使用を提案する。

Why:
    テキストでの選択肢列挙はユーザーの入力負担が大きく、選択ミスのリスクがある。
    AskUserQuestionツールを使うことでUXが向上する。

What:
    - トランスクリプトから選択肢パターン（A案/B案、1./2.等）を検出
    - AskUserQuestion使用回数と比較
    - 過少な場合に警告を表示（ブロックしない）

Remarks:
    - 警告型フック（ブロックしない、systemMessageで通知）
    - コードブロック内のパターンは除外
    - セッション終了時（Stop）に発火

Changelog:
    - silenvx/dekita#1910: フック追加
    - silenvx/dekita#1916: パフォーマンス改善（パターンのプリコンパイル）
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib.execution import log_hook_execution
from lib.path_validation import is_safe_transcript_path
from lib.session import parse_hook_input
from lib.transcript import extract_assistant_responses, is_in_code_block

HOOK_NAME = "askuser-suggestion"

# 選択肢を列挙するパターン（プリコンパイル済み）
# Issue #1916: パフォーマンス改善
CHOICE_PATTERNS = [
    # A案、B案パターン
    re.compile(r"[A-Z]案.*?[A-Z]案"),
    re.compile(r"[１２３４５].*?[１２３４５]"),
    re.compile(r"[1-5]\s*[\.）\)].*?[1-5]\s*[\.）\)]"),
    # 質問パターン
    re.compile(r"どちらにしますか"),
    re.compile(r"どれを選びますか"),
    re.compile(r"どれにしますか"),
    re.compile(r"以下から選んでください"),
    re.compile(r"どの.*?にしますか"),
    re.compile(r"どちらが.*?ですか"),
    re.compile(r"どちらを.*?しますか"),
    # リスト形式
    re.compile(r"選択肢[：:]\s*\n"),
    re.compile(r"オプション[：:]\s*\n"),
]

# 除外パターン（結合してプリコンパイル）
# Issue #1916: パフォーマンス改善
EXCLUDE_PATTERN = re.compile(
    r"```"  # コードブロック内
    r"|例[：:]"  # 例示
    r"|例えば"
    r"|ドキュメント"
    r"|AGENTS\.md"
    r"|スキル"
)


def check_askuser_usage(transcript_path: str) -> dict:
    """トランスクリプトを分析してAskUserQuestion使用状況を確認"""
    result = {
        "violations": [],
        "askuser_count": 0,
        "choice_text_count": 0,
    }

    try:
        with open(transcript_path, encoding="utf-8") as f:
            content = f.read()
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return result

    # AskUserQuestion使用回数をカウント
    result["askuser_count"] = len(re.findall(r"AskUserQuestion", content))

    # Claudeの応答部分を抽出
    claude_responses = extract_assistant_responses(content)

    for response in claude_responses:
        # 除外コンテキストをチェック（結合パターンで一度にマッチ）
        if EXCLUDE_PATTERN.search(response):
            continue

        # 選択肢パターンを検出（プリコンパイル済みパターン使用）
        for pattern in CHOICE_PATTERNS:
            matches = list(pattern.finditer(response))
            for match in matches:
                # コードブロック内は除外
                if is_in_code_block(response, match.start()):
                    continue

                result["choice_text_count"] += 1
                violation = {
                    "pattern": pattern,
                    "matched_text": match.group()[:50],
                }
                result["violations"].append(violation)

    return result


def main() -> None:
    """フックのエントリポイント."""
    hook_input = parse_hook_input()

    # Stop hookはtranscript_pathをトップレベルで受け取る
    transcript_path = hook_input.get("transcript_path", "")

    if not transcript_path:
        # トランスクリプトパスがない場合はスキップ
        log_hook_execution(HOOK_NAME, "approve", "No transcript path")
        print(json.dumps({"continue": True}))
        return

    # セキュリティ: パストラバーサル攻撃を防止 (Issue #1914)
    if not is_safe_transcript_path(transcript_path):
        log_hook_execution(HOOK_NAME, "approve", f"Invalid transcript path: {transcript_path}")
        print(json.dumps({"continue": True}))
        return

    result = check_askuser_usage(transcript_path)

    # 違反がある場合は警告
    if result["violations"] and result["choice_text_count"] > result["askuser_count"]:
        warning_msg = (
            f"⚠️ 選択肢をテキストで{result['choice_text_count']}回列挙しましたが、"
            f"AskUserQuestionは{result['askuser_count']}回しか使用していません。\n"
            "複数の選択肢がある場合はAskUserQuestionツールの使用を推奨します。"
        )
        log_hook_execution(
            HOOK_NAME,
            "warn",
            f"Choice text: {result['choice_text_count']}, AskUser: {result['askuser_count']}",
        )
        # 警告として表示（ブロックはしない）
        print(
            json.dumps(
                {
                    "continue": True,
                    "message": warning_msg,
                }
            )
        )
    else:
        log_hook_execution(HOOK_NAME, "approve", "No violations")
        print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
