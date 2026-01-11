#!/usr/bin/env python3
"""E2Eテスト実行結果を記録する。

Why:
    e2e-test-check.pyがプッシュ前にローカルテスト実行を検証するため、
    テスト結果の記録が必要。結果を永続化することで、プッシュ時の
    判定が可能になる。

What:
    - npm run test:e2e等のコマンド完了を検出
    - 終了コードと出力からテスト結果（pass/fail）を判定
    - ブランチ・コミット情報とともに結果を記録

State:
    - writes: .claude/state/markers/e2e-test-{branch}.done

Remarks:
    - 記録型フック（ブロックしない、マーカーファイル書き込み）
    - PostToolUse:Bashで発火（npm/pnpm test:e2e、npx playwrightコマンド）
    - e2e-test-check.pyと連携（マーカーファイル参照元）
    - exit_codeまたは出力パターンでpass/failを判定

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import re
import sys
import time

from common import MARKERS_LOG_DIR
from lib.execution import log_hook_execution
from lib.git import get_current_branch, get_head_commit
from lib.hook_input import get_tool_result
from lib.session import parse_hook_input
from lib.strings import sanitize_branch_name


def is_e2e_test_command(command: str) -> bool:
    """Check if command is an E2E test command."""
    if not command.strip():
        return False

    # Match npm run test:e2e commands (including test:e2e:chromium, test:e2e:firefox, etc.)
    if re.search(r"npm\s+run\s+test:e2e", command):
        return True

    # Match pnpm test:e2e commands (both "pnpm test:e2e" and "pnpm run test:e2e")
    if re.search(r"pnpm\s+(run\s+)?test:e2e", command):
        return True

    # Match npx playwright test commands
    if re.search(r"npx\s+playwright\s+test", command):
        return True

    return False


def record_e2e_test_run(branch: str, commit: str, passed: bool) -> None:
    """Record that E2E tests were run."""
    safe_branch = sanitize_branch_name(branch)
    log_file = MARKERS_LOG_DIR / f"e2e-test-{safe_branch}.done"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    timestamp = time.time()
    result = "pass" if passed else "fail"
    log_file.write_text(f"{branch}:{commit}:{timestamp}:{result}")


def main():
    """
    PostToolUse hook for Bash commands.

    Records E2E test execution results for later verification.
    """
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        tool_result = get_tool_result(data) or {}
        command = tool_input.get("command", "")

        # Only process E2E test commands
        if not is_e2e_test_command(command):
            sys.exit(0)

        # Check if tests passed based on exit code and output
        stdout = tool_result.get("stdout", "")
        stderr = tool_result.get("stderr", "")
        exit_code = tool_result.get("exit_code", None)

        # Determine if tests passed
        # Primary: check exit code (0 = success)
        if exit_code is not None:
            passed = exit_code == 0
        else:
            # Fallback: check output patterns
            # Playwright shows "X passed" and "X failed" - look for non-zero failures
            # Look for "N failed" where N > 0
            failure_match = re.search(r"(\d+)\s+failed", stdout.lower())
            has_failures = failure_match and int(failure_match.group(1)) > 0

            # Check for explicit error indicators
            has_errors = "error:" in stderr.lower() or "FAILED" in stdout

            passed = not has_failures and not has_errors

        branch = get_current_branch()
        commit = get_head_commit()

        if branch and commit:
            record_e2e_test_run(branch, commit, passed)
            status = "pass" if passed else "fail"
            log_hook_execution(
                "e2e-test-recorder",
                "record",
                f"Recorded E2E test result: {status} for {branch}@{commit[:7]}",
            )

    except Exception as e:
        # Don't block on errors, just log
        print(f"[e2e-test-recorder] Hook error: {e}", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
