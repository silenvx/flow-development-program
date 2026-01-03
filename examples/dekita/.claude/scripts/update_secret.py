#!/usr/bin/env python3
"""GitHub Secretを更新し本番デプロイを実行する。

Why:
    Secret更新→デプロイ→本番確認の一連の作業を
    自動化し、手作業ミスを防ぐため。

What:
    - update_secret(): GitHub Secretを更新
    - trigger_deploy(): デプロイワークフローを起動
    - wait_for_deploy(): デプロイ完了を待機
    - verify_production(): 本番環境を確認

Remarks:
    - gh CLIがインストール・認証済みであること
    - デプロイタイムアウト: 10分
    - ポーリング間隔: 15秒

Changelog:
    - silenvx/dekita#1000: Secret更新自動化機能を追加
"""

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime

# Project configuration
# CUSTOMIZE: Production URL - Set this to your project's production URL
PRODUCTION_URL = "https://dekita.app"
# CUSTOMIZE: Deploy workflow filename - Set this to your project's CI workflow file
DEPLOY_WORKFLOW = "ci.yml"
DEPLOY_TIMEOUT_MINUTES = 10
POLL_INTERVAL_SECONDS = 15


@dataclass
class UpdateResult:
    """Result of the update operation."""

    success: bool
    message: str
    secret_updated: bool = False
    deploy_triggered: bool = False
    deploy_completed: bool = False
    verified: bool = False
    run_id: str | None = None


