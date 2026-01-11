#!/usr/bin/env python3
"""CI E2E失敗後のローカルテスト実行を強制する。

Why:
    CI E2Eテストが失敗した状態でプッシュを繰り返すと、CI負荷増加と
    開発効率低下を招く。ローカルでテストを通すことで、高品質な
    コードのみがプッシュされる。

What:
    - git pushコマンドを検出
    - E2Eテストファイル（tests/**/*.spec.ts）の変更を確認
    - CI失敗記録があり、ローカルテスト成功記録がない場合はブロック

State:
    reads: .claude/state/markers/ci-e2e-failure-{branch}.log
    reads: .claude/state/markers/e2e-test-{branch}.done

Remarks:
    - ローカルテスト結果は30分間有効
    - CI失敗記録は4時間で自動的に無効化される
"""

import json
import re
import subprocess
import sys
import time

from common import MARKERS_LOG_DIR
from lib.execution import log_hook_execution
from lib.git import get_current_branch
from lib.results import make_approve_result, make_block_result
from lib.session import parse_hook_input
from lib.strings import sanitize_branch_name, strip_quoted_strings

# E2E test results are valid for 30 minutes
E2E_RESULT_VALIDITY_SECONDS = 30 * 60
# CI failure is considered stale after 4 hours (new CI run likely)
CI_FAILURE_STALE_SECONDS = 4 * 60 * 60


def is_git_push_command(command: str) -> bool:
    """Check if command is a git push command."""
    if not command.strip():
        return False

    stripped_command = strip_quoted_strings(command)

    if not re.search(r"git\s+push\b", stripped_command):
        return False

    if re.search(r"--help", stripped_command):
        return False

    return True


