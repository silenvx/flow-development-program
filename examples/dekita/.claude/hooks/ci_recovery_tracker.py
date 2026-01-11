#!/usr/bin/env python3
"""CI失敗から復旧までの時間を追跡する。

Why:
    CI失敗の復旧時間を計測することで、チームの対応速度を可視化し、
    改善の指標として活用できる。

What:
    - CI失敗時にタイムスタンプを記録
    - CI成功時に復旧時間を計算してログ記録
    - 復旧時間をsystemMessageで表示

State:
    - writes: {TMPDIR}/claude-hooks/ci-recovery.json
    - writes: .claude/logs/metrics/ci-recovery-metrics.log

Remarks:
    - 記録型フック（ブロックしない、メトリクス記録）
    - PostToolUse:Bashで発火（gh pr checks結果を分析）
    - ブランチ別に失敗タイムスタンプを保持

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from common import METRICS_LOG_DIR
from lib.execution import log_hook_execution
from lib.git import get_current_branch
from lib.hook_input import get_tool_result
from lib.results import print_continue_and_log_skip
from lib.session import create_hook_context, get_session_id, parse_hook_input

# Tracking file location (use TMPDIR for sandbox compatibility)
TRACKING_DIR = Path(tempfile.gettempdir()) / "claude-hooks"
CI_TRACKING_FILE = TRACKING_DIR / "ci-recovery.json"

# Persistent log for analysis
CI_RECOVERY_LOG = METRICS_LOG_DIR / "ci-recovery-metrics.log"

# Patterns to detect CI commands
CI_CHECK_PATTERNS = [
    r"gh pr checks",
    r"gh run view",
    r"gh run watch",
    r"gh run list",
]

# Patterns to detect CI status in output
CI_FAILURE_PATTERNS = [
    r"FAILURE",
    r"fail",
    r"failing",
    r"❌",
    r"X\s+\w+",  # X followed by check name
]

CI_SUCCESS_PATTERNS = [
    r"SUCCESS",
    r"pass",
    r"✓",
    r"✅",
    r"All checks have passed",
]


def load_ci_tracking() -> dict:
    """Load CI tracking data."""
    if CI_TRACKING_FILE.exists():
        try:
            return json.loads(CI_TRACKING_FILE.read_text())
        except Exception:
            pass  # Best effort - corrupted tracking data is ignored
    return {"failure_time": None, "branch": None, "pr_number": None}


def save_ci_tracking(data: dict) -> None:
    """Save CI tracking data."""
    TRACKING_DIR.mkdir(parents=True, exist_ok=True)
    CI_TRACKING_FILE.write_text(json.dumps(data, indent=2))


def log_ci_recovery(
    failure_time: str,
    recovery_time: str,
    recovery_seconds: float,
    branch: str | None,
    pr_number: str | None,
) -> None:
    """Log CI recovery event for later analysis."""
    try:
        METRICS_LOG_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": recovery_time,
            "session_id": get_session_id(),
            "type": "ci_recovery",
            "failure_time": failure_time,
            "recovery_time": recovery_time,
            "recovery_seconds": recovery_seconds,
            "branch": branch,
            "pr_number": pr_number,
        }
        with open(CI_RECOVERY_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # ログ書き込み失敗はサイレントに無視（メトリクスは必須ではない）


def log_ci_failure(branch: str | None, pr_number: str | None) -> None:
    """Log CI failure event."""
    try:
        METRICS_LOG_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": get_session_id(),
            "type": "ci_failure",
            "branch": branch,
            "pr_number": pr_number,
        }
        with open(CI_RECOVERY_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # ログ書き込み失敗はサイレントに無視（メトリクスは必須ではない）


def is_ci_check_command(command: str) -> bool:
    """Check if the command is a CI status check."""
    for pattern in CI_CHECK_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False


def extract_ci_target_number(command: str) -> str | None:
    """Extract PR number or run ID from CI command if present.

    Note: This is a specialized function for CI commands that extracts
    either a PR number (from `gh pr checks/view`) or a run ID (from `gh run view/watch`).
    For general gh pr command PR number extraction, use common.extract_pr_number().
    """
    match = re.search(r"(?:pr\s+(?:checks|view)|run\s+(?:view|watch))\s+(\d+)", command)
    if match:
        return match.group(1)
    return None


def detect_ci_status(output: str) -> str | None:
    """Detect CI status from output.

    Returns: "failure", "success", or None if unknown
    """
    # Check for failure patterns first (they're more definitive)
    for pattern in CI_FAILURE_PATTERNS:
        if re.search(pattern, output, re.IGNORECASE):
            return "failure"

    # Check for success patterns
    for pattern in CI_SUCCESS_PATTERNS:
        if re.search(pattern, output, re.IGNORECASE):
            return "success"

    return None


def main():
    """PostToolUse hook for Bash commands.

    Tracks CI failure and recovery times.
    """
    result = {"continue": True}

    try:
        input_data = parse_hook_input()
        # Issue #2607: Create context for session_id logging
        ctx = create_hook_context(input_data)
        tool_input = input_data.get("tool_input", {})
        tool_result = get_tool_result(input_data) or {}

        command = tool_input.get("command", "")
        stdout = tool_result.get("stdout", "")
        stderr = tool_result.get("stderr", "")
        output = f"{stdout}\n{stderr}"

        # Only process CI check commands
        if not is_ci_check_command(command):
            print_continue_and_log_skip("ci-recovery-tracker", "not a CI check command", ctx=ctx)
            return

        now = datetime.now(UTC)
        branch = get_current_branch()
        pr_number = extract_ci_target_number(command)
        ci_status = detect_ci_status(output)

        tracking = load_ci_tracking()

        if ci_status == "failure":
            # Record failure if not already tracking one, or if branch changed
            # (branch change means the old tracking is stale)
            if tracking["failure_time"] is None or tracking["branch"] != branch:
                tracking["failure_time"] = now.isoformat()
                tracking["branch"] = branch
                tracking["pr_number"] = pr_number
                save_ci_tracking(tracking)
                log_ci_failure(branch, pr_number)

        elif ci_status == "success":
            # Calculate recovery time if we were tracking a failure
            # Only count recovery if same branch (to avoid cross-PR false positives)
            if tracking["failure_time"] is not None and tracking["branch"] == branch:
                failure_time = datetime.fromisoformat(tracking["failure_time"])
                recovery_seconds = (now - failure_time).total_seconds()

                log_ci_recovery(
                    tracking["failure_time"],
                    now.isoformat(),
                    recovery_seconds,
                    tracking["branch"],
                    tracking["pr_number"],
                )

                # Format message
                if recovery_seconds < 60:
                    time_str = f"{recovery_seconds:.0f}秒"
                elif recovery_seconds < 3600:
                    time_str = f"{recovery_seconds / 60:.1f}分"
                else:
                    time_str = f"{recovery_seconds / 3600:.1f}時間"

                result["systemMessage"] = f"📊 CI復旧時間: {time_str}"

                # Clear tracking
                tracking = {"failure_time": None, "branch": None, "pr_number": None}
                save_ci_tracking(tracking)

    except Exception:
        # フック実行の失敗でClaude Codeをブロックしない
        pass

    log_hook_execution(
        "ci-recovery-tracker",
        "approve",
        details={"type": "ci_tracked"},
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
