#!/usr/bin/env python3
"""PRライフサイクルメトリクスを収集する。

Why:
    PRのサイクルタイム、レビュー時間、CI時間を分析し、
    開発プロセスの改善ポイントを特定するため。

What:
    - collect_pr_metrics(): PRメトリクスを収集
    - collect_recent(): 最近マージされたPRを一括収集

State:
    - reads: GitHub API（gh pr view）
    - writes: .claude/logs/metrics/pr-metrics.jsonl

Remarks:
    - --recent で直近のマージ済みPRを収集
    - worktree内でも本体のログに書き込む

Changelog:
    - silenvx/dekita#1400: PRメトリクス収集機能を追加
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# ログディレクトリ
SCRIPT_DIR = Path(__file__).parent


def _get_main_project_root() -> Path:
    """メインプロジェクトルートを取得"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=SCRIPT_DIR,
        )
        if result.returncode == 0:
            git_common_dir = Path(result.stdout.strip())
            if git_common_dir.name == ".git":
                return git_common_dir.parent
            return git_common_dir.parent
    except Exception:
        pass  # git コマンド失敗時はフォールバックを使用
    return SCRIPT_DIR.parent.parent


PROJECT_ROOT = _get_main_project_root()
LOGS_DIR = PROJECT_ROOT / ".claude" / "logs"
METRICS_LOG_DIR = LOGS_DIR / "metrics"
PR_METRICS_LOG = METRICS_LOG_DIR / "pr-metrics.log"


def fetch_pr_details(pr_number: int) -> dict[str, Any] | None:
    """PR詳細情報を取得"""
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--json",
                "number,title,state,createdAt,mergedAt,author,reviews,comments,commits,additions,deletions,changedFiles,labels,reviewDecision",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=PROJECT_ROOT,
        )

        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception:
        pass  # API失敗時はNoneを返す

    return None


def fetch_pr_checks(pr_number: int) -> dict[str, Any]:
    """PRのCIチェック情報を取得"""
    checks_info = {
        "total_checks": 0,
        "passed": 0,
        "failed": 0,
        "pending": 0,
    }

    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "checks",
                str(pr_number),
                "--json",
                "name,state,conclusion",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=PROJECT_ROOT,
        )

        if result.returncode == 0:
            checks = json.loads(result.stdout)
            checks_info["total_checks"] = len(checks)
            for check in checks:
                state = check.get("state", "").upper()
                conclusion = check.get("conclusion", "").upper()
                if state == "SUCCESS" or conclusion == "SUCCESS":
                    checks_info["passed"] += 1
                elif state == "FAILURE" or conclusion == "FAILURE":
                    checks_info["failed"] += 1
                else:
                    checks_info["pending"] += 1

    except Exception:
        pass  # CI情報取得失敗時はデフォルト値を返す

    return checks_info


def fetch_review_details(pr_number: int) -> dict[str, Any]:
    """レビュー詳細情報を取得"""
    review_info = {
        "total_reviews": 0,
        "approved": 0,
        "changes_requested": 0,
        "commented": 0,
        "ai_reviews": 0,
        "human_reviews": 0,
        "reviewers": [],
    }

    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/reviews",
                "--jq",
                ".",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=PROJECT_ROOT,
        )

        if result.returncode == 0:
            reviews = json.loads(result.stdout)
            review_info["total_reviews"] = len(reviews)

            ai_reviewers = {"copilot", "github-actions", "codex", "openai"}
            reviewers_seen = set()

            for review in reviews:
                state = review.get("state", "").upper()
                if state == "APPROVED":
                    review_info["approved"] += 1
                elif state == "CHANGES_REQUESTED":
                    review_info["changes_requested"] += 1
                elif state == "COMMENTED":
                    review_info["commented"] += 1

                user = review.get("user", {})
                login = user.get("login", "").lower()
                user_type = user.get("type", "").lower()

                if login not in reviewers_seen:
                    reviewers_seen.add(login)
                    if user_type == "bot" or any(ai in login for ai in ai_reviewers):
                        review_info["ai_reviews"] += 1
                    else:
                        review_info["human_reviews"] += 1

            review_info["reviewers"] = list(reviewers_seen)

    except Exception:
        pass  # レビュー情報取得失敗時はデフォルト値を返す

    return review_info


