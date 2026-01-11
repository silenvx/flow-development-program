#!/usr/bin/env python3
# - 責務: セッション終了時に未デプロイのフロントエンドシークレットがないか確認
# - 重複なし: secret-deploy-trigger.pyと連携、追跡データを参照
# - ブロック型: 未デプロイのシークレットがあればブロック
# - AGENTS.md: 広告ID更新時の教訓から実装
"""
Stop hook to verify frontend secrets have been deployed.

Checks the tracking file created by secret-deploy-trigger.py and:
1. If secrets were updated but not deployed, blocks with instructions
2. If deployed or no secrets updated, approves

This ensures frontend secret updates are always deployed before session ends.
"""

import json
import subprocess
import tempfile
from pathlib import Path

from lib.constants import TIMEOUT_HEAVY
from lib.execution import log_hook_execution
from lib.results import make_block_result
from lib.session import parse_hook_input
from lib.timestamp import parse_iso_timestamp

# Tracking file location (shared with secret-deploy-trigger.py)
TRACKING_FILE = Path(tempfile.gettempdir()) / "claude-secret-updates.json"

# CUSTOMIZE: Production URL for verification - Set this to your project's production URL
PRODUCTION_URL = "https://dekita.app"


def load_tracking_data() -> dict:
    """Load tracking data."""
    if TRACKING_FILE.exists():
        try:
            return json.loads(TRACKING_FILE.read_text())
        except Exception:
            # Ignore corrupted/invalid JSON - start fresh
            pass
    return {"secrets": [], "updated_at": None}


def clear_tracking_data() -> None:
    """Clear tracking data after successful deploy."""
    if TRACKING_FILE.exists():
        TRACKING_FILE.unlink()


def check_deploy_after_update(updated_at: str | None) -> bool:
    """Check if a successful deploy was triggered AFTER the secret update.

    Args:
        updated_at: ISO format timestamp of when secrets were updated.

    Returns:
        True if ANY successful CI run on main branch completed after the secret update.
    """
    if not updated_at:
        return False

    try:
        update_time = parse_iso_timestamp(updated_at)
        if update_time is None:
            return False

        # Get recent CI runs on main branch (not just the latest)
        result = subprocess.run(
            [
                "gh",
                "run",
                "list",
                "--workflow",
                "ci.yml",
                "--branch",
                "main",
                "--limit",
                "10",
                "--json",
                "createdAt,conclusion",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_HEAVY,
        )
        if result.returncode == 0 and result.stdout.strip():
            runs = json.loads(result.stdout.strip())

            # Check if ANY successful run occurred after the secret update
            for run_data in runs:
                created_at = run_data.get("createdAt", "")
                conclusion = run_data.get("conclusion", "")

                if created_at and conclusion == "success":
                    run_time = parse_iso_timestamp(created_at)
                    if run_time and run_time > update_time:
                        return True
    except Exception:
        # gh command failed or JSON parse error - assume not deployed
        pass
    return False


def main():
    """Stop hook to verify frontend secrets deployment."""
    try:
        # Read context from stdin
        input_data = parse_hook_input()

        # Prevent infinite loops
        if input_data.get("stop_hook_active"):
            log_hook_execution("secret-deploy-check", "approve", "stop_hook_active")
            print(json.dumps({"ok": True, "decision": "approve"}))
            return

        # Load tracking data
        data = load_tracking_data()
        secrets = data.get("secrets", [])
        updated_at = data.get("updated_at")

        # No secrets updated, nothing to check
        if not secrets:
            log_hook_execution("secret-deploy-check", "approve")
            print(json.dumps({"ok": True, "decision": "approve"}))
            return

        # Check if deploy was performed AFTER the secret update
        if check_deploy_after_update(updated_at):
            clear_tracking_data()
            log_hook_execution(
                "secret-deploy-check", "approve", None, {"secrets": secrets, "deployed": True}
            )
            print(
                json.dumps(
                    {
                        "ok": True,
                        "decision": "approve",
                        "systemMessage": f"✅ フロントエンドシークレットがデプロイされました: {', '.join(secrets)}",
                    }
                )
            )
            return

        # Secrets updated but not deployed - block
        secrets_list = ", ".join(secrets)
        reason = f"""フロントエンドシークレットが更新されましたが、デプロイされていません。

更新されたシークレット: {secrets_list}

デプロイを実行してください:
```bash
gh workflow run ci.yml --ref main
python3 .claude/scripts/ci_monitor.py main
```

または update_secret.py を使用（デプロイと検証を自動実行）:
```bash
python3 .claude/scripts/update_secret.py <SECRET_NAME> <VALUE>
```

本番確認URL: {PRODUCTION_URL}"""
        log_hook_execution("secret-deploy-check", "block", reason, {"secrets": secrets})
        result = make_block_result("secret-deploy-check", reason)
        result["ok"] = False
        print(json.dumps(result))

    except Exception:
        # On error, don't block
        log_hook_execution("secret-deploy-check", "approve", "Hook error")
        print(json.dumps({"ok": True, "decision": "approve"}))


if __name__ == "__main__":
    main()
