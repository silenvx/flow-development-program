#!/usr/bin/env python3
"""Edit時に「〜と同じ」参照スタイルのコメントを検出して警告。

Why:
    「〜と同じ」「copied from 〜」などの参照コメントは、
    参照先が変更されると嘘になりやすい。コードの整合性が崩れる。

What:
    - Editツール実行を検出
    - new_stringから参照スタイルのコメントパターンを検索
    - 検出時はsystemMessageで警告（importや理由説明への変更を推奨）

Remarks:
    - 非ブロック型（警告のみ）
    - PreToolUse:Edit フック
    - .py, .ts, .tsx, .js, .jsx ファイルが対象

Changelog:
    - silenvx/dekita#2007: フック追加（コード-コメント整合性チェック）
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))
from lib.execution import log_hook_execution
from lib.session import parse_hook_input

HOOK_NAME = "reference-comment-check"

# Patterns that indicate reference-style comments (problematic)
# These patterns are checked against the new_string content
REFERENCE_PATTERNS = [
    # Japanese patterns
    r"[#/]\s*[^\n]*と同じ",  # "〜と同じ" (same as ~)
    r"[#/]\s*[^\n]*と共通",  # "〜と共通" (shared with ~)
    r"[#/]\s*[^\n]*を参照",  # "〜を参照" (refer to ~)
    r"[#/]\s*[^\n]*から(?:コピー|流用)",  # "〜からコピー/流用"
    # English patterns
    r"[#/]\s*[^\n]*(?i:same\s+as)\s+\S+\.(?:py|ts|js|tsx|jsx)",  # "same as file.py"
    r"[#/]\s*[^\n]*(?i:copied\s+from)\s+\S+\.(?:py|ts|js|tsx|jsx)",  # "copied from file.py"
    r"[#/]\s*[^\n]*(?i:see|refer\s+to)\s+\S+\.(?:py|ts|js|tsx|jsx)",  # "see file.py"
]

# Compiled patterns for efficiency
COMPILED_PATTERNS = [re.compile(p) for p in REFERENCE_PATTERNS]

# File extensions to check
CHECKABLE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx"}


def should_check_file(file_path: str) -> bool:
    """Check if the file should be checked for reference comments.

    Args:
        file_path: Path to the file being edited

    Returns:
        True if the file should be checked
    """
    path = Path(file_path)
    return path.suffix in CHECKABLE_EXTENSIONS


def find_reference_comments(text: str) -> list[str]:
    """Find reference-style comments in the given text.

    Args:
        text: The text to check (typically new_string from Edit tool)

    Returns:
        List of matched reference comments
    """
    matches = []
    for pattern in COMPILED_PATTERNS:
        for match in pattern.finditer(text):
            # Extract the full comment line
            start = text.rfind("\n", 0, match.start()) + 1
            end = text.find("\n", match.end())
            if end == -1:
                end = len(text)
            comment_line = text[start:end].strip()
            if comment_line and comment_line not in matches:
                matches.append(comment_line)
    return matches


def format_warning_message(matches: list[str], file_path: str) -> str:
    """Format the warning message for detected reference comments.

    Args:
        matches: List of matched reference comments
        file_path: Path to the file being edited

    Returns:
        Formatted warning message
    """
    lines = [
        "⚠️ **参照スタイルのコメントを検出しました**",
        "",
        f"ファイル: `{file_path}`",
        "",
        "検出されたコメント:",
    ]

    for match in matches[:5]:  # Limit to 5 matches
        lines.append(f"  - `{match}`")

    lines.extend(
        [
            "",
            "**問題点**: 参照先のコードが変更されると、コメントが嘘になります。",
            "",
            "**推奨される対応**:",
            "1. 同じ値を使うなら `import` で共有する",
            "2. 「なぜこの値か」を説明するコメントに変更する",
            "",
            "例: `# タイムアウトは10秒（ネットワーク遅延を考慮）`",
        ]
    )

    return "\n".join(lines)


def main():
    """PreToolUse:Edit hook for reference comment detection."""
    result = {"decision": "approve"}

    try:
        input_data = parse_hook_input()
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        # Only check Edit operations
        if tool_name != "Edit":
            print(json.dumps(result))
            return

        file_path = tool_input.get("file_path", "")
        new_string = tool_input.get("new_string", "")

        # Skip if not a checkable file
        if not should_check_file(file_path):
            log_hook_execution(
                HOOK_NAME,
                "approve",
                f"skipped: not a checkable file ({file_path})",
            )
            print(json.dumps(result))
            return

        # Skip if no new content
        if not new_string:
            print(json.dumps(result))
            return

        # Find reference comments
        matches = find_reference_comments(new_string)

        if matches:
            result["systemMessage"] = format_warning_message(matches, file_path)
            log_hook_execution(
                HOOK_NAME,
                "approve",
                f"warning: found {len(matches)} reference comments",
                {"file": file_path, "matches": matches},
            )
        else:
            log_hook_execution(
                HOOK_NAME,
                "approve",
                "no reference comments found",
            )

    except Exception as e:
        print(f"[{HOOK_NAME}] Error: {e}", file=sys.stderr)
        log_hook_execution(HOOK_NAME, "approve", f"error: {e}")

    print(json.dumps(result))


if __name__ == "__main__":
    main()
