"""トランスクリプトファイルからの情報抽出を行う共通関数を提供する。

Why:
    トランスクリプト処理ロジックを複数フックで重複実装しないため。

What:
    - is_in_code_block(): コードブロック内判定
    - extract_assistant_responses(): assistant応答テキスト抽出
    - load_transcript(): JSON/JSONL両形式のトランスクリプト読み込み

Remarks:
    - JSONL形式（1行1JSON）とJSON配列形式の両方に対応
    - 正規表現フォールバックでパース失敗時も可能な限り抽出
    - 空文字列のcontentは意図的に除外

Changelog:
    - silenvx/dekita#1915: askuser-suggestionとdefer-keyword-checkから共通化
    - silenvx/dekita#2254: JSONL形式サポート追加
    - silenvx/dekita#2261: 4つのStop hookからload_transcriptを共通化
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lib.path_validation import is_safe_transcript_path


def is_in_code_block(text: str, match_pos: int) -> bool:
    """マッチ位置がコードブロック内かチェック.

    Args:
        text: 検索対象のテキスト
        match_pos: マッチ位置（文字列のインデックス）

    Returns:
        コードブロック内であれば True

    Example:
        >>> text = "Hello ```code``` world"
        >>> is_in_code_block(text, 10)  # "code" の位置
        True
        >>> is_in_code_block(text, 20)  # "world" の位置
        False
    """
    code_block_starts = [m.start() for m in re.finditer(r"```", text[:match_pos])]
    return len(code_block_starts) % 2 == 1


def extract_assistant_responses(content: str) -> list[str]:
    """トランスクリプトからassistantの応答を抽出.

    以下の形式に対応:
    - JSONL形式（1行1JSON）
    - JSON配列形式
    - 正規表現によるフォールバック（上記が失敗した場合）

    Args:
        content: トランスクリプトファイルの内容

    Returns:
        assistantの応答テキストのリスト

    Example:
        >>> content = '{"role": "assistant", "content": "Hello"}\\n'
        >>> extract_assistant_responses(content)
        ['Hello']

    Note:
        空文字列のcontentは意図的に除外される（Issue #1933）。

        設計根拠:
            1. 呼び出し元（defer-keyword-check、askuser-suggestion等）は
               応答テキスト内のキーワードを検索する。空文字列では
               検索対象がなく、処理しても意味がない。
            2. 空文字列を返すと呼び出し側で空チェックが必要になり、
               コードが複雑化する。
            3. JSONL処理、JSON配列処理、正規表現フォールバック全てで
               同じ動作（空を除外）を保証することで一貫性を維持。
    """
    responses: list[str] = []

    # JSONLフォーマット（1行1JSON）の場合
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            # objがdictの場合のみ処理（配列の場合はスキップ）
            if isinstance(obj, dict) and obj.get("role") == "assistant" and obj.get("content"):
                responses.append(obj["content"])
        except json.JSONDecodeError:
            continue

    # JSON配列フォーマットの場合
    if not responses:
        try:
            data = json.loads(content)
            if isinstance(data, list):
                for item in data:
                    if item.get("role") == "assistant" and item.get("content"):
                        responses.append(item["content"])
        except json.JSONDecodeError:
            pass  # JSONLでもJSON配列でもない場合は正規表現にフォールバック

    # フォールバック: 正規表現（エスケープ対応）
    if not responses:
        # JSONエスケープされた文字列を考慮
        pattern = r'"role"\s*:\s*"assistant"[^}]*"content"\s*:\s*"((?:[^"\\]|\\.)*)\"'
        for match in re.finditer(pattern, content, re.DOTALL):
            try:
                # JSONエスケープをデコード
                decoded = json.loads(f'"{match.group(1)}"')
                if decoded:  # 空文字列を除外（JSONL処理と一貫）
                    responses.append(decoded)
            except json.JSONDecodeError:
                if match.group(1):  # 空文字列を除外
                    responses.append(match.group(1))

    return responses


def load_transcript(transcript_path: str) -> list[dict[str, Any]] | None:
    """Load and parse the transcript file.

    Supports both JSON (.json) and JSON Lines (.jsonl) formats.

    Args:
        transcript_path: Path to the transcript file.

    Returns:
        Parsed transcript as list of message dicts, or None on error.

    Note:
        Issue #2254: Added JSONL format support.
        Issue #2261: Extracted to common utility from 4 Stop hooks.
    """
    if not is_safe_transcript_path(transcript_path):
        return None

    try:
        path = Path(transcript_path)
        if not path.exists():
            return None
        content = path.read_text(encoding="utf-8")

        # Issue #2254: Support JSONL format (one JSON object per line)
        if transcript_path.endswith(".jsonl"):
            lines = content.strip().split("\n")
            return [json.loads(line) for line in lines if line.strip()]

        # Standard JSON format
        return json.loads(content)
    except (json.JSONDecodeError, OSError):
        return None
