#!/usr/bin/env python3
"""ci-monitor.pyを使用すべきコマンドをブロックする。

Why:
    gh pr checks --watch や手動ポーリングは冗長なログでコンテキストを消費し、
    BEHIND検知や自動リベースなどの機能がない。ci-monitor.pyを使用すべき。

What:
    - gh pr checks --watchをブロック
    - gh run watchをブロック（冗長ログ）
    - 手動PR状態チェック（gh api /pulls/xxx）をブロック
    - 手動ポーリング（sleep && gh ...）をブロック
    - ci-monitor.pyの使用を案内

Remarks:
    - ブロック型フック（非推奨コマンドはブロック）
    - PreToolUse:Bashで発火
    - コメント/メッセージ内のパターンは除外（引用符内は検査しない）

Changelog:
    - silenvx/dekita#1008: コメント内容の誤検知防止
    - silenvx/dekita#1508: Noneハンドリング
    - silenvx/dekita#2052: gh issue close/comment対応
    - silenvx/dekita#2062: git commit -m対応
"""

import json
import re
import sys

from lib.execution import log_hook_execution
from lib.results import make_approve_result, make_block_result
from lib.session import parse_hook_input

# Patterns for manual PR state check commands that should use ci-monitor.py instead.
# Each command type has two patterns because PR number can appear either:
#   - Before flags: `gh pr view 123 --json mergeStateStatus`
#   - After flags: `gh pr view --json mergeStateStatus 123`
#
# Note: The gh api pattern uses negative lookahead (?!...) to allow endpoints like:
#   - /pulls/{PR}/comments - for review comments (read/write)
#   - /pulls/{PR}/reviews - for reviews
#   - /pulls/{PR}/requested_reviewers - for reviewer info
MANUAL_CHECK_PATTERNS = [
    # gh pr view with --json mergeStateStatus
    r"gh\s+pr\s+view\s+(?:.*?\s+)?(\d+)\s+.*?--json\s+mergeStateStatus",
    r"gh\s+pr\s+view\s+--json\s+mergeStateStatus\s+(?:.*?\s+)?(\d+)",
    # gh api /repos/.../pulls/... (direct PR access only, not sub-endpoints)
    # Negative lookahead excludes /comments, /reviews, /requested_reviewers
    # The /? after (\d+) allows matching trailing slashes like /pulls/123/
    r"gh\s+api\s+/repos/[^/]+/[^/]+/pulls/(\d+)/?(?!comments|reviews|requested_reviewers)(?:\s|$)",
    # gh pr view with --json reviews
    r"gh\s+pr\s+view\s+(?:.*?\s+)?(\d+)\s+.*?--json\s+reviews",
    r"gh\s+pr\s+view\s+--json\s+reviews\s+(?:.*?\s+)?(\d+)",
    # gh pr view with --json requested_reviewers
    r"gh\s+pr\s+view\s+(?:.*?\s+)?(\d+)\s+.*?--json\s+requested_reviewers",
    r"gh\s+pr\s+view\s+--json\s+requested_reviewers\s+(?:.*?\s+)?(\d+)",
]


def get_pr_number_from_checks(command: str) -> str | None:
    """Extract PR number from gh pr checks command.

    Handles two patterns:
    - gh pr checks 123 --watch
    - gh pr checks --watch 123

    Examples:
        >>> get_pr_number_from_checks("gh pr checks 123 --watch")
        '123'
        >>> get_pr_number_from_checks("gh pr checks --watch 456")
        '456'
        >>> get_pr_number_from_checks("gh pr checks --watch")
        None
    """
    # Pattern 1: PR number comes immediately after 'checks'
    match = re.search(r"gh\s+pr\s+checks\s+(\d+)", command)
    if match:
        return match.group(1)
    # Pattern 2: flags come before the PR number
    match = re.search(r"gh\s+pr\s+checks\s+(?:--\w+\s+)+(\d+)", command)
    if match:
        return match.group(1)
    return None