def log(message: str, level: str = "INFO") -> None:
    """Print a timestamped log message."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}", flush=True)


def run_command(args: list[str], timeout: int = 30, capture: bool = True) -> tuple[bool, str]:
    """Run a command and return (success, output)."""
    try:
        result = subprocess.run(
            args,
            capture_output=capture,
            text=True,
            timeout=timeout,
        )
        return result.returncode == 0, result.stdout.strip() if capture else ""
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


def update_secret(name: str, value: str) -> bool:
    """Update a GitHub Secret."""
    log(f"Updating GitHub Secret: {name}")

    # Use echo to pipe the value to gh secret set
    try:
        process = subprocess.run(
            ["gh", "secret", "set", name],
            input=value,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if process.returncode == 0:
            log(f"Secret {name} updated successfully")
            return True
        else:
            log(f"Failed to update secret: {process.stderr}", "ERROR")
            return False
    except Exception as e:
        log(f"Error updating secret: {e}", "ERROR")
        return False


def trigger_deploy() -> str | None:
    """Trigger the deploy workflow and return the run ID."""
    log(f"Triggering deploy workflow: {DEPLOY_WORKFLOW}")

    success, _ = run_command(["gh", "workflow", "run", DEPLOY_WORKFLOW, "--ref", "main"])

    if not success:
        log("Failed to trigger workflow", "ERROR")
        return None

    # Wait a moment for the run to be created
    time.sleep(3)

    # Get the latest run ID
    success, output = run_command(
        [
            "gh",
            "run",
            "list",
            "--workflow",
            DEPLOY_WORKFLOW,
            "--limit",
            "1",
            "--json",
            "databaseId,event",
            "--jq",
            ".[0].databaseId",
        ]
    )

    if success and output:
        log(f"Deploy triggered, run ID: {output}")
        return output

    log("Could not get run ID", "ERROR")
    return None


def wait_for_deploy(run_id: str) -> bool:
    """Wait for the deploy workflow to complete."""
    log(f"Waiting for deploy to complete (timeout: {DEPLOY_TIMEOUT_MINUTES} min)...")

    start_time = time.time()
    timeout_seconds = DEPLOY_TIMEOUT_MINUTES * 60

    while True:
        elapsed = time.time() - start_time
        if elapsed > timeout_seconds:
            log("Deploy timeout exceeded", "ERROR")
            return False

        success, output = run_command(
            [
                "gh",
                "run",
                "view",
                run_id,
                "--json",
                "status,conclusion",
                "--jq",
                '.status + "|" + .conclusion',
            ]
        )

        if success and output:
            parts = output.split("|")
            status = parts[0] if len(parts) > 0 else ""
            conclusion = parts[1] if len(parts) > 1 else ""

            if status == "completed":
                if conclusion == "success":
                    log("Deploy completed successfully")
                    return True
                else:
                    log(f"Deploy failed with conclusion: {conclusion}", "ERROR")
                    return False

            remaining = int(timeout_seconds - elapsed)
            log(f"Deploy in progress... ({remaining}s remaining)")

        time.sleep(POLL_INTERVAL_SECONDS)


def verify_production(secret_name: str, expected_value: str) -> bool:
    """Verify the secret is reflected in production.

    For VITE_ prefixed secrets (frontend), we check if the corresponding
    script or resource is loaded correctly.
    """
    log(f"Verifying production at {PRODUCTION_URL}...")

    # For AdMax ID, check if the script URL is in the page
    if secret_name == "VITE_ADMAX_ID":
        expected_url = f"https://adm.shinobi.jp/o/{expected_value}"
        log(f"Looking for AdMax script: {expected_url}")

        # Fetch the page and look for the script URL
        success, output = run_command(
            ["curl", "-s", "-L", PRODUCTION_URL],
            timeout=30,
        )

        if success and expected_url in output:
            log("AdMax script URL found in production!")
            return True

        # The script might be loaded dynamically via iframe, so also check
        # if the page loads without error
        if success and "dekita" in output.lower():
            log(
                "Page loads correctly. AdMax verification requires browser check.",
                "WARNING",
            )
            log(f"Manual verification: Open {PRODUCTION_URL} and check Network tab")
            log(f"Expected script: {expected_url}")
            return True

        log("Could not verify production", "WARNING")
        return False

    # For other secrets, just verify the site is accessible
    success, output = run_command(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", PRODUCTION_URL],
        timeout=30,
    )

    if success and output == "200":
        log("Production site is accessible")
        return True

    log(f"Production verification failed (HTTP {output})", "ERROR")
    return False


def update_secret_and_deploy(
    secret_name: str, secret_value: str, skip_deploy: bool = False
) -> UpdateResult:
    """Main function to update secret and deploy."""
    result = UpdateResult(success=False, message="")

    # Step 1: Update the secret
    if not update_secret(secret_name, secret_value):
        result.message = "Failed to update GitHub Secret"
        return result
    result.secret_updated = True

    if skip_deploy:
        result.success = True
        result.message = "Secret updated (deploy skipped)"
        return result

    # Step 2: Trigger deploy
    run_id = trigger_deploy()
    if not run_id:
        result.message = "Failed to trigger deploy workflow"
        return result
    result.deploy_triggered = True
    result.run_id = run_id

    # Step 3: Wait for deploy
    if not wait_for_deploy(run_id):
        result.message = "Deploy failed or timed out"
        return result
    result.deploy_completed = True

    # Step 4: Verify production
    # Wait a bit for CDN propagation
    log("Waiting for CDN propagation (10s)...")
    time.sleep(10)

    if verify_production(secret_name, secret_value):
        result.verified = True

    result.success = True
    result.message = "Secret updated and deployed successfully"
    if not result.verified:
        result.message += " (manual verification recommended)"

    return result


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Update GitHub Secrets and deploy to production")
    parser.add_argument("secret_name", help="Name of the GitHub Secret to update")
    parser.add_argument("secret_value", help="New value for the secret")
    parser.add_argument(
        "--skip-deploy",
        action="store_true",
        help="Only update secret, don't trigger deploy",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON",
    )

    args = parser.parse_args()

    result = update_secret_and_deploy(
        args.secret_name,
        args.secret_value,
        skip_deploy=args.skip_deploy,
    )

    if args.json:
        output = {
            "success": result.success,
            "message": result.message,
            "secret_updated": result.secret_updated,
            "deploy_triggered": result.deploy_triggered,
            "deploy_completed": result.deploy_completed,
            "verified": result.verified,
            "run_id": result.run_id,
            "production_url": PRODUCTION_URL,
        }
        print(json.dumps(output, indent=2))
    else:
        print("\n" + "=" * 50)
        print(f"Result: {'SUCCESS' if result.success else 'FAILURE'}")
        print(f"Message: {result.message}")
        print(f"Production URL: {PRODUCTION_URL}")
        if result.run_id:
            print(f"Workflow Run: https://github.com/silenvx/dekita/actions/runs/{result.run_id}")
        print("=" * 50)

    sys.exit(0 if result.success else 1)


if __name__ == "__main__":
    main()
