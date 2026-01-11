#!/usr/bin/env python3
"""Dependabot PRを一括処理する。

Why:
    複数のDependabot PRを効率的に処理し、
    リベース→CI待機→マージのフローを自動化するため。

What:
    - list_dependabot_prs(): オープンなDependabot PRを取得
    - process_pr(): 1つのPRをリベース→CI待機→マージ
    - batch_merge(): 複数PRを順次処理

Remarks:
    - production-patch → dev-dependencies の順で処理
    - --dry-run でプレビュー
    - --group でグループ指定可能
    - エラー時はスキップして次へ進む

Changelog:
    - silenvx/dekita#1616: Dependabot一括マージ機能を追加
"""

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# Add hooks directory to path for imports
SCRIPT_DIR = Path(__file__).parent
HOOKS_DIR = SCRIPT_DIR.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from common import TIMEOUT_HEAVY, TIMEOUT_LONG

# Polling configuration
BEHIND_POLL_INTERVAL = 30  # seconds
BEHIND_POLL_TIMEOUT = 600  # 10 minutes
REBASE_RETRY_MAX = 3
REBASE_RETRY_INTERVAL = 10  # seconds


class PRStatus(Enum):
    """Status of PR processing."""

    PENDING = "pending"
    PROCESSING = "processing"
    MERGED = "merged"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class DependabotPR:
    """Represents a Dependabot PR."""

    number: int
    title: str
    head_ref: str
    labels: list[str]
    status: PRStatus = PRStatus.PENDING
    skip_reason: str = ""

    @property
    def group(self) -> str:
        """Determine the dependency group from labels."""
        for label in self.labels:
            if "production" in label.lower():
                return "production-patch"
            if "dev" in label.lower():
                return "dev-dependencies"
        # Default to dev-dependencies if no group label
        return "dev-dependencies"

    @property
    def sort_key(self) -> tuple[int, int]:
        """Sort key: production-patch first (0), then dev-dependencies (1)."""
        group_order = 0 if self.group == "production-patch" else 1
        return (group_order, self.number)


def log(msg: str, json_mode: bool = False) -> None:
    """Print log message (skipped in JSON mode)."""
    if not json_mode:
        print(f"[dependabot-batch] {msg}")