def detect_manual_pr_check(command: str) -> tuple[bool, str | None]:
    """Detect manual PR state check commands.

    Detects commands that manually check PR state instead of using ci-monitor.py:
    - gh pr view with --json mergeStateStatus/reviews/requested_reviewers
    - gh api /repos/.../pulls/{PR} (direct PR access)

    Returns:
        Tuple of (is_manual_check, pr_number).

    Examples:
        >>> detect_manual_pr_check("gh pr view 123 --json mergeStateStatus")
        (True, '123')
        >>> detect_manual_pr_check("gh api /repos/owner/repo/pulls/456")
        (True, '456')
        >>> detect_manual_pr_check("gh pr view 123")
        (False, None)
    """
    for pattern in MANUAL_CHECK_PATTERNS:
        match = re.search(pattern, command)
        if match:
            return True, match.group(1)
    return False, None


def extract_pr_number_from_command(command: str) -> str | None:
    """Extract PR number from a command string.

    Looks for common patterns like:
    - gh pr view 123
    - gh pr checks 123
    - gh api repos/.../pulls/123

    Returns None if no PR number found.
    """
    # Pattern: gh pr <subcommand> <PR number>
    match = re.search(r"gh\s+pr\s+\w+\s+(\d+)", command)
    if match:
        return match.group(1)
    # Pattern: gh api .../pulls/<PR number>
    match = re.search(r"gh\s+api\s+.*?/pulls/(\d+)", command)
    if match:
        return match.group(1)
    return None


def detect_manual_polling(command: str) -> tuple[bool, str | None]:
    """Detect manual polling patterns that indicate workaround usage.

    Patterns detected:
    - sleep X && gh ...
    - sleep X; gh ...
    - while ... do ... sleep ... gh ... (both single-line and multiline forms)

    These patterns suggest using manual workarounds instead of proper tools
    like ci-monitor.py. This is a code smell that should trigger investigation.

    Returns:
        Tuple of (is_manual_polling, pr_number).

    Examples:
        >>> detect_manual_polling("sleep 30 && gh api repos/owner/repo/pulls/123")
        (True, '123')
        >>> detect_manual_polling("sleep 30; gh pr view 456")
        (True, '456')
        >>> detect_manual_polling("while true; do sleep 10; gh api ...; done")
        (True, None)
        >>> detect_manual_polling("sleep 10")
        (False, None)
        >>> detect_manual_polling("gh pr list")
        (False, None)
    """
    # Pattern: sleep followed by gh command (chained with && or ;)
    # Using explicit (&&|;) instead of [;&]+ for clarity and precision
    if re.search(r"sleep\s+\d+\s*(&&|;)\s*gh\s+", command):
        return True, extract_pr_number_from_command(command)
    # Pattern: while loop with sleep and gh (requires do keyword)
    # Handles both single-line (while ...; do) and multiline (while ...\ndo) forms
    if re.search(r"while\s+.*\bdo\b.*sleep\s+.*gh\s+", command, re.DOTALL):
        return True, extract_pr_number_from_command(command)
    return False, None


def strip_quoted_content(command: str) -> str:
    r"""Remove content inside quotes to avoid false positives in chain detection.

    This allows us to check for command chaining without being confused by
    quoted text in --body or --title that might contain && or ; characters.

    Handles:
    - Double-quoted strings with escaped quotes
    - Single-quoted strings with escaped quotes
    - Unclosed quotes (treats rest of string as quoted)
    - Mixed quotes (single inside double is fine)
    - Escaped quotes outside strings (not treated as delimiters)

    Examples:
        `cmd --title "He said \"hello\""` → double-quoted with escapes
        `cmd --title 'It\'s working'` → single-quoted with escapes
        `cmd --title "it's a test"` → mixed quotes
        `cmd \"foo\" && other` → backslash-quote preserved
    """
    result = []
    i = 0
    while i < len(command):
        char = command[i]
        if char == "\\" and i + 1 < len(command):
            # Escaped character outside quotes - preserve both chars
            result.append(command[i])
            result.append(command[i + 1])
            i += 2
        elif char in ('"', "'"):
            quote_char = char
            result.append(quote_char)
            i += 1
            # Consume until matching unescaped quote or end of string
            while i < len(command):
                if command[i] == "\\" and i + 1 < len(command):
                    # Skip escaped character
                    i += 2
                elif command[i] == quote_char:
                    result.append(quote_char)
                    i += 1
                    break
                else:
                    i += 1
            # If we reached end without closing quote, that's fine (unclosed quote)
        else:
            result.append(char)
            i += 1
    return "".join(result)


