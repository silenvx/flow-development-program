#!/usr/bin/env python3
"""依存関係追加時にContext7/Web検索での最新情報確認を促す。

Why:
    古いAPIや非推奨メソッドの使用を防ぐため、パッケージ追加時に
    最新のドキュメントを確認する習慣を促進する。

What:
    - pnpm add, npm install, pip install等を検出
    - Context7やWeb検索での最新情報確認を促すメッセージを表示
    - セッション内で同じパッケージへの重複リマインドを防止

State:
    - writes: .claude/state/session/dependency-check-reminded-{session}.json

Remarks:
    - 情報提供型フック（ブロックしない、systemMessageでリマインド）
    - PreToolUse:Bashで発火（pnpm/npm/pip/uv等）
    - セッション内で同じパッケージへの重複リマインド防止
    - requirements.txtインストール（-r）は除外

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import json
import re
import sys
from pathlib import Path

from common import SESSION_DIR
from lib.execution import log_hook_execution
from lib.session import create_hook_context, parse_hook_input

# Package manager command patterns
# Note: Detection patterns match broadly, then package extraction filters actual packages
DEPENDENCY_COMMANDS = [
    # JavaScript/TypeScript
    (r"pnpm\s+add\s+", "pnpm add"),
    (r"npm\s+install\s+\S", "npm install"),  # Has at least one argument
    (r"npm\s+i\s+\S", "npm i"),  # Has at least one argument
    (r"yarn\s+add\s+", "yarn add"),
    # Python
    (r"pip\s+install\s+\S", "pip install"),  # Has at least one argument
    (r"uv\s+add\s+", "uv add"),
    (r"poetry\s+add\s+", "poetry add"),
    # Rust
    (r"cargo\s+add\s+", "cargo add"),
]

# Commands to exclude (requirements file installs)
EXCLUDE_PATTERNS = [
    r"pip\s+install\s+.*(?:-r|--requirement)\s",  # pip install with -r/--requirement
]

# Patterns to extract package names (supports scoped packages like @types/node)
PACKAGE_EXTRACTORS = {
    "pnpm add": r"pnpm\s+add\s+(?:-\S+\s+)*(\S+)",
    "npm install": r"npm\s+(?:install|i)\s+(?:-\S+\s+)*(\S+)",
    "npm i": r"npm\s+i\s+(?:-\S+\s+)*(\S+)",
    "yarn add": r"yarn\s+add\s+(?:-\S+\s+)*(\S+)",
    "pip install": r"pip\s+install\s+(?:-\S+\s+)*(\S+)",
    "uv add": r"uv\s+add\s+(?:-\S+\s+)*(\S+)",
    "poetry add": r"poetry\s+add\s+(?:-\S+\s+)*(\S+)",
    "cargo add": r"cargo\s+add\s+(?:-\S+\s+)*(\S+)",
}


def get_reminded_packages_file(session_id: str) -> Path:
    """Get the file tracking reminded packages for this session.

    Args:
        session_id: The Claude session ID to scope the file.

    Returns:
        Path to session-specific reminded packages file.
    """
    return SESSION_DIR / f"dependency-check-reminded-{session_id}.json"


def load_reminded_packages(session_id: str) -> set:
    """Load the set of packages already reminded in this session.

    Args:
        session_id: The Claude session ID.

    Returns:
        Set of packages already reminded.
    """
    file_path = get_reminded_packages_file(session_id)
    try:
        if file_path.exists():
            data = json.loads(file_path.read_text())
            return set(data.get("packages", []))
    except (json.JSONDecodeError, OSError):
        # Silently fail if file is missing, corrupt, or unreadable
        # This is non-critical - worst case is showing duplicate reminders
        pass
    return set()


def save_reminded_packages(session_id: str, packages: set) -> None:
    """Save the set of reminded packages.

    Args:
        session_id: The Claude session ID.
        packages: Set of packages to save.
    """
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    file_path = get_reminded_packages_file(session_id)
    try:
        file_path.write_text(json.dumps({"packages": list(packages)}))
    except OSError:
        # Silently fail if file cannot be written (permissions, disk full, etc.)
        # This is non-critical - worst case is showing duplicate reminders
        pass


def detect_dependency_command(command: str) -> tuple[str | None, str | None]:
    """Detect if command is a dependency management command.

    Returns:
        Tuple of (command_type, package_name) or (None, None) if not detected.
    """
    # Check exclusion patterns first
    for exclude_pattern in EXCLUDE_PATTERNS:
        if re.search(exclude_pattern, command, re.IGNORECASE):
            return None, None

    for pattern, cmd_type in DEPENDENCY_COMMANDS:
        if re.search(pattern, command, re.IGNORECASE):
            # Try to extract package name
            extractor = PACKAGE_EXTRACTORS.get(cmd_type)
            if extractor:
                match = re.search(extractor, command, re.IGNORECASE)
                if match:
                    package = match.group(1)
                    # Clean up package name (remove version specifiers)
                    # Handle scoped packages like @types/node@1.0.0
                    if package.startswith("@"):
                        # For scoped packages, find the second @ (version) if exists
                        at_pos = package.find("@", 1)
                        if at_pos != -1:
                            package = package[:at_pos]
                    else:
                        # For regular packages, remove version after @ or ^
                        package = re.sub(r"[@^~>=<].*$", "", package)
                    return cmd_type, package
            return cmd_type, None
    return None, None


def format_reminder_message(cmd_type: str, package: str | None) -> str:
    """Format the reminder message for dependency check."""
    lines = [
        "📦 **依存関係追加を検出**",
        "",
    ]

    if package:
        lines.extend(
            [
                f"パッケージ `{package}` を追加しようとしています。",
                "",
                "**最新情報を確認してください:**",
                "",
                f"1. **Context7**: `{package}` のドキュメントを参照",
                "   - `mcp__context7__resolve-library-id` でライブラリIDを取得",
                "   - `mcp__context7__get-library-docs` でドキュメントを取得",
                "",
                "2. **Web検索**: 最新バージョン・変更履歴を確認",
                f"   - 「{package} latest version」で検索",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "依存関係を追加しようとしています。",
                "",
                "**最新情報を確認してください:**",
                "",
                "- Context7でライブラリのドキュメントを参照",
                "- Web検索で最新バージョン・変更履歴を確認",
                "",
            ]
        )

    lines.append("💡 古いAPIや非推奨メソッドの使用を防ぐため、最新情報の確認を推奨します。")

    return "\n".join(lines)


def main():
    """
    PreToolUse hook for Bash commands.

    Detects dependency management commands and reminds to check latest docs.
    """
    result = {"decision": "approve"}

    try:
        # Read tool input from stdin (Claude Code passes hook data via stdin)
        input_data = parse_hook_input()

        ctx = create_hook_context(input_data)
        tool_input = input_data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Check if this is a dependency command
        cmd_type, package = detect_dependency_command(command)

        if cmd_type:
            # Get session ID for session-specific file
            session_id = ctx.get_session_id()

            # Check if we already reminded about this package
            reminded = load_reminded_packages(session_id)

            # Only remind once per package per session
            remind_key = package if package else cmd_type
            if remind_key not in reminded:
                result["systemMessage"] = format_reminder_message(cmd_type, package)
                reminded.add(remind_key)
                save_reminded_packages(session_id, reminded)

    except Exception as e:
        # Don't block on errors, just skip the reminder
        print(f"[dependency-check-reminder] Error: {e}", file=sys.stderr)

    log_hook_execution(
        "dependency-check-reminder", result.get("decision", "approve"), result.get("reason")
    )
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