def get_changed_files() -> list[str]:
    """Get list of files changed between HEAD and main branch."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "main...HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip().split("\n") if result.stdout.strip() else []
    except subprocess.CalledProcessError:
        return []


def has_e2e_test_changes(changed_files: list[str]) -> bool:
    """Check if any E2E test files were changed."""
    for file in changed_files:
        if file.startswith("tests/") and file.endswith(".spec.ts"):
            return True
    return False


def get_changed_e2e_files(changed_files: list[str]) -> list[str]:
    """Get list of changed E2E test files."""
    return [f for f in changed_files if f.startswith("tests/") and f.endswith(".spec.ts")]


def check_ci_e2e_failure(branch: str) -> tuple[bool, float | None]:
    """Check if CI E2E tests have failed recently for this branch.

    Returns:
        Tuple of (has_failure, timestamp).
        has_failure: True if there's a recent CI failure.
        timestamp: Unix timestamp of the failure (if any).
    """
    safe_branch = sanitize_branch_name(branch)
    log_file = MARKERS_LOG_DIR / f"ci-e2e-failure-{safe_branch}.log"

    if not log_file.exists():
        return False, None

    try:
        content = log_file.read_text().strip()
        # Format: branch:timestamp
        parts = content.split(":")
        if len(parts) >= 2:
            timestamp = float(parts[1])
            current_time = time.time()
            # Consider failure stale after CI_FAILURE_STALE_SECONDS
            is_recent = (current_time - timestamp) < CI_FAILURE_STALE_SECONDS
            return is_recent, timestamp
    except (ValueError, IndexError):
        pass  # Silent ignore: malformed log files don't block push

    return False, None


def record_ci_e2e_failure(branch: str) -> None:
    """Record that CI E2E tests failed for this branch."""
    safe_branch = sanitize_branch_name(branch)
    log_file = MARKERS_LOG_DIR / f"ci-e2e-failure-{safe_branch}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    timestamp = time.time()
    log_file.write_text(f"{branch}:{timestamp}")


def clear_ci_e2e_failure(branch: str) -> None:
    """Clear CI E2E failure record after local tests pass."""
    safe_branch = sanitize_branch_name(branch)
    log_file = MARKERS_LOG_DIR / f"ci-e2e-failure-{safe_branch}.log"
    if log_file.exists():
        log_file.unlink()


def check_local_e2e_test_pass(branch: str) -> tuple[bool, float | None]:
    """Check if E2E tests passed locally recently for this branch.

    Returns:
        Tuple of (has_pass, timestamp).
        has_pass: True if tests passed within validity period.
        timestamp: Unix timestamp of last test run (if any).
    """
    safe_branch = sanitize_branch_name(branch)
    log_file = MARKERS_LOG_DIR / f"e2e-test-{safe_branch}.done"

    if not log_file.exists():
        return False, None

    try:
        content = log_file.read_text().strip()
        # Format: branch:commit:timestamp:result
        parts = content.split(":")
        if len(parts) >= 4:
            timestamp = float(parts[2])
            result = parts[3]
            current_time = time.time()
            is_valid = (current_time - timestamp) < E2E_RESULT_VALIDITY_SECONDS
            is_pass = result == "pass"
            return is_valid and is_pass, timestamp
    except (ValueError, IndexError):
        pass  # Silent ignore: malformed log files don't block push

    return False, None


def main():
    """
    PreToolUse hook for Bash commands.

    Blocks `git push` if CI E2E tests failed and local tests haven't passed.
    """
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        # Only check git push commands
        if not is_git_push_command(command):
            result = make_approve_result("e2e-test-check")
            print(json.dumps(result))
            sys.exit(0)

        branch = get_current_branch()

        # Skip check for main/master branches
        if branch in ("main", "master", None):
            result = make_approve_result("e2e-test-check")
            print(json.dumps(result))
            sys.exit(0)

        # Check if E2E test files were changed
        changed_files = get_changed_files()
        if not has_e2e_test_changes(changed_files):
            result = make_approve_result("e2e-test-check")
            print(json.dumps(result))
            sys.exit(0)

        # Check if CI has failed recently
        has_ci_failure, ci_failure_time = check_ci_e2e_failure(branch)

        if not has_ci_failure:
            # No recent CI failure - allow push (CI will verify)
            result = make_approve_result("e2e-test-check")
            print(json.dumps(result))
            sys.exit(0)

        # CI has failed - require local test pass AFTER the CI failure
        has_local_pass, local_pass_time = check_local_e2e_test_pass(branch)

        if has_local_pass and local_pass_time is not None and ci_failure_time is not None:
            # Only accept local pass if it occurred AFTER the CI failure
            if local_pass_time > ci_failure_time:
                # Local tests passed after CI failure - clear failure record and allow push
                clear_ci_e2e_failure(branch)
                result = make_approve_result("e2e-test-check")
                print(json.dumps(result))
                sys.exit(0)

        # CI failed and no local pass - block
        changed_e2e = get_changed_e2e_files(changed_files)
        files_list = "\n".join(f"  - {f}" for f in changed_e2e[:5])
        if len(changed_e2e) > 5:
            files_list += f"\n  ... and {len(changed_e2e) - 5} more"

        reason = (
            f"CI E2Eテストが失敗しています。ローカルでテストを通してからプッシュしてください。\n\n"
            f"変更されたテストファイル:\n{files_list}\n\n"
            f"以下のコマンドでローカルテストを実行してください:\n\n"
            "```bash\n"
            "npm run test:e2e:chromium -- tests/stories/\n"
            "```\n\n"
            "テスト成功後、再度プッシュしてください。\n"
            "（ローカルテストの結果は30分間有効です）"
        )
        result = make_block_result("e2e-test-check", reason)
        log_hook_execution("e2e-test-check", "block", reason)
        print(json.dumps(result))
        sys.exit(0)

    except Exception as e:
        print(f"[e2e-test-check] Hook error: {e}", file=sys.stderr)
        result = make_approve_result("e2e-test-check", f"Hook error: {e}")

    log_hook_execution("e2e-test-check", result.get("decision", "approve"))
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
