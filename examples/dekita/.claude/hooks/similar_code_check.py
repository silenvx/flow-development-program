#!/usr/bin/env python3
"""新規フック作成時に類似コードを検索して参考情報を提供。

Why:
    フック実装時に既存のパターンを知らずに独自実装すると、一貫性が失われ
    レビューで指摘される。類似コードを事前に提示することで品質を向上させる。

What:
    - フックファイル（.claude/hooks/*.py）へのWrite/Edit時に発火
    - 新しい関数定義（def xxx）を抽出
    - 既存フックから類似パターン（has_skip_, check_, get_等）を検索
    - 見つかった場合はsystemMessageで参照ファイルを提示

Remarks:
    - 非ブロック型（情報提供のみ）
    - existing-impl-checkはworktree作成時、本フックはWrite/Edit時
    - 検索パターンは SEARCH_PATTERNS で定義

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import json
import re
import subprocess
import sys

from lib.constants import TIMEOUT_LIGHT
from lib.execution import log_hook_execution
from lib.repo import get_repo_root
from lib.session import parse_hook_input

HOOK_NAME = "similar-code-check"

# Patterns to search for similar implementations
SEARCH_PATTERNS = {
    "has_skip_": "スキップ判定関数（環境変数チェック等）",
    "is_.*_command": "コマンド判定関数",
    "check_": "検証/チェック関数",
    "get_": "データ取得関数",
    "extract_": "データ抽出関数",
    "format_": "フォーマット関数",
    "parse_": "パース関数",
}


def is_hook_file(file_path: str) -> bool:
    """Check if the file is a hook Python file."""
    if not file_path:
        return False
    # Match .claude/hooks/*.py but not tests
    return (
        ".claude/hooks/" in file_path and file_path.endswith(".py") and "/tests/" not in file_path
    )


def extract_function_names(content: str) -> list[str]:
    """Extract function definitions from Python content."""
    if not content:
        return []

    # Match "def function_name(" pattern
    pattern = r"^def\s+([a-z_][a-z0-9_]*)\s*\("
    matches = re.findall(pattern, content, re.MULTILINE)
    return matches


def search_similar_functions(function_names: list[str]) -> dict[str, list[str]]:
    """Search for similar function patterns in existing hooks.

    Returns dict mapping pattern description to list of matching files.
    """
    results: dict[str, list[str]] = {}

    # Get repo root once before the loop
    repo_root = get_repo_root()
    if not repo_root:
        return results  # Early return if repo root unavailable

    for func_name in function_names:
        for pattern, description in SEARCH_PATTERNS.items():
            if re.match(pattern, func_name):
                # Search for existing functions with this pattern
                # Use -E for extended regex to properly match patterns
                try:
                    grep_result = subprocess.run(
                        [
                            "git",
                            "grep",
                            "-E",
                            "-l",
                            f"def {pattern}",
                            "--",
                            ".claude/hooks/*.py",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=TIMEOUT_LIGHT,
                        cwd=repo_root,
                    )
                    if grep_result.returncode == 0 and grep_result.stdout.strip():
                        files = grep_result.stdout.strip().split("\n")
                        # Limit to 5 files per pattern
                        key = f"`{func_name}` ({description})"
                        if key not in results:
                            results[key] = []
                        for f in files[:5]:
                            if f and f not in results[key]:
                                results[key].append(f)
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    pass  # Fail-open: 検索失敗時は継続

    return results


def format_suggestions(similar: dict[str, list[str]]) -> str:
    """Format search results as a systemMessage."""
    if not similar:
        return ""

    lines = ["💡 **類似コードが見つかりました** - 一貫性のため参考にしてください:\n"]

    for pattern_desc, files in similar.items():
        lines.append(f"\n**{pattern_desc}**:")
        for f in files:
            lines.append(f"  - `{f}`")

    lines.append(
        "\n\n既存実装を参考にすることで、"
        "レビュー指摘を事前に防ぎ、一貫性のあるコードベースを維持できます。"
    )

    return "\n".join(lines)


def main():
    """PreToolUse hook for Write/Edit commands.

    Detects new hook file creation and suggests similar existing code.
    """
    result = {"decision": "approve"}

    try:
        input_data = parse_hook_input()
        tool_input = input_data.get("tool_input", {})
        file_path = tool_input.get("file_path", "")
        # Handle both Write (content) and Edit (new_string) tool inputs
        content = tool_input.get("content", "") or tool_input.get("new_string", "")

        # Only process hook files
        if is_hook_file(file_path):
            # Extract function names from new content
            func_names = extract_function_names(content)

            if func_names:
                # Search for similar patterns
                similar = search_similar_functions(func_names)

                if similar:
                    result["systemMessage"] = format_suggestions(similar)

    except Exception as e:
        # Don't block on errors (fail-open)
        print(f"[{HOOK_NAME}] Error: {e}", file=sys.stderr)

    log_hook_execution(HOOK_NAME, result.get("decision", "approve"), result.get("reason"))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
