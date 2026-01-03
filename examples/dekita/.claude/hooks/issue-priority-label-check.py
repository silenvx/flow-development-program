#!/usr/bin/env python3
"""gh issue create時に優先度ラベル（P0-P3）の指定を強制する。

Why:
    GitHubのIssueテンプレートでdropdownの優先度を必須にしても、
    選択した値はラベルとして自動付与されない。gh CLI経由での
    作成時はテンプレート自体が適用されない。優先度ラベルを
    強制することでIssueの優先順位管理を徹底する。

What:
    - gh issue createコマンドを検出
    - --labelオプションから優先度ラベル（P0-P3）を確認
    - 優先度ラベルがない場合はブロック

Remarks:
    - issue-label-checkはラベル有無のみ確認、これは優先度ラベル専用
    - P0: Critical、P1: High、P2: Medium、P3: Low
"""

import json
import os.path
import shlex
import sys

from lib.execution import log_hook_execution
from lib.labels import extract_labels_from_command, has_priority_label
from lib.results import make_approve_result, make_block_result
from lib.session import parse_hook_input

HOOK_NAME = "issue-priority-label-check"


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
        # クォートが閉じていない等の場合はフォールバック
        parts = command.split()
        remaining = _skip_env_prefixes(parts)
        if len(remaining) < 3:
            return False
        return _is_gh_command(remaining[0]) and remaining[1] == "issue" and remaining[2] == "create"

    remaining = _skip_env_prefixes(tokens)

    if len(remaining) < 3:
        return False
    return _is_gh_command(remaining[0]) and remaining[1] == "issue" and remaining[2] == "create"


def main():
    """PreToolUse hook for Bash commands."""
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # gh issue create コマンドを検出
        if not is_gh_issue_create_command(command):
            sys.exit(0)

        # ラベルを抽出して優先度チェック
        labels = extract_labels_from_command(command)
        if has_priority_label(labels):
            log_hook_execution(HOOK_NAME, "approve")
            sys.exit(0)

        # 優先度ラベルなし: ブロック
        reason_lines = [
            "優先度ラベル（P0-P3）が指定されていません。",
            "",
            "Issueには必ず優先度を指定してください:",
            "",
            "| 優先度 | 説明 |",
            "|--------|------|",
            "| P0 | Critical - ビジネス上必須、即座に対応 |",
            "| P1 | High - 早急に対応が必要 |",
            "| P2 | Medium - 通常の優先度 |",
            "| P3 | Low - 時間があれば対応 |",
            "",
            "例:",
            "```bash",
            'gh issue create --title "..." --body "..." --label "enhancement,P2"',
            "```",
            "",
            "迷ったら P2 を選択してください。",
        ]
        reason = "\n".join(reason_lines)

        result = make_block_result(HOOK_NAME, reason)
        log_hook_execution(HOOK_NAME, "block", "priority label missing")
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
