#!/usr/bin/env python3
"""大きすぎるファイルの読み込み時にリファクタリングを促す警告を表示する。

Why:
    AIがファイルを読み込む際、大きすぎるファイルは認知負荷が高く、
    凝集度・結合度・責務の観点から分割を検討すべき場合がある。

What:
    - PreToolUse (Read) でファイル読み込み時に行数をチェック
    - 閾値超過時に警告メッセージを表示（ブロックはしない）
    - 言語別の閾値: TS/JS 400行、Python 500行、その他 500行

Remarks:
    - 警告のみ（approve with systemMessage）でブロックしない
    - テストファイル、型定義、生成ファイル、設定ファイルは除外
    - AGENTS.md, CLAUDE.md等の意図的に長いドキュメントも除外

Changelog:
    - silenvx/dekita#2625: フック追加
"""

import json
import os
import sys

from lib.constants import (
    FILE_SIZE_THRESHOLD_DEFAULT,
    FILE_SIZE_THRESHOLD_PY,
    FILE_SIZE_THRESHOLD_TS,
)
from lib.execution import log_hook_execution
from lib.session import parse_hook_input

# 除外パターン（種類別）

# 拡張子・接尾辞パターン（endswith）
SUFFIX_PATTERNS = [
    ".test.ts",
    ".test.tsx",
    ".spec.ts",
    ".spec.tsx",
    "_test.py",
    ".d.ts",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".lock",
]

# 接頭辞パターン（startswith）
PREFIX_PATTERNS = [
    "test_",
]

# ディレクトリパターン（contains）
DIRECTORY_PATTERNS = [
    "/generated/",
    "/dist/",
    "/node_modules/",
    "/__pycache__/",
    "/build/",
]

# 完全一致パターン
EXACT_PATTERNS = [
    "AGENTS.md",
    "CLAUDE.md",
    "SKILL.md",
]


def should_exclude(file_path: str) -> bool:
    """除外対象のファイルかどうかを判定する。"""
    if not file_path:
        return True

    # パスを正規化
    normalized = file_path.replace("\\", "/")
    basename = os.path.basename(normalized)

    # 接尾辞パターン（拡張子など）
    for pattern in SUFFIX_PATTERNS:
        if normalized.endswith(pattern):
            return True

    # 接頭辞パターン（test_など）
    for pattern in PREFIX_PATTERNS:
        if basename.startswith(pattern):
            return True

    # ディレクトリパターン（相対パスでも機能するよう、スラッシュなしでマッチ）
    path_parts = normalized.split("/")
    for pattern in DIRECTORY_PATTERNS:
        # パターンからスラッシュを除去してディレクトリ名を取得
        dir_name = pattern.strip("/")
        if dir_name in path_parts:
            return True

    # 完全一致パターン
    for pattern in EXACT_PATTERNS:
        if basename == pattern:
            return True

    return False


def get_threshold(file_path: str) -> int:
    """ファイルの拡張子に基づいて閾値を返す。"""
    if not file_path:
        return FILE_SIZE_THRESHOLD_DEFAULT

    lower_path = file_path.lower()

    # TypeScript/JavaScript
    if any(lower_path.endswith(ext) for ext in [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]):
        return FILE_SIZE_THRESHOLD_TS

    # Python
    if lower_path.endswith(".py"):
        return FILE_SIZE_THRESHOLD_PY

    return FILE_SIZE_THRESHOLD_DEFAULT


def count_lines(file_path: str) -> int | None:
    """ファイルの行数をカウントする。エラー時はNoneを返す。"""
    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            return sum(1 for _ in f)
    except OSError:
        return None


def main():
    """PreToolUse hook for Read tool."""
    result = {"decision": "approve"}

    try:
        data = parse_hook_input()
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})

        # Readツールのみ対象
        if tool_name != "Read":
            print(json.dumps(result))
            return

        file_path = tool_input.get("file_path", "")

        # 除外対象はスキップ
        if should_exclude(file_path):
            log_hook_execution("file-size-warning", "skip", f"Excluded: {file_path}")
            print(json.dumps(result))
            return

        # ファイルが存在しない場合はスキップ
        if not os.path.isfile(file_path):
            print(json.dumps(result))
            return

        # 行数カウント
        line_count = count_lines(file_path)
        if line_count is None:
            print(json.dumps(result))
            return

        # 閾値チェック
        threshold = get_threshold(file_path)
        if line_count > threshold:
            # 相対パス表示用
            try:
                cwd = os.getcwd()
                display_path = os.path.relpath(file_path, cwd)
            except ValueError:
                display_path = file_path

            warning_message = (
                f"このファイルは大きいです（{line_count}行 > {threshold}行閾値）\n\n"
                f"{display_path}\n\n"
                f"リファクタリングを検討:\n"
                f"- 単一責任: このファイルは複数の責務を持っていませんか？\n"
                f"- 凝集度: 関連する機能がまとまっていますか？\n"
                f"- 結合度: 他モジュールへの依存が多すぎませんか？"
            )

            result = {
                "decision": "approve",
                "systemMessage": warning_message,
            }

            log_hook_execution(
                "file-size-warning",
                "warn",
                f"{display_path}: {line_count} lines > {threshold} threshold",
            )
        else:
            log_hook_execution("file-size-warning", "approve", f"{file_path}: {line_count} lines")

    except Exception as e:
        print(f"[file-size-warning] Hook error: {e}", file=sys.stderr)
        result = {"decision": "approve"}

    print(json.dumps(result))


if __name__ == "__main__":
    main()
