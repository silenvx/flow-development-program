#!/usr/bin/env python3
"""Issue編集時のスコープ確認を強制する。

Why:
    1つのIssueに異なるタスクを追加すると追跡性が低下する。
    1Issue1タスクの原則を強制することで、Issueの管理性を向上させる。

What:
    - gh issue edit --bodyコマンドを検出
    - チェックボックスのみの変更は許可（進捗更新のため）
    - 内容追加時はスコープ確認を強制しブロック
    - SKIP_ISSUE_SCOPE_CHECK環境変数でバイパス可能

Remarks:
    - ブロック型フック（内容追加時はブロック）
    - PreToolUse:Bashで発火（gh issue editコマンド）
    - issue-multi-problem-check.pyはIssue作成時のみ対象（責務分離）
    - forkセッションではSKIP環境変数を許可しない

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#2423: チェックボックス更新を許可する機能を追加
    - silenvx/dekita#2431: SKIP環境変数サポートと拒否メッセージの改善
"""

import json
import os
import re
import subprocess
import sys

from lib.execution import log_hook_execution
from lib.results import make_approve_result, make_block_result
from lib.session import is_fork_session, parse_hook_input
from lib.strings import extract_inline_skip_env, is_skip_env_enabled

HOOK_NAME = "issue-scope-check"
SKIP_ENV_NAME = "SKIP_ISSUE_SCOPE_CHECK"


def extract_issue_number(command: str) -> str | None:
    """コマンドからIssue番号を抽出する."""
    # gh issue edit 123 --body "..." or gh issue edit #123 -b "..."
    match = re.search(r"gh\s+issue\s+edit\s+#?(\d+)", command)
    if match:
        return match.group(1)
    return None


