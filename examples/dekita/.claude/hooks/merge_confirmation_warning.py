#!/usr/bin/env python3
"""「マージしますか？」パターンを検出し、原則違反を警告する。

Why:
    AGENTS.mdの「マージまで完遂」原則では、マージ可能になったら
    確認なしに即座にマージすべき。確認パターンは原則違反である。

What:
    - Claudeの応答から「マージしますか？」等のパターンを検出
    - 検出したらセッション終了時に警告を表示
    - ルール説明やコードブロック内は誤検知防止で除外

Remarks:
    - 警告型フック（ブロックしない、事後検出で警告）
    - Stopで発火（transcript分析）
    - AGENTS.md参照や❌例示は誤検知防止で除外
    - 次回からの改善を促すフィードバック

Changelog:
    - silenvx/dekita#2284: フック追加
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

HOOK_NAME = "merge-confirmation-warning"

# マージ確認パターン（単一の正規表現に結合）
MERGE_CONFIRMATION_PATTERN = re.compile(
    r"(?:"
    # 直接的な確認パターン
    r"マージしますか[\?？]"
    r"|マージしてよい(?:です)?か[\?？]"
    r"|マージしてもよろしい(?:です)?か[\?？]"
    r"|マージを実行しますか[\?？]"
    r"|マージしても(?:いい|良い)(?:です)?か[\?？]"
    # PR関連の確認パターン
    r"|PR(?:を)?マージしますか[\?？]"
    r"|プルリクエスト(?:を)?マージしますか[\?？]"
    # 完了報告後の待機パターン
    r"|次は何をしますか[\?？].*?(?:マージ|merge)"
    r")",
    re.IGNORECASE,
)

# 除外パターン（ルール説明や例示を除外）
# コードブロックはis_in_code_blockで別途チェック
EXCLUDE_PATTERN = re.compile(
    r"AGENTS\.md"  # ドキュメント参照
    r"|禁止.*パターン"  # ルール説明
    r"|❌"  # 悪い例
    r"|正しい対応"  # ルール説明
    r"|例:"  # 例示
)

# 除外パターンのコンテキスト範囲（マッチ前後の文字数）
EXCLUDE_CONTEXT_RANGE = 30


def check_merge_confirmation(transcript_path: str) -> dict:
    """トランスクリプトを分析してマージ確認パターンを検出."""
    result = {
        "violations": [],
        "confirmation_count": 0,
    }

    try:
        with open(transcript_path, encoding="utf-8") as f:
            content = f.read()
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return result

    # Claudeの応答部分を抽出
    claude_responses = extract_assistant_responses(content)

    for response in claude_responses:
        # マージ確認パターンを検出
        for match in MERGE_CONFIRMATION_PATTERN.finditer(response):
            # コードブロック内は除外
            if is_in_code_block(response, match.start()):
                continue

            # マッチ周辺のコンテキストのみで除外パターンをチェック
            context_start = max(0, match.start() - EXCLUDE_CONTEXT_RANGE)
            context_end = min(len(response), match.end() + EXCLUDE_CONTEXT_RANGE)
            match_context = response[context_start:context_end]

            if EXCLUDE_PATTERN.search(match_context):
                continue

            result["confirmation_count"] += 1
            # 報告用のコンテキスト（前後50文字）
            report_start = max(0, match.start() - 50)
            report_end = min(len(response), match.end() + 50)
            result["violations"].append(
                {
                    "pattern": match.group(),
                    "context": response[report_start:report_end],
                }
            )

    return result


def main() -> None:
    """フックのエントリポイント."""
    hook_input = parse_hook_input()

    # Stop hookはtranscript_pathをトップレベルで受け取る
    transcript_path = hook_input.get("transcript_path", "")

    if not transcript_path:
        log_hook_execution(HOOK_NAME, "approve", "No transcript path")
        print(json.dumps({"continue": True}))
        return

    # セキュリティ: パストラバーサル攻撃を防止
    if not is_safe_transcript_path(transcript_path):
        log_hook_execution(HOOK_NAME, "approve", f"Invalid transcript path: {transcript_path}")
        print(json.dumps({"continue": True}))
        return

    result = check_merge_confirmation(transcript_path)

    # 違反がある場合は警告
    if result["violations"]:
        examples = result["violations"][:3]
        example_text = "\n".join(f"  - 「{v['pattern']}」" for v in examples)
        warning_msg = (
            f"⚠️ 「マージ確認」パターンが{len(result['violations'])}回検出されました:\n"
            f"{example_text}\n\n"
            "AGENTS.mdの「マージまで完遂」原則:\n"
            "  ❌ 「マージしますか？」と確認を求める\n"
            "  ✅ マージ可能になったら即座に `gh pr merge` を実行\n\n"
            "次回から、CIパス・レビュー完了後はユーザー確認なしにマージを実行してください。"
        )
        log_hook_execution(
            HOOK_NAME,
            "warn",
            f"Merge confirmation patterns detected: {len(result['violations'])}",
        )
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
