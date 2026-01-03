#!/usr/bin/env python3
"""マージ前の安全性チェックを強制する。

Why:
    AIレビュー（Copilot/Codex）を確認せずにマージすると、品質問題を
    見逃す可能性がある。また、--auto/--adminオプションや、未解決の
    レビュースレッド、未完了の受け入れ基準があるままマージすると
    品質管理が形骸化する。

What:
    - gh pr merge --auto/--adminをブロック
    - REST APIマージをブロック（フックバイパス防止）
    - AIレビュー進行中/エラー状態でのマージをブロック
    - 未解決レビュースレッド、未検証の修正主張をブロック
    - 未完了の受け入れ基準を持つIssueのCloseをブロック
    - --dry-runモードでマージ前チェックが可能

Remarks:
    - 複数のブロック理由を一度に収集・表示（Issue #874）
    - モジュール分割: ai_review_checker, issue_checker, review_checker等
    - 後方互換性のため既存テストで使用される関数を__all__で再エクスポート

Changelog:
    - silenvx/dekita#263: AIレビューエラー検出追加
    - silenvx/dekita#457: 修正主張の検証チェック追加
    - silenvx/dekita#598: Issue受け入れ基準チェック追加
    - silenvx/dekita#858: 数値主張の検証チェック追加
    - silenvx/dekita#874: ブロック理由の一括収集・表示
    - silenvx/dekita#892: --dry-runモード追加
    - silenvx/dekita#1130: バグ別Issue化の警告追加
    - silenvx/dekita#1379: REST APIマージブロック追加
    - silenvx/dekita#2347: マージコミット背景リマインダー追加
    - silenvx/dekita#2377: --adminブロック時の詳細ステータス表示
    - silenvx/dekita#2384: --body内の誤検知防止
"""

import argparse
import json
import re
import sys

# Re-exports for backward compatibility (existing tests import from this module)
# flake8: noqa: F401
from ai_review_checker import (
    check_ai_review_error,
    check_ai_reviewing,
    request_copilot_review,
)
from check_utils import (
    CODE_BLOCK_PATTERN,
    ISSUE_REFERENCE_PATTERN,
    get_repo_owner_and_name,
    strip_code_blocks,
    truncate_body,
)
from fix_verification_checker import (
    EXPLICIT_NOT_VERIFIED_PATTERN,
    FIX_CLAIM_KEYWORDS,
    NUMERIC_CLAIM_PATTERN,
    NUMERIC_VERIFICATION_PATTERN,
    VERIFICATION_NEGATION_PATTERN,
    VERIFICATION_POSITIVE_PATTERN,
    FixClaimKeyword,
    check_numeric_claims_verified,
    check_resolved_without_verification,
    has_valid_verification,
    is_specific_fix_claim,
)
from issue_checker import (
    BUG_ISSUE_TITLE_KEYWORDS,
    ISSUE_CREATION_PATTERN,
    _collect_issue_refs_from_review,
    _is_bug_issue,
    _references_pr,
    check_bug_issue_from_review,
    check_excluded_criteria_without_followup,
    check_incomplete_acceptance_criteria,
    extract_issue_numbers_from_commits,
    extract_issue_numbers_from_pr_body,
    fetch_issue_acceptance_criteria,
    get_pr_body,
)
from lib.execution import log_hook_execution
from lib.github import extract_pr_number, get_pr_merge_status, is_pr_merged
from lib.results import make_approve_result, make_block_result
from lib.session import parse_hook_input
from lib.strings import split_command_chain, strip_quoted_strings
from merge_conditions import BlockingReason, run_all_pr_checks
from review_checker import (
    check_dismissal_without_issue,
    check_resolved_without_response,
    check_unresolved_ai_threads,
)

