#!/usr/bin/env python3
"""gh issue create時にIssue本文の必須項目をチェックする。

Why:
    調査なしにIssueを作成すると、実装時に問題の把握に時間がかかる。
    必須セクション（なぜ/現状/期待動作）を強制することで、
    Issue作成時点での十分な調査を促す。

What:
    - gh issue createコマンドを検出
    - --body オプションから本文を抽出
    - 必須セクション（なぜ/現状/期待動作）の存在を確認
    - 不足している場合はブロック

Remarks:
    - trivial/documentationラベルでスキップ可能
    - issue-investigation-reminderを置き換えた後継フック

Changelog:
    - silenvx/dekita#2455: フック追加
"""

import json
import os.path
import re
import shlex
import sys
from pathlib import Path

from lib.execution import log_hook_execution
from lib.results import make_approve_result, make_block_result
from lib.session import parse_hook_input

HOOK_NAME = "issue-body-requirements-check"

# 必須セクションのパターン定義
# 各パターンは (名前, 正規表現, 説明) のタプル
REQUIRED_SECTIONS = [
    (
        "なぜ/背景",
        r"^(?:##|###)\s*(?:なぜ|背景|理由|Why|Motivation|Background|Reason)",
        "変更の動機・背景を記載してください",
    ),
    (
        "現状/実際の動作",
        r"^(?:##|###)\s*(?:現状|実際|現在|Current|Actual|Status)",
        "現在の状態・実際の動作を記載してください",
    ),
    (
        "期待動作/対応案",
        r"^(?:##|###)\s*(?:期待|対応|Expected|Proposed|Solution|何を|What)",
        "期待する動作または対応案を記載してください",
    ),
]

# スキップ対象のラベル
SKIP_LABELS = ["trivial", "documentation", "docs"]


def _is_gh_command(token: str) -> bool:
    """Check if a token represents the gh command (bare name or full path)."""
    return os.path.basename(token) == "gh"


def _skip_env_prefixes(parts: list[str]) -> list[str]:
    """Skip VAR=value environment variable prefixes from token list."""
    cmd_start = 0
    for i, token in enumerate(parts):
        if "=" in token and not token.startswith("-"):
            cmd_start = i + 1
        else:
            break
    return parts[cmd_start:]


def is_gh_issue_create_command(command: str) -> bool:
    """Check if command starts with gh issue create."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False

    remaining = _skip_env_prefixes(tokens)

    if len(remaining) < 3:
        return False
    return _is_gh_command(remaining[0]) and remaining[1] == "issue" and remaining[2] == "create"


def extract_body_from_command(command: str) -> str | None:
    """Extract --body or --body-file option value from gh issue create command.

    Supports:
    - --body "content" / --body="content" / -b "content"
    - --body-file "path" / --body-file="path" / -F "path" (reads file content)

    If both --body and --body-file are specified, --body takes precedence.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None

    body: str | None = None
    body_file: str | None = None

    for i, token in enumerate(tokens):
        # --body "value" or --body value
        if token == "--body" and i + 1 < len(tokens):
            if body is None:
                body = tokens[i + 1]
            continue
        # --body="value"
        if token.startswith("--body="):
            if body is None:
                body = token[7:]
            continue
        # -b "value" or -b value (short form)
        if token == "-b" and i + 1 < len(tokens):
            if body is None:
                body = tokens[i + 1]
            continue
        # --body-file "path" or --body-file path
        if token == "--body-file" and i + 1 < len(tokens):
            if body_file is None:
                body_file = tokens[i + 1]
            continue
        # --body-file="path"
        if token.startswith("--body-file="):
            if body_file is None:
                body_file = token[12:]
            continue
        # -F "path" or -F path (short form for --body-file)
        if token == "-F" and i + 1 < len(tokens):
            if body_file is None:
                body_file = tokens[i + 1]
            continue

    # --body takes precedence over --body-file
    if body is not None:
        return body

    # If body-file was specified, read the file content with path traversal protection
    if body_file:
        try:
            # Path traversal protection: only allow files within cwd
            safe_directory = Path.cwd().resolve()
            file_path = Path(body_file).resolve()

            if not str(file_path).startswith(str(safe_directory)):
                # Path is outside the safe directory, reject it
                return None

            if file_path.exists():
                return file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # If file cannot be read, return None (will trigger block)
            pass

    return None


