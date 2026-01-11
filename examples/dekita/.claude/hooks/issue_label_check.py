#!/usr/bin/env python3
"""gh issue create時に--labelオプションの指定を強制する。

Why:
    ラベルなしのIssueは分類・検索・優先度管理が困難になる。
    Issue作成時にラベルを強制することで、Issue管理の質を維持する。

What:
    - gh issue createコマンドを検出
    - --labelオプションの有無をチェック
    - ラベルがない場合、タイトル/ボディから適切なラベルを自動提案
    - ブロックし、推奨コマンドを表示

Remarks:
    - ブロック型フック
    - issue-priority-label-checkは優先度ラベル専用、本フックはラベル有無の確認

Changelog:
    - silenvx/dekita#xxx: フック追加
    - silenvx/dekita#2451: タイトル/ボディからラベル自動提案機能を追加
"""

import json
import os.path
import shlex
import sys

from lib.execution import log_hook_execution
from lib.labels import (
    extract_body_from_command,
    extract_title_from_command,
    suggest_labels_from_text,
)
from lib.results import make_approve_result, make_block_result
from lib.session import parse_hook_input

HOOK_NAME = "issue-label-check"


def _is_gh_command(token: str) -> bool:
    """Check if a token represents the gh command (bare name or full path).

    Uses os.path.basename to correctly extract the executable name,
    avoiding false positives for commands like /usr/bin/fakegh.
    """
    # Get the basename (executable name) from the token
    # This handles both "gh" and "/usr/local/bin/gh" correctly
    # and avoids false positives like "/usr/bin/fakegh"
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
    """
    コマンドが実際に gh issue create で始まるかチェック。

    単純な部分文字列マッチングではなく、トークン化して
    先頭のコマンドが gh issue create であることを確認する。
    これにより、コミットメッセージや引数内に「gh issue create」が
    含まれていても誤検知しない。

    環境変数プレフィックス（例: GH_TOKEN=xxx gh issue create）も
    正しく検出する。

    フルパス指定（例: /usr/local/bin/gh issue create）も
    正しく検出する。
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        # クォートが閉じていない等の場合はフォールバック
        parts = command.split()
        # 環境変数プレフィックスをスキップ
        remaining = _skip_env_prefixes(parts)
        if len(remaining) < 3:
            return False
        return _is_gh_command(remaining[0]) and remaining[1] == "issue" and remaining[2] == "create"

    # 環境変数プレフィックス（VAR=value形式）をスキップ
    remaining = _skip_env_prefixes(tokens)

    # 残りのトークンが3つ以上あり、gh issue create かチェック
    if len(remaining) < 3:
        return False
    return _is_gh_command(remaining[0]) and remaining[1] == "issue" and remaining[2] == "create"


def has_label_option(command: str) -> bool:
    """
    コマンドに --label オプションが指定されているかチェック。

    タイトルやボディ内の文字列ではなく、実際のオプションとして
    指定されているかを判定する。
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        # クォートが閉じていない等の場合はフォールバック
        return "--label" in command.split() or "-l" in command.split()

    # --label または -l がトークンとして存在するかチェック
    for i, token in enumerate(tokens):
        # --label value または --label=value
        if token == "--label" or token.startswith("--label="):
            return True
        # -l value（短縮形）
        if token == "-l" and i + 1 < len(tokens):
            return True
    return False


def main():
    """PreToolUse hook for Bash commands."""
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # gh issue create コマンドを検出（先頭のコマンドをチェック）
        if not is_gh_issue_create_command(command):
            # 対象外のコマンドは何も出力しない
            sys.exit(0)

        # --label オプションが指定されているかチェック
        if has_label_option(command):
            # ラベルあり: 許可（出力なし）
            log_hook_execution(HOOK_NAME, "approve")
            sys.exit(0)

        # ラベルなしでIssue作成しようとしている
        # タイトル/ボディからラベルを提案
        title = extract_title_from_command(command)
        body = extract_body_from_command(command)
        suggestions = suggest_labels_from_text(title or "", body)

        reason_lines = [
            "Issue作成時に --label オプションが指定されていません。",
            "",
        ]

        if suggestions:
            # 提案がある場合はそれを表示
            suggested_labels = [label for label, _ in suggestions]
            reason_lines.append("**📝 内容から検出したラベル候補:**")
            reason_lines.append("")
            for label, description in suggestions:
                reason_lines.append(f"- `{label}`: {description}")
            reason_lines.append("")
            reason_lines.append("**推奨コマンド（優先度ラベルを追加してください）:**")
            reason_lines.append("")
            reason_lines.append("```bash")
            # 優先度P2を追加した推奨コマンドを生成
            all_labels = ",".join(suggested_labels + ["P2"])
            if title:
                # shlex.quoteで安全にエスケープ（コマンドインジェクション対策）
                escaped_title = shlex.quote(title)
                reason_lines.append(
                    f'gh issue create --title {escaped_title} --body "..." --label "{all_labels}"'
                )
            else:
                reason_lines.append(
                    f'gh issue create --title "..." --body "..." --label "{all_labels}"'
                )
            reason_lines.append("```")
            reason_lines.append("")
            reason_lines.append("**優先度の選択:**")
        else:
            # 提案がない場合は利用可能なラベル一覧を表示
            reason_lines.append("利用可能なラベルを確認してください:")
            reason_lines.append("")
            reason_lines.append("```")
            reason_lines.append("gh label list")
            reason_lines.append("```")
            reason_lines.append("")
            reason_lines.append("**主なラベル:**")
            reason_lines.append("")
            reason_lines.append("- `bug`: バグ報告")
            reason_lines.append("- `enhancement`: 新機能")
            reason_lines.append("- `documentation`: ドキュメント改善")
            reason_lines.append("")
            reason_lines.append("**優先度（必須）:**")

        reason_lines.append("")
        reason_lines.append("| 優先度 | 説明 |")
        reason_lines.append("|--------|------|")
        reason_lines.append("| P0 | Critical - 即座に対応 |")
        reason_lines.append("| P1 | High - 早急に対応 |")
        reason_lines.append("| P2 | Medium - 通常の優先度（迷ったらこれ） |")
        reason_lines.append("| P3 | Low - 時間があれば対応 |")

        reason = "\n".join(reason_lines)
        result = make_block_result(HOOK_NAME, reason)
        log_hook_execution(HOOK_NAME, "block", "label option missing")
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
