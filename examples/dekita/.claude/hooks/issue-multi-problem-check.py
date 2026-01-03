#!/usr/bin/env python3
"""Issue作成時に複数問題を1Issueにまとめていないかチェックする。

Why:
    1つのIssueに複数の問題を含めると、議論が分散し解決が遅れる。
    1Issue1問題の原則を強制することで、追跡性と解決速度を向上させる。

What:
    - gh issue createコマンドからタイトルを抽出
    - 複数問題パターン（「AとBの実装」等）を検出
    - 検出時はブロックして分離を促す

Remarks:
    - ブロック型フック（複数問題検出時はブロック）
    - PreToolUse:Bashで発火（gh issue createコマンド）
    - issue-scope-check.pyはIssue編集時のみ対象（責務分離）
    - 除外パターンで「検出と警告」等の関連動作は許可

Changelog:
    - silenvx/dekita#1981: フック追加
    - silenvx/dekita#1991: 重複警告防止
    - silenvx/dekita#2240: ブロック型に変更
"""

import json
import re
import shlex
import sys

from lib.results import make_block_result
from lib.session import parse_hook_input

HOOK_NAME = "issue-multi-problem-check"

# 複数問題を示すパターン（日本語）
# CUSTOMIZE: 言語に合わせてパターンを調整
MULTI_PROBLEM_PATTERNS_JA = [
    # 「AとBの改善」「AとBを実装」のようなパターン
    # ただし「検出と警告」のような関連動作は除外
    (r"(.+)と(.+)の(実装|改善|修正|追加|削除|対応)", "「{0}」と「{1}」を分離すべき可能性"),
    # 「A、Bを実装」のようなパターン
    (r"(.+)、(.+)を(実装|改善|修正|追加|削除)", "「{0}」と「{1}」を分離すべき可能性"),
    # 「AおよびB」のようなパターン
    (r"(.+)および(.+)", "「{0}」と「{1}」を分離すべき可能性"),
]

# 複数問題を示すパターン（英語）
MULTI_PROBLEM_PATTERNS_EN = [
    # "A and B implementation" pattern
    (
        r"(.+) and (.+) (implementation|improvement|fix|addition)",
        "'{0}' and '{1}' should be separate issues",
    ),
]

# 除外パターン（誤検知防止）
# CUSTOMIZE: プロジェクト固有の用語を追加
EXCLUDE_PATTERNS = [
    r"検出.*警告",  # 関連動作
    r"作成.*削除",  # 対になる操作
    r"追加.*更新",  # 関連操作
    r"読み.*書き",  # 対になる操作
    r"入力.*出力",  # 対になる操作
    r"開始.*終了",  # 対になる操作
    r"create.*delete",  # 対になる操作（英語）
    r"read.*write",  # 対になる操作（英語）
    r"start.*stop",  # 対になる操作（英語）
]


def extract_title_from_command(command: str) -> str | None:
    """gh issue create コマンドからタイトルを抽出

    Uses shlex.split() for robust parsing of command-line arguments.
    This handles edge cases better than regex:
    - Properly handles quoted strings with spaces
    - Handles escaped characters
    - Handles --title=value format
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None

    i = 0
    while i < len(tokens):
        token = tokens[i]

        # --title value or -t value
        if token in ("--title", "-t") and i + 1 < len(tokens):
            return tokens[i + 1]

        # --title=value
        if token.startswith("--title="):
            return token[len("--title=") :]

        # -t=value
        if token.startswith("-t="):
            return token[len("-t=") :]

        i += 1

    return None


def check_multi_problem_patterns(title: str) -> list[str]:
    """タイトルに複数問題パターンが含まれているかチェック

    最初にマッチしたパターンのみを使用する（重複警告防止）。
    Issue #1991: 複数パターンが同じタイトルにマッチした場合の重複を防ぐ。
    """
    # 除外パターンに該当する場合はスキップ
    for exclude_pattern in EXCLUDE_PATTERNS:
        if re.search(exclude_pattern, title, re.IGNORECASE):
            return []

    # 日本語パターンをチェック（最初のマッチで終了）
    for pattern, message_template in MULTI_PROBLEM_PATTERNS_JA:
        match = re.search(pattern, title)
        if match:
            groups = match.groups()
            if len(groups) >= 2:
                return [message_template.format(groups[0], groups[1])]

    # 英語パターンをチェック（最初のマッチで終了）
    for pattern, message_template in MULTI_PROBLEM_PATTERNS_EN:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            groups = match.groups()
            if len(groups) >= 2:
                return [message_template.format(groups[0], groups[1])]

    return []


def main():
    """PreToolUse hook for Bash commands."""
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # gh issue create コマンドを検出
        if "gh issue create" not in command:
            sys.exit(0)

        # タイトルを抽出
        title = extract_title_from_command(command)
        if not title:
            sys.exit(0)

        # 複数問題パターンをチェック
        warnings = check_multi_problem_patterns(title)

        if warnings:
            block_message = f"""🚫 このIssueは複数の問題を含んでいる可能性があります。

タイトル: {title}

検出されたパターン:
{chr(10).join(f"  - {w}" for w in warnings)}

**1つのIssue = 1つの問題** を徹底してください。
分離が必要な場合は、別々のIssueを作成してください。

【対応方法】
1. 問題を分離して複数のIssueを作成
2. 誤検知の場合: ユーザーに確認してから続行
"""
            # make_block_result内でlog_hook_executionが自動呼び出しされる
            result = make_block_result(HOOK_NAME, block_message)
            print(json.dumps(result))
            sys.exit(2)

        # パターンに該当しない場合は何も出力しない
        sys.exit(0)

    except Exception as e:
        print(f"[{HOOK_NAME}] Hook error: {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