# Explicit __all__ to document intentional re-exports for backward compatibility.
# Existing tests import from this module, so these must remain available.
__all__ = [
    # From ai_review_checker
    "check_ai_review_error",
    "check_ai_reviewing",
    "request_copilot_review",
    # From check_utils
    "CODE_BLOCK_PATTERN",
    "ISSUE_REFERENCE_PATTERN",
    "get_repo_owner_and_name",
    "strip_code_blocks",
    "truncate_body",
    # From fix_verification_checker
    "EXPLICIT_NOT_VERIFIED_PATTERN",
    "FIX_CLAIM_KEYWORDS",
    "NUMERIC_CLAIM_PATTERN",
    "NUMERIC_VERIFICATION_PATTERN",
    "VERIFICATION_NEGATION_PATTERN",
    "VERIFICATION_POSITIVE_PATTERN",
    "FixClaimKeyword",
    "check_numeric_claims_verified",
    "check_resolved_without_verification",
    "has_valid_verification",
    "is_specific_fix_claim",
    # From issue_checker
    "BUG_ISSUE_TITLE_KEYWORDS",
    "ISSUE_CREATION_PATTERN",
    "_collect_issue_refs_from_review",
    "_is_bug_issue",
    "_references_pr",
    "check_bug_issue_from_review",
    "check_excluded_criteria_without_followup",
    "check_incomplete_acceptance_criteria",
    "extract_issue_numbers_from_commits",
    "extract_issue_numbers_from_pr_body",
    "fetch_issue_acceptance_criteria",
    "get_pr_body",
    # From merge_conditions
    "BlockingReason",
    "run_all_pr_checks",
    # From review_checker
    "check_dismissal_without_issue",
    "check_resolved_without_response",
    "check_unresolved_ai_threads",
    # From common
    "is_pr_merged",
]


def dry_run_check(pr_number: int) -> int:
    """Run all merge checks and report issues without blocking (Issue #892).

    This mode allows checking merge readiness before attempting to merge,
    preventing multiple failed merge attempts.

    Args:
        pr_number: The PR number to check.

    Returns:
        0 if no issues found (merge ready), 1 if issues found, 2 if error occurred.
    """
    print(f"[DRY-RUN] PR #{pr_number} のマージ前チェックを実行中...")
    print()

    try:
        blocking_reasons, warnings = run_all_pr_checks(str(pr_number), dry_run=True)

        # Display warnings first (Issue #630)
        for warning in warnings:
            print(warning, file=sys.stderr)

        if blocking_reasons:
            print(f"⚠️  {len(blocking_reasons)}件の問題が見つかりました:")
            print()
            separator = "=" * 60

            for i, br in enumerate(blocking_reasons, 1):
                print(f"【問題 {i}/{len(blocking_reasons)}】{br.title}")
                print(br.details)
                if i < len(blocking_reasons):
                    print(separator)
                print()

            print(f"全{len(blocking_reasons)}件の問題を解決後、マージを実行してください。")
            return 1
        else:
            print(f"✅ PR #{pr_number} はマージ可能です")
            return 0

    except Exception as e:
        print(f"❌ チェック中にエラーが発生しました: {e}", file=sys.stderr)
        return 2


def strip_option_values(cmd: str) -> str:
    """Strip values of options that may contain text like '--admin' or '--auto'.

    Supported options (space or equals-separated):
    - --body, -b: PR body text
    - --subject, -t: PR subject line (gh pr merge)
    - -m: Message (git commit style)

    Issue #2384: Prevents false positives from --body "The '--admin' option".
    Copilot review: Support --body= syntax and -b shorthand.

    Args:
        cmd: The command string.

    Returns:
        Command with option values replaced by empty quotes.
    """
    result = cmd
    # Support both space-separated and equals-separated forms
    # --body "x", --body="x", -b "x", -b="x"
    result = re.sub(r'(--body(?:\s+|=))"[^"]*"', r'\1""', result)
    result = re.sub(r"(--body(?:\s+|=))'[^']*'", r"\1''", result)
    result = re.sub(r'(-b(?:\s+|=))"[^"]*"', r'\1""', result)
    result = re.sub(r"(-b(?:\s+|=))'[^']*'", r"\1''", result)
    # --subject "x", --subject="x", -t "x", -t="x"
    result = re.sub(r'(--subject(?:\s+|=))"[^"]*"', r'\1""', result)
    result = re.sub(r"(--subject(?:\s+|=))'[^']*'", r"\1''", result)
    result = re.sub(r'(-t(?:\s+|=))"[^"]*"', r'\1""', result)
    result = re.sub(r"(-t(?:\s+|=))'[^']*'", r"\1''", result)
    # -m "x", -m="x" (message option)
    result = re.sub(r'(-m(?:\s+|=))"[^"]*"', r'\1""', result)
    result = re.sub(r"(-m(?:\s+|=))'[^']*'", r"\1''", result)
    return result