def calculate_metrics(
    pr_data: dict[str, Any], checks: dict[str, Any], reviews: dict[str, Any]
) -> dict[str, Any]:
    """メトリクスを計算"""
    metrics = {
        "timestamp": datetime.now(UTC).isoformat(),
        "type": "pr_lifecycle",
        # 基本情報
        "pr_number": pr_data.get("number"),
        "title": pr_data.get("title", "")[:100],
        "state": pr_data.get("state"),
        "author": pr_data.get("author", {}).get("login"),
        # 時間メトリクス
        "created_at": pr_data.get("createdAt"),
        "merged_at": pr_data.get("mergedAt"),
        "cycle_time_hours": None,
        # サイズメトリクス
        "additions": pr_data.get("additions", 0),
        "deletions": pr_data.get("deletions", 0),
        "changed_files": pr_data.get("changedFiles", 0),
        "commits": len(pr_data.get("commits", [])),
        # レビューメトリクス
        "review_decision": pr_data.get("reviewDecision"),
        "total_reviews": reviews["total_reviews"],
        "approved_count": reviews["approved"],
        "changes_requested_count": reviews["changes_requested"],
        "ai_reviews": reviews["ai_reviews"],
        "human_reviews": reviews["human_reviews"],
        "comment_count": len(pr_data.get("comments", [])),
        # CIメトリクス
        "ci_checks_total": checks["total_checks"],
        "ci_checks_passed": checks["passed"],
        "ci_checks_failed": checks["failed"],
        # ラベル
        "labels": [label.get("name") for label in pr_data.get("labels", [])],
    }

    # サイクルタイム計算
    if pr_data.get("createdAt") and pr_data.get("mergedAt"):
        created = datetime.fromisoformat(pr_data["createdAt"].replace("Z", "+00:00"))
        merged = datetime.fromisoformat(pr_data["mergedAt"].replace("Z", "+00:00"))
        cycle_time = (merged - created).total_seconds() / 3600
        metrics["cycle_time_hours"] = round(cycle_time, 2)

    return metrics


def record_metrics(metrics: dict[str, Any]) -> None:
    """メトリクスを記録"""
    METRICS_LOG_DIR.mkdir(parents=True, exist_ok=True)

    try:
        with open(PR_METRICS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(metrics, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"Warning: Failed to write PR metrics: {e}", file=sys.stderr)


def collect_recent_prs(days: int = 7) -> list[int]:
    """最近マージされたPRを取得"""
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "merged",
                "--limit",
                "20",
                "--json",
                "number,mergedAt",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=PROJECT_ROOT,
        )

        if result.returncode == 0:
            prs = json.loads(result.stdout)
            cutoff = datetime.now(UTC) - timedelta(days=days)
            recent = []

            for pr in prs:
                if pr.get("mergedAt"):
                    merged = datetime.fromisoformat(pr["mergedAt"].replace("Z", "+00:00"))
                    if merged >= cutoff:
                        recent.append(pr["number"])

            return recent

    except Exception:
        pass  # 最近のPR取得失敗時は空リストを返す

    return []


def is_already_recorded(pr_number: int) -> bool:
    """既に記録済みかチェック"""
    if not PR_METRICS_LOG.exists():
        return False

    try:
        with open(PR_METRICS_LOG, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("pr_number") == pr_number:
                        return True
                except json.JSONDecodeError:
                    continue  # 不正なJSONは無視
    except OSError:
        pass  # ファイル読み込みエラー時は未記録とみなす

    return False


def main():
    if len(sys.argv) < 2:
        print("Usage: collect-pr-metrics.py <pr_number>")
        print("       collect-pr-metrics.py --recent")
        sys.exit(1)

    pr_numbers = []

    if sys.argv[1] == "--recent":
        pr_numbers = collect_recent_prs()
        print(f"Found {len(pr_numbers)} recently merged PRs")
    else:
        try:
            pr_numbers = [int(sys.argv[1])]
        except ValueError:
            print(f"Invalid PR number: {sys.argv[1]}")
            sys.exit(1)

    collected = 0
    skipped = 0

    for pr_number in pr_numbers:
        if is_already_recorded(pr_number):
            skipped += 1
            continue

        pr_data = fetch_pr_details(pr_number)
        if not pr_data:
            print(f"Warning: Could not fetch PR #{pr_number}")
            continue

        checks = fetch_pr_checks(pr_number)
        reviews = fetch_review_details(pr_number)
        metrics = calculate_metrics(pr_data, checks, reviews)

        record_metrics(metrics)
        collected += 1

        print(
            f"Collected PR #{pr_number}: {metrics.get('cycle_time_hours', 'N/A')}h cycle time, {metrics.get('total_reviews', 0)} reviews"
        )

    print(f"\nSummary: {collected} collected, {skipped} skipped (already recorded)")


if __name__ == "__main__":
    main()
