#!/usr/bin/env python3
# - 責務: VITE_プレフィックスのシークレット更新を記録し、Stopフックで確認を促す
# - 重複なし: 他のフックにはシークレット更新追跡機能なし
# - 記録型: 更新されたシークレットをファイルに記録、Stopフックで確認
# - AGENTS.md: 広告ID更新時の教訓から実装
"""
PostToolUse hook to track frontend secret updates.

When `gh secret set VITE_*` is executed successfully, this hook:
1. Records the secret name to a tracking file
2. The Stop hook will check if deploy was performed

This ensures frontend secrets are deployed without interrupting workflow.
"""

import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from lib.execution import log_hook_execution
from lib.hook_input import get_tool_result
from lib.results import print_continue_and_log_skip
from lib.session import create_hook_context, parse_hook_input

# Only track frontend secrets (VITE_ prefix)
FRONTEND_SECRET_PREFIX = "VITE_"

# Tracking file location
TRACKING_FILE = Path(tempfile.gettempdir()) / "claude-secret-updates.json"


def load_tracking_data() -> dict:
    """Load existing tracking data."""
    if TRACKING_FILE.exists():
        try:
            return json.loads(TRACKING_FILE.read_text())
        except Exception:
            # Ignore corrupted/invalid JSON - start fresh
            pass
    return {"secrets": [], "updated_at": None}


def save_tracking_data(data: dict) -> None:
    """Save tracking data."""
    TRACKING_FILE.write_text(json.dumps(data, indent=2))


def main():
    """PostToolUse hook for Bash commands.

    Tracks VITE_* secret updates for later deploy verification.
    """
    result = {"continue": True}

    try:
        # Read input from stdin
        input_data = parse_hook_input()
        # Issue #2607: Create context for session_id logging
        ctx = create_hook_context(input_data)
        tool_input = input_data.get("tool_input", {})
        tool_result = get_tool_result(input_data) or {}

        command = tool_input.get("command", "")
        # Default to 0 (success) if exit_code not provided
        # Issue #1470: Previous default of -1 caused trigger to be skipped for successful commands
        exit_code = tool_result.get("exit_code", tool_result.get("exitCode", 0))

        # Only process successful gh secret set commands
        if "gh secret set" not in command or exit_code != 0:
            print_continue_and_log_skip(
                "secret-deploy-trigger", "not gh secret set or failed", ctx=ctx
            )
            return

        # Extract secret name from command (handle flags before secret name)
        # Pattern matches: gh secret set [--flags] SECRET_NAME
        match = re.search(r"gh secret set\s+(?:--\S+\s+)*([A-Z_][A-Z0-9_]*)", command)
        if not match:
            print_continue_and_log_skip(
                "secret-deploy-trigger", "secret name not found in command", ctx=ctx
            )
            return

        secret_name = match.group(1)

        # Only track frontend secrets
        if not secret_name.startswith(FRONTEND_SECRET_PREFIX):
            print_continue_and_log_skip(
                "secret-deploy-trigger", f"not a frontend secret: {secret_name}", ctx=ctx
            )
            return

        # Record the secret update with timestamp
        data = load_tracking_data()
        if secret_name not in data["secrets"]:
            data["secrets"].append(secret_name)
        # Always update timestamp to latest secret update
        data["updated_at"] = datetime.now(UTC).isoformat()
        save_tracking_data(data)

        # Brief notification (not blocking)
        result["systemMessage"] = (
            f"📝 フロントエンドシークレット '{secret_name}' を記録しました。"
            f"作業完了時にデプロイを確認します。"
        )

    except Exception:
        pass  # Best effort - tracking update may fail

    log_hook_execution("secret-deploy-trigger", "approve")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