def is_command_with_comment_content(command: str) -> bool:
    """Check if command has comment/body content that should not be inspected.

    These commands may contain arbitrary text in --body/--title/--comment/-m that should not
    be inspected for blocked patterns. For example:
    - gh issue create --body "Detected gh pr checks --watch pattern"
    - gh issue close --comment "Fixed the gh pr checks --watch issue"
    - gh pr comment --body "This uses ci-monitor.py instead of gh pr checks"
    - git commit -m "fix: remove gh pr checks --watch usage"

    However, if the command is chained with other gh commands using && or ;,
    we should NOT early approve to avoid bypassing blocked patterns:
    - gh pr create ... && gh pr checks --watch  <- should be blocked

    Returns True only if the command is a standalone command with comment content.

    Issue #2052: Extended to cover gh issue close/comment, gh pr comment/review/close.
    Issue #2062: Extended to cover git commit -m (commit messages).
    """
    # Commands that may contain arbitrary text in --body/--title/--comment/-m
    # Single combined pattern for efficiency (avoids multiple re.search calls)
    # Matches:
    #   - gh issue create/close/comment
    #   - gh pr create/comment/review/close
    #   - git commit with message (-m, --message, -am)
    # Note: Using .*? (non-greedy) to prevent ReDoS vulnerability
    comment_command_pattern = (
        r"(gh\s+(issue\s+(create|close|comment)\b|pr\s+(create|comment|review|close)\b)"
        r"|git\s+commit\s+.*?(--message\b|-a?m\b))"
    )

    # Check if it matches the combined command pattern
    if not re.search(comment_command_pattern, command):
        return False

    # Strip quoted content to avoid false positives from text in --body/--title/--comment
    # e.g., --body "sleep 30 && gh api ..." should not trigger chain detection
    stripped = strip_quoted_content(command)

    # If command contains chained gh commands, don't early approve
    # This prevents bypassing blocks like:
    #   - gh pr create && gh pr checks --watch (gh after operator)
    #   - gh pr checks --watch && git commit -m "msg" (gh before operator)
    # Operators detected: && (and), || (or), ; (sequential), | (pipe)
    # Pattern checks both: gh command before operator OR gh command after operator
    if re.search(r"(gh\s+.*?(&&|\|\||[;|])|(&&|\|\||[;|])\s*gh\s+)", stripped):
        return False

    return True


