#!/usr/bin/env python3
"""セッション開始時にGitの状態を確認し、未コミット変更を警告する。

Why:
    mainブランチに未コミット変更がある状態でセッションを開始すると、
    意図しない変更の混入や競合が発生するリスクがある。

What:
    - Gitワーキングツリーの状態を確認
    - mainブランチに未コミット変更がある場合に警告
    - featureブランチの未コミット変更は情報として表示

Remarks:
    - ブロックはしない（警告のみ）
    - stop_hook_active時はスキップ
"""

import json
import subprocess
import sys

from lib.constants import TIMEOUT_LIGHT
from lib.execution import log_hook_execution
from lib.session import parse_hook_input


def get_git_status() -> tuple[bool, str, str]:
    """
    Check git status and branch.
    Returns (is_clean, branch_name, status_output).
    """
    try:
        # Get current branch
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        branch = branch_result.stdout.strip()

        # Get git status
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        status_output = status_result.stdout.strip()
        is_clean = len(status_output) == 0

        return is_clean, branch, status_output
    except subprocess.TimeoutExpired:
        return True, "", "timeout"
    except Exception as e:
        return True, "", str(e)


def main():
    """
    Entry point for the git status check hook.

    Reads a JSON object from stdin with the following format:
        {
            "transcript_path": "<path to transcript file>",
            "stop_hook_active": <bool, optional>
        }

    Outputs a JSON object to stdout with the following format:
        {
            "decision": "approve",
            "reason": "<explanation>",
            "systemMessage": "<message to display>"
        }
    """
    result = None
    try:
        input_json = parse_hook_input()

        # If stop_hook_active is set, approve immediately
        if input_json.get("stop_hook_active"):
            result = {
                "ok": True,
                "decision": "approve",
                "reason": "stop_hook_active is set; skipping git status check.",
            }
        else:
            is_clean, branch, status_output = get_git_status()

            if is_clean:
                result = {
                    "ok": True,
                    "decision": "approve",
                    "reason": f"Git working tree is clean on branch '{branch}'",
                    "systemMessage": f"✅ git-status-check: クリーン ({branch})",
                }
            elif branch == "main":
                # Warning: uncommitted changes on main branch
                result = {
                    "ok": True,
                    "decision": "approve",  # Don't block, just warn
                    "reason": f"Uncommitted changes on main branch:\n{status_output}",
                    "systemMessage": f"⚠️ git-status-check: mainブランチに未コミット変更があります\n{status_output}",
                }
            else:
                # Changes on feature branch - OK
                result = {
                    "ok": True,
                    "decision": "approve",
                    "reason": f"Uncommitted changes on branch '{branch}' (not main, OK)",
                    "systemMessage": f"ℹ️ git-status-check: 未コミット変更あり ({branch})",
                }

    except Exception as e:
        # On error, approve to avoid blocking
        print(f"[git-status-check] Hook error: {e}", file=sys.stderr)
        result = {"ok": True, "decision": "approve", "reason": f"Hook error: {e}"}

    log_hook_execution("git-status-check", result.get("decision", "approve"), result.get("reason"))
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
