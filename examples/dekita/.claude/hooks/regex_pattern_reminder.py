#!/usr/bin/env python3
"""正規表現パターン実装時にAGENTS.mdチェックリストを表示。

Why:
    正規表現実装でよくあるミス（成功条件確認漏れ、フラグ不一致等）を
    防ぐため、編集時にチェックリストを提示する。

What:
    - Edit操作でPythonファイルを検出
    - new_stringに正規表現パターン（re.compile, PATTERN= 等）があるか確認
    - 検出時はAGENTS.mdのチェックリストをsystemMessageで表示
    - 同一ファイルへのリマインドは1セッション1回

State:
    - writes: /tmp/claude-hooks/regex-pattern-reminded-{session_id}.json

Remarks:
    - 非ブロック型（情報提供のみ）
    - PreToolUse:Edit フック
    - AGENTS.md「パターンマッチング実装（P1）」を仕組み化

Changelog:
    - silenvx/dekita#2375: フック追加（パターンマッチング実装チェック漏れ防止）
    - silenvx/dekita#2529: ppidフォールバック廃止
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

from lib.execution import log_hook_execution
from lib.session import parse_hook_input

# Hook name for logging
HOOK_NAME = "regex-pattern-reminder"

# Regex patterns to detect in new_string
REGEX_DETECTION_PATTERNS = [
    r"re\.compile\s*\(",
    r"re\.search\s*\(",
    r"re\.match\s*\(",
    r"re\.findall\s*\(",
    r"re\.sub\s*\(",
    r"re\.split\s*\(",
    r"[A-Z_]*PATTERN\s*=",  # PATTERN = , _PATTERN = , SOME_PATTERN =
    r"[A-Z_]*PATTERNS\s*=",  # PATTERNS = , _PATTERNS =
]

COMPILED_DETECTION_PATTERN = re.compile("|".join(REGEX_DETECTION_PATTERNS))


def get_session_id() -> str | None:
    """Get current session ID (returns None if not available).

    Issue #2529: ppidフォールバック完全廃止、Noneを返す。
    """
    return None


def get_project_root() -> str:
    """Get project root directory."""
    return os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())


def get_confirmation_file_path() -> Path:
    """Get path to session confirmation tracking file.

    Uses a temp file named with session ID to persist state across hook invocations.

    Returns:
        Path to the session confirmation file.
    """
    session_id = get_session_id()
    base_dir = Path(tempfile.gettempdir()) / "claude-hooks"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / f"regex-pattern-reminded-{session_id}.json"


def load_reminded_files() -> set[str]:
    """Load reminded files from session file.

    Returns:
        Set of reminded file paths.
    """
    try:
        conf_file = get_confirmation_file_path()
        if conf_file.exists():
            data = json.loads(conf_file.read_text())
            return set(data.get("files", []))
    except (OSError, json.JSONDecodeError):
        # File doesn't exist or is corrupted - treat as empty
        pass
    return set()


def save_reminded_files(files: set[str]) -> None:
    """Save reminded files to session file.

    Args:
        files: Set of reminded file paths.
    """
    try:
        conf_file = get_confirmation_file_path()
        conf_file.write_text(json.dumps({"files": list(files)}))
    except OSError:
        # Best effort - don't fail on I/O errors
        pass


def is_python_file(file_path: str) -> bool:
    """Check if file is a Python file.

    Args:
        file_path: File path to check.

    Returns:
        True if file is a Python file.
    """
    return file_path.endswith(".py")


def contains_regex_pattern(new_string: str) -> bool:
    """Check if new_string contains regex pattern definitions.

    Args:
        new_string: The new string being added in Edit operation.

    Returns:
        True if regex patterns are detected.
    """
    if not new_string:
        return False
    return bool(COMPILED_DETECTION_PATTERN.search(new_string))


def is_reminded_in_session(file_path: str) -> bool:
    """Check if file has been reminded in current session.

    Args:
        file_path: Absolute file path to check.

    Returns:
        True if already reminded in this session.
    """
    normalized_path = str(Path(file_path).resolve())
    reminded = load_reminded_files()
    return normalized_path in reminded


def mark_as_reminded(file_path: str) -> None:
    """Mark file as reminded in current session.

    Args:
        file_path: Absolute file path to mark.
    """
    normalized_path = str(Path(file_path).resolve())
    reminded = load_reminded_files()
    reminded.add(normalized_path)
    save_reminded_files(reminded)


def get_relative_path(file_path: str) -> str | None:
    """Get relative path from project root.

    Args:
        file_path: Absolute file path.

    Returns:
        Relative path from project root, or None if outside project.
    """
    project_root_path = Path(get_project_root()).resolve()
    file_path_obj = Path(file_path).resolve()
    try:
        relative = file_path_obj.relative_to(project_root_path)
    except ValueError:
        return None
    return str(relative)


def main():
    """Check if editing Python files with regex patterns and show checklist."""
    result = {"decision": "approve"}
    file_path = ""

    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        file_path = tool_input.get("file_path", "")
        new_string = tool_input.get("new_string", "")

        if not file_path:
            # No file path provided, skip
            pass
        elif not is_python_file(file_path):
            # Not a Python file, skip
            pass
        elif not contains_regex_pattern(new_string):
            # No regex patterns in new_string, skip
            pass
        elif is_reminded_in_session(file_path):
            # Already reminded in this session
            pass
        else:
            # First regex pattern edit - show checklist
            rel_path = get_relative_path(file_path) or file_path

            result["systemMessage"] = f"""⚠️ パターンマッチング実装チェックリスト ({rel_path})

**AGENTS.md「パターンマッチング実装（P1）」より:**

| チェック項目 | 説明 |
|-------------|------|
| **複数条件の組み合わせ** | 「成功条件の存在」を積極的に確認し、「失敗条件の不在」のみで成功と判断しない |
| **フラグの一貫性** | `re.IGNORECASE` 等のフラグは全ての検索で統一する |
| **テストのリアリティ** | 実際の出力を模倣（stdout/stderr両方を考慮） |
| **距離制限** | `.*` を使用する場合、`.{{0,N}}` のように距離制限を検討 |

💡 実装前にエッジケースを洗い出し、テストケースを先に書くと見落としを防げます。"""

            # Mark as reminded for this session
            mark_as_reminded(file_path)

    except Exception as e:
        # Don't block on errors
        print(f"[{HOOK_NAME}] Hook error: {e}", file=sys.stderr)
        result = {"decision": "approve", "reason": f"Hook error: {e}"}

    log_hook_execution(
        HOOK_NAME,
        result.get("decision", "approve"),
        result.get("systemMessage"),
        {"file_path": file_path} if file_path else None,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