def run_command(
    cmd: list[str],
    timeout: int = TIMEOUT_HEAVY,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    """Run a command with proper error handling."""
    try:
        return subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=timeout,
            check=check,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Command failed: {e.stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Command timed out after {timeout}s") from e


def get_dependabot_prs() -> list[DependabotPR]:
    """Get list of open Dependabot PRs."""
    result = run_command(
        [
            "gh",
            "pr",
            "list",
            "--author",
            "app/dependabot",
            "--state",
            "open",
            "--json",
            "number,title,headRefName,labels",
        ]
    )
    prs_data = json.loads(result.stdout)

    prs = []
    for pr_data in prs_data:
        labels = [label["name"] for label in pr_data.get("labels", [])]
        prs.append(
            DependabotPR(
                number=pr_data["number"],
                title=pr_data["title"],
                head_ref=pr_data["headRefName"],
                labels=labels,
            )
        )

    # Sort by group (production-patch first) then by PR number
    prs.sort(key=lambda pr: pr.sort_key)
    return prs


def get_pr_merge_state(pr_number: int) -> str:
    """Get the merge state of a PR (CLEAN, BEHIND, DIRTY, etc.)."""
    result = run_command(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--json",
            "mergeStateStatus",
        ]
    )
    data = json.loads(result.stdout)
    return data.get("mergeStateStatus", "UNKNOWN")


def request_rebase(pr_number: int, json_mode: bool = False) -> bool:
    """Request Dependabot to rebase the PR."""
    log(f"PR #{pr_number}: Requesting rebase...", json_mode)

    for attempt in range(1, REBASE_RETRY_MAX + 1):
        try:
            run_command(
                [
                    "gh",
                    "pr",
                    "comment",
                    str(pr_number),
                    "--body",
                    "@dependabot rebase",
                ]
            )
            log(f"PR #{pr_number}: Rebase requested (attempt {attempt})", json_mode)
            return True
        except RuntimeError as e:
            if attempt < REBASE_RETRY_MAX:
                log(
                    f"PR #{pr_number}: Rebase request failed, retrying... ({e})",
                    json_mode,
                )
                time.sleep(REBASE_RETRY_INTERVAL)
            else:
                log(f"PR #{pr_number}: Rebase request failed after {attempt} attempts", json_mode)
                return False
    return False  # Satisfy type checker (loop always returns)


def wait_for_behind_resolution(pr_number: int, json_mode: bool = False) -> bool:
    """Wait for PR to no longer be BEHIND."""
    start_time = time.time()
    log(f"PR #{pr_number}: Waiting for BEHIND resolution...", json_mode)

    while time.time() - start_time < BEHIND_POLL_TIMEOUT:
        merge_state = get_pr_merge_state(pr_number)

        if merge_state == "CLEAN":
            log(f"PR #{pr_number}: Branch is up to date (CLEAN)", json_mode)
            return True
        elif merge_state == "DIRTY":
            log(f"PR #{pr_number}: Merge conflict detected (DIRTY)", json_mode)
            return False
        elif merge_state == "BLOCKED":
            log(f"PR #{pr_number}: Merge is blocked", json_mode)
            return False
        elif merge_state == "BEHIND":
            elapsed = int(time.time() - start_time)
            log(
                f"PR #{pr_number}: Still BEHIND, waiting... ({elapsed}s elapsed)",
                json_mode,
            )
        elif merge_state == "UNKNOWN":
            # UNKNOWN state indicates API/network issues - fail fast instead of polling
            log(f"PR #{pr_number}: Cannot determine state (UNKNOWN)", json_mode)
            return False
        else:
            # Any other unexpected state - fail fast
            log(f"PR #{pr_number}: Unexpected state: {merge_state}", json_mode)
            return False

        time.sleep(BEHIND_POLL_INTERVAL)

    log(f"PR #{pr_number}: Timeout waiting for BEHIND resolution", json_mode)
    return False


def wait_for_ci(pr_number: int, json_mode: bool = False) -> bool:
    """Wait for CI to pass using ci_monitor.py."""
    log(f"PR #{pr_number}: Waiting for CI...", json_mode)

    ci_monitor_path = SCRIPT_DIR / "ci_monitor.py"
    try:
        result = run_command(
            [
                "python3",
                str(ci_monitor_path),
                str(pr_number),
                "--json",
            ],
            timeout=TIMEOUT_LONG,
            check=False,
        )

        if result.returncode == 0:
            try:
                ci_result = json.loads(result.stdout)
                if ci_result.get("success") and ci_result.get("ci_passed"):
                    log(f"PR #{pr_number}: CI passed", json_mode)
                    return True
                else:
                    log(
                        f"PR #{pr_number}: CI failed - {ci_result.get('message', 'Unknown')}",
                        json_mode,
                    )
                    return False
            except json.JSONDecodeError:
                # ci-monitor.py with --json should always return JSON
                log(
                    f"PR #{pr_number}: CI monitoring returned invalid JSON output",
                    json_mode,
                )
                return False
        else:
            log(f"PR #{pr_number}: CI monitoring failed", json_mode)
            return False
    except RuntimeError as e:
        log(f"PR #{pr_number}: CI monitoring error: {e}", json_mode)
        return False


def merge_pr(pr_number: int, json_mode: bool = False) -> bool:
    """Merge the PR."""
    log(f"PR #{pr_number}: Attempting merge...", json_mode)

    try:
        run_command(
            [
                "gh",
                "pr",
                "merge",
                str(pr_number),
                "--squash",
                "--delete-branch",
            ]
        )
        log(f"PR #{pr_number}: Merged successfully", json_mode)
        return True
    except RuntimeError as e:
        log(f"PR #{pr_number}: Merge failed: {e}", json_mode)
        return False


def process_pr(pr: DependabotPR, json_mode: bool = False, dry_run: bool = False) -> bool:
    """Process a single Dependabot PR."""
    pr.status = PRStatus.PROCESSING
    log(f"PR #{pr.number}: Processing '{pr.title}' (group: {pr.group})", json_mode)

    if dry_run:
        log(f"PR #{pr.number}: [DRY-RUN] Would process this PR", json_mode)
        pr.status = PRStatus.PENDING
        return True

    # Check current merge state
    merge_state = get_pr_merge_state(pr.number)
    was_behind = merge_state == "BEHIND"

    # If BEHIND, request rebase and wait
    if was_behind:
        if not request_rebase(pr.number, json_mode):
            pr.status = PRStatus.SKIPPED
            pr.skip_reason = "Rebase request failed"
            return False

        if not wait_for_behind_resolution(pr.number, json_mode):
            pr.status = PRStatus.SKIPPED
            pr.skip_reason = "BEHIND resolution timeout or conflict"
            return False

    elif merge_state == "DIRTY":
        pr.status = PRStatus.SKIPPED
        pr.skip_reason = "Merge conflict (suggest @dependabot recreate)"
        log(f"PR #{pr.number}: Skipped - {pr.skip_reason}", json_mode)
        return False

    elif merge_state == "BLOCKED":
        pr.status = PRStatus.SKIPPED
        pr.skip_reason = "Merge blocked"
        log(f"PR #{pr.number}: Skipped - {pr.skip_reason}", json_mode)
        return False

    elif merge_state == "UNKNOWN":
        pr.status = PRStatus.SKIPPED
        pr.skip_reason = "Cannot determine merge state (API issue)"
        log(f"PR #{pr.number}: Skipped - {pr.skip_reason}", json_mode)
        return False

    elif merge_state != "CLEAN":
        # Any other unexpected state
        pr.status = PRStatus.SKIPPED
        pr.skip_reason = f"Unexpected merge state: {merge_state}"
        log(f"PR #{pr.number}: Skipped - {pr.skip_reason}", json_mode)
        return False

    # Verify CLEAN state after rebase (only if it was BEHIND)
    # Note: wait_for_behind_resolution already checks for CLEAN,
    # but we re-verify here before CI to catch race conditions
    if was_behind:
        final_state = get_pr_merge_state(pr.number)
        if final_state != "CLEAN":
            pr.status = PRStatus.SKIPPED
            pr.skip_reason = f"State changed during processing: {final_state}"
            log(f"PR #{pr.number}: Skipped - {pr.skip_reason}", json_mode)
            return False

    # Wait for CI
    if not wait_for_ci(pr.number, json_mode):
        pr.status = PRStatus.FAILED
        pr.skip_reason = "CI failed"
        return False

    # Merge
    if not merge_pr(pr.number, json_mode):
        pr.status = PRStatus.FAILED
        pr.skip_reason = "Merge failed"
        return False

    pr.status = PRStatus.MERGED
    return True


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Batch merge Dependabot PRs efficiently")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview mode (show what would be done)",
    )
    parser.add_argument(
        "--group",
        choices=["production-patch", "dev-dependencies"],
        help="Process only specific dependency group",
    )
    parser.add_argument(
        "--max",
        type=int,
        help="Maximum number of PRs to process",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format",
    )

    args = parser.parse_args()
    json_mode = args.json

    log("Fetching Dependabot PRs...", json_mode)

    try:
        prs = get_dependabot_prs()
    except RuntimeError as e:
        if json_mode:
            print(json.dumps({"error": str(e), "success": False}))
        else:
            log(f"Error fetching PRs: {e}")
        return 1

    if not prs:
        if json_mode:
            print(
                json.dumps({"message": "No open Dependabot PRs found", "prs": [], "success": True})
            )
        else:
            log("No open Dependabot PRs found")
        return 0

    # Filter by group if specified
    if args.group:
        prs = [pr for pr in prs if pr.group == args.group]
        if not prs:
            if json_mode:
                print(
                    json.dumps(
                        {
                            "message": f"No Dependabot PRs in group '{args.group}'",
                            "prs": [],
                            "success": True,
                        }
                    )
                )
            else:
                log(f"No Dependabot PRs in group '{args.group}'")
            return 0

    # Limit number of PRs if specified
    if args.max and args.max < len(prs):
        prs = prs[: args.max]

    log(f"Found {len(prs)} Dependabot PR(s) to process:", json_mode)
    for i, pr in enumerate(prs, 1):
        log(f"  {i}. PR #{pr.number}: {pr.title} (group: {pr.group})", json_mode)

    if args.dry_run:
        log("", json_mode)
        log("[DRY-RUN] Processing order:", json_mode)
        for i, pr in enumerate(prs, 1):
            log(f"  {i}. PR #{pr.number} ({pr.group})", json_mode)
        log("", json_mode)
        log("[DRY-RUN] No changes made", json_mode)

        if json_mode:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "prs": [
                            {
                                "number": pr.number,
                                "title": pr.title,
                                "group": pr.group,
                                "order": i,
                            }
                            for i, pr in enumerate(prs, 1)
                        ],
                        "success": True,
                    }
                )
            )
        return 0

    # Process PRs
    log("", json_mode)
    log("Starting batch processing...", json_mode)
    log("=" * 50, json_mode)

    merged_count = 0
    skipped_count = 0
    failed_count = 0

    for pr in prs:
        log("", json_mode)
        process_pr(pr, json_mode, args.dry_run)

        if pr.status == PRStatus.MERGED:
            merged_count += 1
        elif pr.status == PRStatus.SKIPPED:
            skipped_count += 1
        elif pr.status == PRStatus.FAILED:
            failed_count += 1

    # Summary
    log("", json_mode)
    log("=" * 50, json_mode)
    log("Batch processing complete:", json_mode)
    log(f"  Merged:  {merged_count}", json_mode)
    log(f"  Skipped: {skipped_count}", json_mode)
    log(f"  Failed:  {failed_count}", json_mode)

    if skipped_count > 0 or failed_count > 0:
        log("", json_mode)
        log("Issues encountered:", json_mode)
        for pr in prs:
            if pr.skip_reason:
                log(f"  PR #{pr.number}: {pr.skip_reason}", json_mode)

    if json_mode:
        print(
            json.dumps(
                {
                    "success": failed_count == 0,
                    "summary": {
                        "total": len(prs),
                        "merged": merged_count,
                        "skipped": skipped_count,
                        "failed": failed_count,
                    },
                    "prs": [
                        {
                            "number": pr.number,
                            "title": pr.title,
                            "group": pr.group,
                            "status": pr.status.value,
                            "skip_reason": pr.skip_reason if pr.skip_reason else None,
                        }
                        for pr in prs
                    ],
                }
            )
        )

    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