def main():
    """Entry point for the CI wait check hook."""
    try:
        input_json = parse_hook_input()
        # Issue #1508: tool_inputやcommandがNoneの場合のハンドリング
        # dict.get()はキーが存在して値がNoneの場合、デフォルト値を使わずNoneを返す
        tool_input = input_json.get("tool_input") or {}
        command = tool_input.get("command") or ""

        # Early approve: commands with comment/body content (standalone only)
        # These may contain blocked patterns in --body/--title/--comment text (Issue #1008, #2052)
        # Note: Chained commands like "gh pr create && gh pr checks --watch" are NOT approved
        if is_command_with_comment_content(command):
            result = make_approve_result("ci-wait-check")
            log_hook_execution("ci-wait-check", "approve", "command with comment content")
            print(json.dumps(result))
            return

        # Block: gh pr checks [PR] --watch
        if re.search(r"gh\s+pr\s+checks\s+.*--watch", command):
            pr_number = get_pr_number_from_checks(command)
            pr_display = pr_number or "{PR番号}"
            reason = (
                f"gh pr checks --watch は使用禁止です。\n"
                f"ci-monitor.py を使用してください:\n\n"
                f'python3 "$CLAUDE_PROJECT_DIR"/.claude/scripts/ci-monitor.py {pr_display} '
                f"--session-id <SESSION_ID>\n\n"
                f"※ <SESSION_ID>はUserPromptSubmit hookで提供されるセッションID\n\n"
                f"ci-monitor.py は以下を自動処理します:\n"
                f"  - BEHIND検知→自動リベース\n"
                f"  - レビュー完了検知→コメント取得\n"
                f"  - CI失敗→即座に通知"
            )
            result = make_block_result("ci-wait-check", reason)
            log_hook_execution("ci-wait-check", "block", reason, {"command": command[:100]})
            print(json.dumps(result))
            return

        # Block: gh run watch (verbose output, use ci-monitor.py instead)
        # Strip quoted content to avoid false positives on commands like:
        # gh pr comment -b "please avoid gh run watch"
        if re.search(r"gh\s+run\s+watch\b", strip_quoted_content(command)):
            reason = (
                "gh run watch は使用禁止です（ログが冗長）。\n\n"
                "【PR関連のCI監視の場合】\n"
                "ci-monitor.py を使用してください:\n"
                '  python3 "$CLAUDE_PROJECT_DIR"/.claude/scripts/ci-monitor.py {PR番号} '
                "--session-id <SESSION_ID>\n\n"
                "※ <SESSION_ID>はUserPromptSubmit hookで提供されるセッションID\n\n"
                "【PR不要のワークフロー監視の場合】\n"
                "(例: workflow_dispatch, 手動トリガー)\n"
                "gh run view を繰り返し使用してください:\n"
                "  gh run view {run_id} --json status,conclusion\n\n"
                "gh run watch を使うと大量のログが出力され、\n"
                "コンテキストを消費します。"
            )
            result = make_block_result("ci-wait-check", reason)
            log_hook_execution("ci-wait-check", "block", reason, {"command": command[:100]})
            print(json.dumps(result))
            return

        # Block: Manual PR state check commands
        is_manual_check, pr_number = detect_manual_pr_check(command)
        if is_manual_check:
            pr_display = pr_number or "{PR番号}"
            reason = (
                f"手動のPR状態チェックは使用禁止です。\n"
                f"ci-monitor.py を使用してください:\n\n"
                f'python3 "$CLAUDE_PROJECT_DIR"/.claude/scripts/ci-monitor.py {pr_display} '
                f"--session-id <SESSION_ID>\n\n"
                f"※ <SESSION_ID>はUserPromptSubmit hookで提供されるセッションID\n\n"
                f"ci-monitor.py は以下を自動処理します:\n"
                f"  - マージ状態チェック（BEHIND/DIRTY検知）\n"
                f"  - レビュー完了検知→コメント自動取得\n"
                f"  - CI完了待機→結果通知"
            )
            result = make_block_result("ci-wait-check", reason)
            log_hook_execution("ci-wait-check", "block", reason, {"command": command[:100]})
            print(json.dumps(result))
            return

        # Block: Manual polling patterns (workaround detection)
        is_manual_polling, pr_number = detect_manual_polling(command)
        if is_manual_polling:
            pr_display = pr_number or "{PR番号}"
            reason = (
                "手動ポーリングパターン（sleep + gh）を検出しました。\n\n"
                "【PR関連のCI監視の場合】\n"
                "ci-monitor.py を使用してください:\n"
                f'  python3 "$CLAUDE_PROJECT_DIR"/.claude/scripts/ci-monitor.py {pr_display} '
                f"--session-id <SESSION_ID>\n\n"
                f"※ <SESSION_ID>はUserPromptSubmit hookで提供されるセッションID\n\n"
                "【PR不要のワークフロー監視の場合】\n"
                "(例: workflow_dispatch, 手動トリガー)\n"
                "sleepなしで gh run view を繰り返し使用してください:\n"
                "  gh run view {run_id} --json status,conclusion\n\n"
                "手動ポーリング（sleep + gh）はコンテキストを消費します。"
            )
            result = make_block_result("ci-wait-check", reason)
            log_hook_execution("ci-wait-check", "block", reason, {"command": command[:100]})
            print(json.dumps(result))
            return

        # All other commands: approve
        result = make_approve_result("ci-wait-check")
        log_hook_execution("ci-wait-check", "approve")
        print(json.dumps(result))

    except Exception as e:
        # On error, approve to avoid blocking legitimate commands
        print(f"[ci-wait-check] Hook error: {e}", file=sys.stderr)
        result = make_approve_result("ci-wait-check", f"Hook error: {e}")
        log_hook_execution("ci-wait-check", "approve", f"Hook error: {e}")
        print(json.dumps(result))


if __name__ == "__main__":
    main()