def get_current_issue_body(issue_number: str) -> str | None:
    """GitHub APIで現在のIssue bodyを取得する."""
    try:
        result = subprocess.run(
            ["gh", "issue", "view", issue_number, "--json", "body", "--jq", ".body"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # gh CLI未インストールまたはタイムアウト時は取得失敗として扱う
        # → Noneを返し、チェックボックス判定をスキップしてブロック（安全側）
        pass
    return None


def extract_body_from_command(command: str) -> str | None:
    """コマンドから--bodyオプションの値を抽出する.

    サポートするパターン:
    1. 単純なクォート: --body "content" または --body 'content'
    2. heredoc: --body "$(cat <<'EOF' ... EOF)" 等のバリエーション
       - EOF のクォート: 'EOF', "EOF", EOF（クォートなし）
       - 終端パターン: EOF)", EOF"), EOF), EOF", EOF + 空白, EOF + 文字列末尾

    heredocパターンを先にチェックすることで、heredoc内のクォートで
    誤って終端しないようにする。
    """
    # --body "$(cat <<'EOF' ... EOF)" パターン（先にチェック）
    # heredoc内の内容を抽出する。終端は EOF)", EOF"), EOF), EOF" などを許容する
    # heredocパターンを先にチェックすることで、内部のクォートで誤マッチしない
    match = re.search(
        r"--body\s+\"\$\(\s*cat\s+<<['\"]?EOF['\"]?\s*\n"
        r"(?P<body>.*?)"
        r"\nEOF(?:\)\"|\"\)|\)|\"|\s|$)",
        command,
        re.DOTALL,
    )
    if match:
        return match.group("body")

    # クォート種別をキャプチャして、対応する終端クォートまでを非貪欲に取得する
    # heredocパターンにマッチしなかった場合のフォールバック
    match = re.search(
        r"--body\s+(?P<quote>['\"])(?P<body>.*?)(?P=quote)",
        command,
        re.DOTALL,
    )
    if match:
        return match.group("body")

    return None


def is_checkbox_only_change(old_body: str, new_body: str) -> bool:
    """変更がチェックボックスのステータス変更のみかどうかを判定する.

    チェックボックスのステータス変更:
    - [ ] → [x] または [X]
    - [x] または [X] → [ ]

    上記以外の変更がある場合はFalseを返す。

    注意:
    - old_body / new_body が None の場合は判定不能として False を返す。
    - old_body / new_body が空文字列 "" の場合も、チェックボックス変更とはみなさず False を返す。
    """
    # None や空文字列の body は「チェックボックスのみの変更」とはみなさない
    if old_body is None or new_body is None:
        return False
    if old_body == "" or new_body == "":
        return False

    # 行ごとに比較
    old_lines = old_body.splitlines()
    new_lines = new_body.splitlines()

    # 行数が異なる場合は内容追加/削除あり
    if len(old_lines) != len(new_lines):
        return False

    # Markdownでは -, *, + がリストマーカーとして有効なため、いずれも許可する
    checkbox_pattern = re.compile(r"^(\s*[-*+]\s*)\[([ xX])\](.*)$")

    for old_line, new_line in zip(old_lines, new_lines, strict=True):
        if old_line == new_line:
            continue

        # 両方がチェックボックス行かどうか
        old_match = checkbox_pattern.match(old_line)
        new_match = checkbox_pattern.match(new_line)

        if old_match and new_match:
            # プレフィックスと内容が同じで、チェック状態のみ異なる場合はOK
            if old_match.group(1) == new_match.group(1) and old_match.group(3) == new_match.group(
                3
            ):
                # チェック状態のみ異なる
                continue

        # チェックボックス以外の変更がある
        return False

    return True


def main():
    """PreToolUse hook for Bash commands."""
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # gh issue edit コマンドを検出
        if "gh issue edit" not in command:
            # 対象外のコマンドは何も出力しない
            sys.exit(0)

        # --body オプションで内容を変更しようとしている場合のみ
        if "--body" not in command:
            # --body なしは対象外
            sys.exit(0)

        # Issue #2458: forkセッション判定（SKIPチェックの前に実行）
        session_id = data.get("session_id", "")
        source = data.get("source", "")
        transcript_path = data.get("transcript_path")
        is_fork = is_fork_session(session_id, source, transcript_path)

        # Issue #2431: SKIP環境変数のチェック（エクスポートとインライン両対応）
        # Issue #2458: forkセッションではSKIPを許可しない
        skip_requested = is_skip_env_enabled(os.environ.get(SKIP_ENV_NAME)) or is_skip_env_enabled(
            extract_inline_skip_env(command, SKIP_ENV_NAME)
        )

        if skip_requested:
            if is_fork:
                # forkセッションではSKIPを許可しない
                log_hook_execution(
                    HOOK_NAME,
                    "block",
                    f"fork-session: {SKIP_ENV_NAME} not allowed",
                )
                result = make_block_result(
                    HOOK_NAME,
                    f"""[issue-scope-check] 🚫 forkセッションではSKIP不可

forkセッションでは{SKIP_ENV_NAME}は使用できません。
forkセッションは別タスクとして扱うべきです。

【対応方法】
新しいIssueを作成してください:
gh issue create --title "..." --body "..."
""",
                )
                print(json.dumps(result))
                sys.exit(0)

            # 通常セッションではSKIPを許可
            log_hook_execution(
                HOOK_NAME,
                "approve",
                f"{SKIP_ENV_NAME}=1: スコープ確認をスキップ",
            )
            result = make_approve_result(HOOK_NAME, f"{SKIP_ENV_NAME}=1")
            print(json.dumps(result))
            sys.exit(0)

        # Issue #2423: チェックボックス更新のみの場合は許可
        issue_number = extract_issue_number(command)
        if issue_number:
            current_body = get_current_issue_body(issue_number)
            new_body = extract_body_from_command(command)

            if current_body and new_body and is_checkbox_only_change(current_body, new_body):
                # チェックボックス更新のみなので許可
                result = make_approve_result(HOOK_NAME, "checkbox status change only")
                log_hook_execution(HOOK_NAME, "approve", "checkbox status change only")
                print(json.dumps(result))
                sys.exit(0)

            # チェックボックス判定を行えなかった理由をログに残す
            if not current_body:
                log_hook_execution(HOOK_NAME, "skip", "Failed to get current issue body")
            if not new_body:
                log_hook_execution(HOOK_NAME, "skip", "Failed to extract new body from command")

        # スコープ確認を強制（ブロック）
        # Issue番号があればスキップコマンドに含める
        issue_num_for_msg = issue_number if issue_number else "<Issue番号>"
        block_message = f"""🚫 Issue編集時のスコープ確認

Issueに内容を追加する前に確認が必要です:
- 追加しようとしている内容は、元のIssueと同じタスクですか？
- 異なるタスクであれば、別のIssueとして作成すべきです
- 1 Issue = 1 タスク の原則を守ってください

【対応方法】
1. 同じタスクの場合: ユーザーに確認してから編集を続行
2. 異なるタスクの場合: gh issue create --title "..." --body "..." で新規作成

【スキップ方法】（ユーザー確認済みの場合）
```
SKIP_ISSUE_SCOPE_CHECK=1 gh issue edit {issue_num_for_msg} --body "..."
```

【補足】
- チェックボックスのステータス変更のみの場合は自動許可されます
- 行数が変わる変更（セクション追加など）はブロックされます
"""
        # make_block_result内でlog_hook_executionが自動呼び出しされる
        result = make_block_result(HOOK_NAME, block_message)
        print(json.dumps(result))
        sys.exit(2)

    except Exception as e:
        print(f"[{HOOK_NAME}] Hook error: {e}", file=sys.stderr)
        result = make_approve_result(HOOK_NAME, f"Hook error: {e}")
        log_hook_execution(HOOK_NAME, "approve", f"Hook error: {e}")
        print(json.dumps(result))
        sys.exit(0)


if __name__ == "__main__":
    main()
