#!/usr/bin/env python3
"""PRボディに「なぜ」と「参照」が含まれているかチェックする。

Why:
    PRの背景・動機が不明なままマージすると、将来の変更履歴追跡が困難になる。
    また、Issue/PRへの参照がないとトレーサビリティが失われる。

What:
    - gh pr create/mergeコマンドを検出
    - PRボディから「なぜ」セクションの存在を確認
    - Issue/PR/ドキュメントへの参照を確認
    - 不足している場合はブロック

Remarks:
    - closes-keyword-checkはClosesキーワードの提案、これは全体品質チェック
    - --body-file/-F使用時はファイル内容を確認できないため警告のみ
    - 段階的移行PRにはIssue参照強制（Issue #2608）

Changelog:
    - silenvx/dekita#2406: フック追加
    - silenvx/dekita#2608: 段階的移行PRの残タスクIssue参照強制
"""

import json
import re
import subprocess
import sys

# Re-export for tests (has_why_section, has_reference used via module.has_why_section())
from check_utils import (
    check_body_quality,
    check_incremental_pr,
    has_reference,
    has_why_section,
)

# 静的解析ツール向けに「使用済み」であることを示すダミー参照
_REEXPORTED_SYMBOLS = (has_reference, has_why_section)
from lib.execution import log_hook_execution
from lib.results import make_block_result
from lib.session import parse_hook_input
from lib.strings import strip_quoted_strings


def is_gh_pr_create_command(command: str) -> bool:
    """Check if command is a gh pr create command."""
    if not command.strip():
        return False
    stripped_command = strip_quoted_strings(command)
    return bool(re.search(r"gh\s+pr\s+create\b", stripped_command))


def is_gh_pr_merge_command(command: str) -> bool:
    """Check if command is a gh pr merge command."""
    if not command.strip():
        return False
    stripped_command = strip_quoted_strings(command)
    return bool(re.search(r"gh\s+pr\s+merge\b", stripped_command))


def extract_pr_number_from_merge(command: str) -> str | None:
    """Extract PR number from gh pr merge command.

    Handles:
    - gh pr merge 123
    - gh pr merge #123
    - gh pr merge (uses current branch's PR)
    """
    stripped_command = strip_quoted_strings(command)

    # Match PR number after "merge"
    match = re.search(r"gh\s+pr\s+merge\s+#?(\d+)", stripped_command)
    if match:
        return match.group(1)

    # No explicit PR number - will use current branch's PR
    return None


def has_body_file_option(command: str) -> bool:
    """Check if command uses --body-file or -F option."""
    stripped_command = strip_quoted_strings(command)
    return bool(re.search(r"(?:--body-file|-F)\b", stripped_command))


def extract_pr_body(command: str) -> str | None:
    """Extract the PR body from gh pr create command.

    Handles:
    - --body "..."
    - -b "..."
    - --body="..."
    - HEREDOC patterns: --body "$(cat <<'EOF' ... EOF)"

    Returns None if body is not explicitly specified inline.
    """
    # Try HEREDOC pattern first (most complex)
    heredoc_pattern = r'--body\s+"\$\(cat\s+<<[\'"]?(\w+)[\'"]?\s*(.*?)\s*\1\s*\)"'
    heredoc_match = re.search(heredoc_pattern, command, re.DOTALL)
    if heredoc_match:
        return heredoc_match.group(2)

    # Standard patterns (ordered by specificity)
    dq_content = r'([^"\\]*(?:\\.[^"\\]*)*)'  # Double-quoted content with escapes
    sq_content = r"([^'\\]*(?:\\.[^'\\]*)*)"  # Single-quoted content with escapes
    patterns = [
        rf'--body="{dq_content}"',  # --body="..."
        rf"--body='{sq_content}'",  # --body='...'
        rf'-b="{dq_content}"',  # -b="..."
        rf"-b='{sq_content}'",  # -b='...'
        rf'--body\s+"{dq_content}"',  # --body "..."
        rf"--body\s+'{sq_content}'",  # --body '...'
        rf'-b\s+"{dq_content}"',  # -b "..."
        rf"-b\s+'{sq_content}'",  # -b '...'
    ]

    for pattern in patterns:
        match = re.search(pattern, command)
        if match:
            return match.group(1)

    return None


