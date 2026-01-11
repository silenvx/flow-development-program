#!/usr/bin/env python3
"""gh pr mergeでの--bodyオプション使用をブロックする。

Why:
    --bodyオプションはPRの詳細な説明（背景、変更内容等）を短い要約で
    上書きしてしまい、コミット履歴から有用な情報が失われる。

What:
    - gh pr mergeコマンドを検出
    - --body/-bオプションが使用されていたらブロック
    - PRボディ更新後にマージする正しい方法を案内

Remarks:
    - ブロック型フック（--body使用時はブロック）
    - PreToolUse:Bashで発火（gh pr mergeコマンド）
    - pr-body-quality-check.pyはPR作成時（責務分離）
    - 正しい方法: gh pr edit → gh pr merge --squash

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import json
import re
import sys

from lib.execution import log_hook_execution
from lib.results import make_block_result
from lib.session import parse_hook_input
from lib.strings import strip_quoted_strings


def is_gh_pr_merge_command(command: str) -> bool:
    """Check if command is a gh pr merge command.

    Uses simple pattern matching consistent with pr-body-quality-check.py.
    """
    if not command.strip():
        return False
    stripped_command = strip_quoted_strings(command)
    return bool(re.search(r"\bgh\s+pr\s+merge\b", stripped_command))


def has_body_option(command: str) -> bool:
    """Check if command has --body or -b option."""
    # Don't strip quoted strings - we need to check if body option exists
    return bool(re.search(r"(?:--body\b|-b\b)", command))


def format_block_message() -> str:
    """Format the block message for --body usage."""
    message = "🚫 gh pr merge での --body オプション使用は禁止されています\n\n"

    message += "**理由:**\n"
    message += "- `--body` はPRの詳細な説明（## なぜ、## 何を等）を上書きしてしまう\n"
    message += "- コミット履歴から有用な情報が失われる\n\n"

    message += "**正しい方法:**\n"
    message += "```bash\n"
    message += "# PRボディを更新（必要な場合）\n"
    message += "gh pr edit {PR} --body \"$(cat <<'EOF'\n"
    message += "## なぜ\n"
    message += "背景・理由を記述\n"
    message += "\n"
    message += "## 何を\n"
    message += "変更内容の概要\n"
    message += "\n"
    message += "Closes #XXX\n"
    message += "EOF\n"
    message += ')"\n'
    message += "\n"
    message += "# マージ（--body なし）\n"
    message += "gh pr merge {PR} --squash --delete-branch\n"
    message += "```\n"

    message += "\n**参照:** development-workflow Skill「squashマージ時」セクション\n"

    return message


def main():
    """
    PreToolUse hook for Bash commands.

    Blocks `gh pr merge --body "..."` or `gh pr merge -b "..."`.
    The --body option overwrites the PR description and should not be used.
    """
    result = {"decision": "approve"}

    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        if is_gh_pr_merge_command(command):
            if has_body_option(command):
                reason = format_block_message()
                result = make_block_result("merge-commit-quality-check", reason)
            else:
                result["systemMessage"] = (
                    "✅ merge-commit-quality-check: --body なし（PRボディがコミットメッセージになります）"
                )

    except Exception as e:
        print(f"[merge-commit-quality-check] Hook error: {e}", file=sys.stderr)
        result = {"decision": "approve"}

    # Log only for non-block decisions (make_block_result() logs automatically)
    if result.get("decision") != "block":
        log_hook_execution(
            "merge-commit-quality-check",
            result.get("decision", "approve"),
            result.get("reason"),
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