def main():
    """
    PreToolUse hook for Bash commands.

    Checks:
    1. Blocks `gh pr merge --auto` pattern
    2. Blocks `gh pr merge --admin` pattern
    3. Blocks merge if Copilot/Codex is in requested_reviewers
    4. Blocks merge if AI review encountered an error (Issue #263)
    5. Blocks merge if review comments were dismissed without Issue reference
    6. Blocks merge if review threads were resolved without Claude Code response
    7. Blocks merge if fix claims lack verification (Issue #457)
    8. Blocks merge if AI review threads are still unresolved
    9. Blocks merge if numeric claims lack verification (Issue #858)
    10. Blocks merge if Closes target Issues have incomplete acceptance criteria

    Issue #874: All blocking reasons are collected and displayed at once,
    instead of early-exiting on the first failure.

    Issue #892: Added --dry-run mode for pre-merge checking.
    """
    # Handle command-line arguments for dry-run mode (Issue #892)
    parser = argparse.ArgumentParser(
        description="Merge safety check hook",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check merge readiness without blocking (returns exit code 0=ready, 1=issues, 2=error)",
    )
    parser.add_argument(
        "pr_number",
        nargs="?",
        type=int,
        help="PR number (required for --dry-run mode)",
    )

    args = parser.parse_args()

    # Dry-run mode: check without blocking
    if args.dry_run:
        if not args.pr_number:
            print("Error: PR number is required for --dry-run mode", file=sys.stderr)
            print("Usage: merge-check.py --dry-run <pr_number>", file=sys.stderr)
            sys.exit(2)
        sys.exit(dry_run_check(args.pr_number))

    # Hook mode: read from stdin (original behavior)
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Check if command contains a real gh pr merge invocation
        # Split by shell operators (&&, ||, ;) and check each part
        # This avoids false positives from strings in --body while still catching
        # chained commands like "cd repo && gh pr merge --auto"
        #
        # Known limitation: Commands with prefixes like "sudo gh pr merge" or
        # "FOO=1 gh pr merge" are not detected. This is acceptable because:
        # 1. This project doesn't use sudo for gh commands
        # 2. Environment variable assignments are rare in this workflow
        # 3. Users intentionally using such patterns can bypass hooks anyway
        def contains_merge_command(cmd: str) -> bool:
            """Check if any part of the command is a gh pr merge invocation.

            Issue #1392: Strip quoted content before splitting to avoid false positives
            from operators inside quoted strings like --body "note; gh api ..."
            """
            # Strip quoted content to avoid false positives from operators inside quotes
            stripped = strip_quoted_strings(cmd)
            # Split by common shell operators
            parts = split_command_chain(stripped)
            for part in parts:
                # Check if this part starts with gh pr merge (after optional whitespace)
                if re.match(r"^\s*gh\s+pr\s+merge\b", part):
                    return True
            return False

        is_merge_command = contains_merge_command(command)

        # Issue #1379: Check for REST API merge (bypasses all hooks)
        # Pattern: gh api repos/:owner/:repo/pulls/123/merge or similar
        # Uses same split approach as contains_merge_command to avoid false positives
        # from --body or other arguments containing the pattern
        def contains_rest_api_merge(cmd: str) -> bool:
            """Check if any part of the command is a REST API merge invocation.

            Issue #1392: Strip quoted content before splitting to avoid false positives
            from operators inside quoted strings like --body "note; gh api ..."

            Codex review: Also check for quoted paths like gh api "pulls/123/merge"
            which would be stripped before pattern matching.
            """
            # Strip quoted content to avoid false positives from operators inside quotes
            stripped = strip_quoted_strings(cmd)
            # Split by common shell operators
            parts = split_command_chain(stripped)
            # Match /merge followed by space, hyphen (for -X), slash, or end of string
            # to avoid false positives like /merge-request or /merges
            # Supports both with and without leading slash: /repos/... or repos/...
            merge_path_pattern = r"(?:/?repos/[^/]+/[^/]+/)?pulls/\d+/merge(?:\s|$|[-/])"

            # Check unquoted paths in stripped parts
            for part in parts:
                # Check if this part starts with gh api and contains pulls/.../merge
                if re.match(r"^\s*gh\s+api\s+", part) and re.search(merge_path_pattern, part):
                    return True

            # Check for quoted paths: gh api "pulls/123/merge" or gh api 'pulls/123/merge'
            # These are stripped before the pattern match above, so check original command
            quoted_path_pattern = r'gh\s+api\s+["\']' + r"(?:/?repos/[^/]+/[^/]+/)?pulls/\d+/merge"
            if re.search(quoted_path_pattern, cmd):
                # Verify this gh api is a real command (not inside another command's quotes)
                if re.search(r"\bgh\s+api\b", stripped):
                    return True

            return False

        if contains_rest_api_merge(command):
            reason = (
                "[merge-check] REST APIによるマージは禁止されています（Issue #1379）。\n\n"
                "理由: REST APIマージはフックをバイパスし、レビューチェックをスキップします。\n\n"
                "代わりに以下のコマンドを使用してください:\n"
                "  gh pr merge {PR番号} --squash\n\n"
                "rate limit時は待機してから再試行してください。"
            )
            log_hook_execution("merge-check", "block", "REST API merge blocked")
            result = make_block_result("merge-check", reason)
            print(json.dumps(result))
            sys.exit(0)

        # Not a merge command, log and skip silently (no output per design principle)
        if not is_merge_command:
            log_hook_execution("merge-check", "skip", "Not a merge command")
            sys.exit(0)

        # Check 1: Block auto-merge (only for actual merge commands)
        # This is an immediate block - command syntax issue, not PR state
        # Issue #2384: Strip option values to avoid false positives from --body text
        # Also check for quoted options like "--auto" to prevent bypass (Codex review)
        # Copilot review: Use strip_option_values to handle nested quotes in --body
        stripped_command = strip_quoted_strings(command)
        # Strip --body/--title values before checking for quoted options
        command_without_body = strip_option_values(command)
        quoted_auto = (
            re.search(r"""(?:^|\s)(?:"--auto"|'--auto')(?:\s|$)""", command_without_body)
            is not None
        )
        has_auto = "--auto" in stripped_command or quoted_auto
        if is_merge_command and has_auto:
            reason = (
                "auto-mergeは使用しないでください。\n"
                "Copilot/Codexレビューを確認してから手動でマージしてください:\n"
                "1. gh api repos/:owner/:repo/pulls/{PR番号} "
                "--jq '.requested_reviewers[].login' で進行中確認\n"
                "2. gh api repos/:owner/:repo/pulls/{PR番号}/reviews "
                "でレビュー確認\n"
                "3. gh pr merge {PR番号} --squash で手動マージ"
            )
            log_hook_execution("merge-check", "block", "--auto option blocked")
            result = make_block_result("merge-check", reason)
            print(json.dumps(result))
            sys.exit(0)

        # Check 2: Block admin merge (bypasses branch protection, only for actual merge commands)
        # Issue #2377: Show detailed PR status and suggested actions
        # Issue #2384: Use stripped_command to avoid false positives from --body text
        # Also check for quoted options like "--admin" to prevent bypass (Codex review)
        # Copilot review: Use strip_option_values to handle nested quotes in --body
        quoted_admin = (
            re.search(r"""(?:^|\s)(?:"--admin"|'--admin')(?:\s|$)""", command_without_body)
            is not None
        )
        has_admin = "--admin" in stripped_command or quoted_admin
        if is_merge_command and has_admin:
            pr_number = extract_pr_number(command)
            reason_parts = [
                "--adminオプションは使用しないでください。",
                "ブランチ保護ルールを迂回するマージは禁止されています。",
                "",
            ]

            # Get PR status for detailed guidance
            if pr_number:
                status = get_pr_merge_status(pr_number)

                # Show current status
                # Issue #2377: Show user-friendly messages for UNKNOWN status
                reason_parts.append(f"📋 PR #{pr_number} の現在の状態:")

                raw_ci_status = status.get("status_check_status") or "UNKNOWN"
                ci_status_emoji = {
                    "SUCCESS": "✅",
                    "FAILURE": "❌",
                    "PENDING": "⏳",
                    "NONE": "➖",
                    "UNKNOWN": "❓",
                }.get(raw_ci_status, "❓")
                ci_status_text = (
                    "取得失敗（GitHub APIエラーの可能性）"
                    if raw_ci_status == "UNKNOWN"
                    else raw_ci_status
                )
                reason_parts.append(f"  - CI: {ci_status_emoji} {ci_status_text}")

                raw_review = status.get("review_decision") or ""
                review_emoji = "✅" if raw_review == "APPROVED" else "❌"
                review_text = "取得失敗" if raw_review == "UNKNOWN" else (raw_review or "未承認")
                reason_parts.append(f"  - レビュー承認: {review_emoji} {review_text}")

                raw_merge_state = status.get("merge_state_status") or "UNKNOWN"
                merge_state_text = "取得失敗" if raw_merge_state == "UNKNOWN" else raw_merge_state
                reason_parts.append(f"  - マージ状態: {merge_state_text}")
                reason_parts.append("")

                # Show blocking reasons if any
                if status["blocking_reasons"]:
                    reason_parts.append("⚠️ ブロック理由:")
                    for br in status["blocking_reasons"]:
                        reason_parts.append(f"  - {br}")
                    reason_parts.append("")

                # Show suggested actions
                if status["suggested_actions"]:
                    reason_parts.append("🔧 解決方法:")
                    for i, action in enumerate(status["suggested_actions"], 1):
                        reason_parts.append(f"  {i}. {action}")
                    reason_parts.append("")

                # If no specific blocking reasons detected, show generic guidance
                if not status["blocking_reasons"]:
                    reason_parts.append("マージがブロックされている場合は、原因を確認してください:")
                    reason_parts.append("1. CIが失敗していないか確認")
                    reason_parts.append("2. 未解決のレビュースレッドがないか確認")
                    reason_parts.append("3. 必要な承認が得られているか確認")
                    reason_parts.append("")
            else:
                reason_parts.append("マージがブロックされている場合は、原因を確認してください:")
                reason_parts.append("1. CIが失敗していないか確認")
                reason_parts.append("2. 未解決のレビュースレッドがないか確認")
                reason_parts.append("3. 必要な承認が得られているか確認")
                reason_parts.append("")

            reason_parts.append("問題を解決してから、通常のマージを実行してください:")
            reason_parts.append(f"gh pr merge {pr_number or '{PR番号}'} --squash")

            reason = "\n".join(reason_parts)
            log_hook_execution("merge-check", "block", "--admin option blocked")
            result = make_block_result("merge-check", reason)
            print(json.dumps(result))
            sys.exit(0)

        # Collect all blocking reasons for PR state checks (Issue #874)
        blocking_reasons: list[BlockingReason] = []
        all_warnings: list[str] = []

        # Check 3-9: If merge command with PR number, run all PR checks
        if is_merge_command and re.search(r"\d+", command):
            pr_number = extract_pr_number(command)
            if pr_number:
                reasons, warnings = run_all_pr_checks(pr_number)
                blocking_reasons.extend(reasons)
                all_warnings.extend(warnings)

        # Log warnings (non-blocking but should be visible) (Issue #630)
        for warning in all_warnings:
            print(warning, file=sys.stderr)
            log_hook_execution("merge-check", "warning", warning)

        # If there are blocking reasons, display all of them at once (Issue #874)
        if blocking_reasons:
            pr_number_str = extract_pr_number(command) or "?"
            header = f"マージがブロックされました（PR #{pr_number_str}）。以下の問題を解決してください:\n"
            separator = "\n" + "=" * 60 + "\n"

            # Build combined reason message
            reason_parts = [header]
            for i, br in enumerate(blocking_reasons, 1):
                reason_parts.append(f"\n【問題 {i}/{len(blocking_reasons)}】{br.title}\n")
                reason_parts.append(f"{br.details}")
                if i < len(blocking_reasons):
                    reason_parts.append(separator)

            reason_parts.append(
                f"\n\n全{len(blocking_reasons)}件の問題を解決後、再度マージを実行してください。"
            )
            combined_reason = "".join(reason_parts)

            result = make_block_result("merge-check", combined_reason)
            # Log all check names that failed
            log_hook_execution(
                "merge-check",
                "block",
                f"Blocked by: {', '.join(br.check_name for br in blocking_reasons)}",
            )
            print(json.dumps(result))
            sys.exit(0)

        # All checks passed - remind about commit message background (Issue #2347)
        # Only show reminder when PR number is present (actual merge command)
        if is_merge_command and re.search(r"\d+", command):
            reminder_message = "\n".join(
                [
                    "[REMINDER] マージコミットに背景（Why）を含めてください。",
                    '例: gh pr merge {PR番号} --squash --body "背景: ..."',
                    "詳細: development-workflow Skill の「コミットメッセージ規約」参照",
                ]
            )
            print(reminder_message, file=sys.stderr)
        log_hook_execution("merge-check", "approve", "All checks passed")
        sys.exit(0)

    except Exception as e:
        # On error, approve to avoid blocking
        error_msg = f"Hook error: {e}"
        print(f"[merge-check] {error_msg}", file=sys.stderr)
        result = make_approve_result("merge-check", error_msg)
        # Log the error explicitly since make_approve_result doesn't set "reason"
        log_hook_execution("merge-check", "approve", error_msg)
        print(json.dumps(result))
        sys.exit(0)


if __name__ == "__main__":
    main()