def get_pr_body_from_api(pr_number: str | None) -> str | None:
    """Get PR body from GitHub API.

    Args:
        pr_number: PR number, or None to use current branch's PR

    Returns:
        PR body content, or None if failed
    """
    try:
        cmd = ["gh", "pr", "view"]
        if pr_number:
            cmd.append(pr_number)
        cmd.extend(["--json", "body", "--jq", ".body"])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        # gh command failure is not critical - we'll warn and skip quality check
        pass
    return None


def format_block_message(missing: list[str], is_merge: bool = False) -> str:
    """Format the block message for missing items."""
    context = "マージ" if is_merge else "作成"

    message = f"PRボディに必須項目がありません（{context}をブロック）\n\n"
    message += "**不足している項目:**\n"
    for item in missing:
        message += f"- {item}\n"

    message += "\n**PRボディの推奨フォーマット:**\n"
    message += "```markdown\n"
    message += "## なぜ\n"
    message += "この変更が必要になった背景・動機を記述\n"
    message += "\n"
    message += "## 何を\n"
    message += "変更内容の概要\n"
    message += "\n"
    message += "Closes #XXX\n"
    message += "```\n"

    if is_merge:
        message += "\n**対処方法:**\n"
        message += '1. `gh pr edit <PR番号> --body "..."` でPRボディを更新\n'
        message += "2. または GitHub Web UI でPRを編集\n"
    else:
        message += "\n**対処方法:**\n"
        message += "`--body` オプションに上記の項目を含めてください\n"

    return message


def main():
    """
    PreToolUse hook for Bash commands.

    Blocks `gh pr create` and `gh pr merge` if PR body lacks required sections.
    """
    result = {"decision": "approve"}

    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        if is_gh_pr_create_command(command):
            # Check PR body at creation time
            if has_body_file_option(command):
                # Skip if using --body-file or -F (can't check file content)
                result["systemMessage"] = (
                    "⚠️ pr-body-quality-check: -F/--body-file使用時はボディ品質を確認できません。"
                    "「なぜ」セクションと参照を含めてください。"
                )
            else:
                body = extract_pr_body(command)
                if body is not None:
                    is_valid, missing = check_body_quality(body)
                    if is_valid:
                        result["systemMessage"] = "✅ pr-body-quality-check: 必須項目OK"
                    else:
                        reason = format_block_message(missing, is_merge=False)
                        result = make_block_result("pr-body-quality-check", reason)
                else:
                    # No body specified - will be entered interactively
                    result["systemMessage"] = (
                        "⚠️ pr-body-quality-check: --body未指定のため品質チェック不可。"
                        "対話入力時に「なぜ」セクションと参照を含めてください。"
                    )

        elif is_gh_pr_merge_command(command):
            # Check PR body before merge
            pr_number = extract_pr_number_from_merge(command)
            body = get_pr_body_from_api(pr_number)

            if body is None:
                # Failed to get PR body - approve but warn
                result["systemMessage"] = (
                    "⚠️ pr-body-quality-check: PRボディの取得に失敗しました。"
                    "品質チェックをスキップします。"
                )
            else:
                is_valid, missing = check_body_quality(body)
                if not is_valid:
                    reason = format_block_message(missing, is_merge=True)
                    result = make_block_result("pr-body-quality-check", reason)
                else:
                    # Issue #2608: Check for incremental migration keywords
                    incremental_valid, incremental_reason = check_incremental_pr(body)
                    if not incremental_valid:
                        result = make_block_result("pr-body-quality-check", incremental_reason)
                    else:
                        result["systemMessage"] = "✅ pr-body-quality-check: マージ前品質チェックOK"

    except Exception as e:
        print(f"[pr-body-quality-check] Hook error: {e}", file=sys.stderr)
        result = {"decision": "approve"}

    # Log only for non-block decisions (make_block_result() logs automatically)
    if result.get("decision") != "block":
        log_hook_execution(
            "pr-body-quality-check",
            result.get("decision", "approve"),
            result.get("reason"),
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