def extract_labels_from_command(command: str) -> list[str]:
    """Extract --label option values from gh issue create command."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return []

    labels = []
    for i, token in enumerate(tokens):
        if token == "--label" and i + 1 < len(tokens):
            # --label can contain comma-separated values
            labels.extend(tokens[i + 1].split(","))
        elif token.startswith("--label="):
            labels.extend(token[8:].split(","))
        elif token == "-l" and i + 1 < len(tokens):
            labels.extend(tokens[i + 1].split(","))

    return [label.strip().lower() for label in labels]


def should_skip_check(command: str, body: str | None) -> bool:
    """Check if the requirements check should be skipped."""
    # Skip if body contains "調査不要"
    if body and "調査不要" in body:
        return True

    # Skip if trivial or documentation label is present
    labels = extract_labels_from_command(command)
    return any(label in SKIP_LABELS for label in labels)


def check_required_sections(body: str) -> list[tuple[str, str]]:
    """Check if body contains all required sections.

    Returns:
        List of (section_name, description) for missing sections.
    """
    missing = []
    for name, pattern, description in REQUIRED_SECTIONS:
        if not re.search(pattern, body, re.MULTILINE | re.IGNORECASE):
            missing.append((name, description))
    return missing


def main():
    """PreToolUse hook for Bash commands."""
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # gh issue create コマンドを検出
        if not is_gh_issue_create_command(command):
            sys.exit(0)

        body = extract_body_from_command(command)

        # スキップ条件をチェック
        if should_skip_check(command, body):
            result = make_approve_result(
                HOOK_NAME, "スキップ条件に該当（trivial/documentation/調査不要）"
            )
            log_hook_execution(HOOK_NAME, "approve", "skip condition matched")
            print(json.dumps(result))
            sys.exit(0)

        # bodyがない場合はブロック
        if not body:
            message = "\n".join(
                [
                    "🚫 Issue本文（--body）が指定されていません。",
                    "",
                    "以下のセクションを含めてください:",
                    "- ## なぜ（変更の動機・背景）",
                    "- ## 現状（現在の状態・実際の動作）",
                    "- ## 期待動作 または ## 対応案",
                    "",
                    "スキップするには --label trivial または --label documentation を付与",
                ]
            )
            result = make_block_result(HOOK_NAME, message)
            log_hook_execution(HOOK_NAME, "block", "no body specified")
            print(json.dumps(result))
            sys.exit(2)

        # 必須セクションをチェック
        missing = check_required_sections(body)
        if missing:
            missing_list = "\n".join([f"- {name}: {desc}" for name, desc in missing])
            message = "\n".join(
                [
                    "🚫 Issue本文に必須セクションがありません。",
                    "",
                    "不足しているセクション:",
                    missing_list,
                    "",
                    "スキップするには --label trivial または --label documentation を付与",
                ]
            )
            result = make_block_result(HOOK_NAME, message)
            log_hook_execution(
                HOOK_NAME, "block", f"missing sections: {[name for name, _ in missing]}"
            )
            print(json.dumps(result))
            sys.exit(2)

        # 全て揃っている場合は承認
        result = make_approve_result(HOOK_NAME, "Issue本文の必須項目を確認しました")
        log_hook_execution(HOOK_NAME, "approve", "all required sections present")
        print(json.dumps(result))
        sys.exit(0)

    except Exception as e:
        print(f"[{HOOK_NAME}] Hook error: {e}", file=sys.stderr)
        result = make_approve_result(HOOK_NAME, f"Hook error: {e}")
        log_hook_execution(HOOK_NAME, "approve", f"Hook error: {e}")
        print(json.dumps(result))
        sys.exit(0)


if __name__ == "__main__":
    main()
