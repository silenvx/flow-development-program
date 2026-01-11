#!/usr/bin/env python3
"""Issue判定の妥当性を評価する。

Why:
    Issue作成/不作成の判定が適切だったかを後から評価し、
    判定基準の改善に活用するため。

What:
    - create判定: Issue状態をGH APIで確認し、NOT_PLANNEDなら不適切判定
    - skip判定: 類似Issueが後から作成されたかを確認

Usage:
    # 直近7日間の判定を評価
    python3 evaluate-issue-decisions.py --days 7

    # 特定セッションの判定を評価
    python3 evaluate-issue-decisions.py --session-id "session-uuid"

    # JSON形式で出力
    python3 evaluate-issue-decisions.py --days 7 --json

Changelog:
    - silenvx/dekita#2677: 初期実装
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

TIMEOUT_SHORT = 10


def _get_main_repo_from_worktree(cwd: Path) -> Path | None:
    """Get main repository path from a worktree.

    Issue #2505: Worktrees store their git info in a `.git` file (not directory)
    containing a path like `gitdir: /path/to/main/.git/worktrees/xxx`.
    """
    git_file = cwd / ".git"
    if not git_file.is_file():
        return None

    try:
        content = git_file.read_text().strip()
        if not content.startswith("gitdir:"):
            return None

        gitdir = content.split(":", 1)[1].strip()
        gitdir_path = Path(gitdir)

        if not gitdir_path.is_absolute():
            gitdir_path = (cwd / gitdir_path).resolve()

        if gitdir_path.parent.name == "worktrees" and gitdir_path.parent.parent.name == ".git":
            if not gitdir_path.exists():
                return None
            # gitdir_pathが存在するなら、その親ディレクトリも必ず存在する
            return gitdir_path.parent.parent.parent
    except (OSError, ValueError):
        # ファイル読み取りやパースに失敗した場合はworktreeではないとみなす
        pass

    return None


def _get_project_dir() -> Path:
    """Get project directory, resolving worktree to main repo.

    Issue #2677: Ensures decision logs are read from main repo's .claude/logs/
    directory, consistent with hooks.
    """
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_dir:
        env_path = Path(env_dir)
        main_repo = _get_main_repo_from_worktree(env_path)
        if main_repo:
            return main_repo
        return env_path

    cwd = Path.cwd()
    main_repo = _get_main_repo_from_worktree(cwd)
    if main_repo:
        return main_repo

    return cwd


def get_decisions_dir() -> Path:
    """Get the directory for decision log files."""
    return _get_project_dir() / ".claude" / "logs" / "decisions"


def load_decisions(
    days: int | None = None,
    session_id: str | None = None,
) -> list[dict]:
    """Load decision records from log files.

    Args:
        days: Number of days to look back (None = all)
        session_id: Specific session ID to filter (None = all)

    Returns:
        List of decision records
    """
    decisions_dir = get_decisions_dir()
    if not decisions_dir.exists():
        return []

    decisions = []
    cutoff = None
    if days:
        cutoff = datetime.now(UTC) - timedelta(days=days)

    pattern = f"issue-decisions-{session_id}.jsonl" if session_id else "issue-decisions-*.jsonl"

    for file in decisions_dir.glob(pattern):
        with open(file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if cutoff:
                        timestamp = datetime.fromisoformat(record["timestamp"])
                        if timestamp < cutoff:
                            continue
                    decisions.append(record)
                except (json.JSONDecodeError, KeyError):
                    continue

    return decisions


def get_issue_state(issue_number: int) -> dict | None:
    """Get issue state from GitHub API.

    Returns:
        Dict with state, stateReason, title, or None if error
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                str(issue_number),
                "--json",
                "state,stateReason,title",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SHORT,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None


def find_similar_issues(problem: str, since_date: str) -> list[dict]:
    """Search for similar issues created after the skip decision.

    Args:
        problem: Problem description to search for
        since_date: ISO date string to filter issues created after

    Returns:
        List of potentially similar issues
    """
    # Extract keywords from problem (simple approach)
    keywords = [w for w in problem.split() if len(w) > 3][:3]
    if not keywords:
        return []

    # since_dateが空の場合は日付フィルタなしで検索
    if not since_date:
        return []

    search_query = " ".join(keywords)

    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--search",
                search_query,
                "--json",
                "number,title,createdAt",
                "--limit",
                "10",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SHORT,
        )
        if result.returncode != 0:
            return []

        issues = json.loads(result.stdout)
        # Filter issues created after the skip decision
        since = datetime.fromisoformat(since_date.replace("Z", "+00:00"))
        return [
            issue
            for issue in issues
            if datetime.fromisoformat(issue["createdAt"].replace("Z", "+00:00")) > since
        ]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError, ValueError):
        # ValueError: 日付パースに失敗した場合
        return []


def evaluate_decisions(decisions: list[dict]) -> dict:
    """Evaluate decision records.

    Returns:
        Evaluation results with statistics and details
    """
    results = {
        "create": {
            "total": 0,
            "appropriate": 0,  # CLOSED (completed) or MERGED
            "inappropriate": 0,  # NOT_PLANNED or DUPLICATE
            "pending": 0,  # Still OPEN
            "details": [],
        },
        "skip": {
            "total": 0,
            "potential_misses": 0,  # Similar issue created later
            "appropriate": 0,  # No similar issues
            "details": [],
        },
    }

    for decision in decisions:
        if decision["decision"] == "create":
            results["create"]["total"] += 1
            issue_number = decision.get("issue_number")
            if not issue_number:
                continue

            state = get_issue_state(issue_number)
            if not state:
                results["create"]["pending"] += 1
                continue

            if state["state"] == "OPEN":
                results["create"]["pending"] += 1
            elif state["stateReason"] in ("NOT_PLANNED", "DUPLICATE"):
                results["create"]["inappropriate"] += 1
                results["create"]["details"].append(
                    {
                        "issue": issue_number,
                        "title": state.get("title", ""),
                        "stateReason": state["stateReason"],
                        "problem": decision.get("problem", ""),
                    }
                )
            else:
                results["create"]["appropriate"] += 1

        elif decision["decision"] == "skip":
            results["skip"]["total"] += 1
            similar = find_similar_issues(
                decision.get("problem", ""),
                decision.get("timestamp", ""),
            )
            if similar:
                results["skip"]["potential_misses"] += 1
                results["skip"]["details"].append(
                    {
                        "problem": decision.get("problem", ""),
                        "reason": decision.get("reason", ""),
                        "similar_issues": [
                            {"number": i["number"], "title": i["title"]} for i in similar[:3]
                        ],
                    }
                )
            else:
                results["skip"]["appropriate"] += 1

    return results


def print_report(results: dict, as_json: bool = False) -> None:
    """Print evaluation report."""
    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    print("=== Issue判定評価レポート ===\n")

    # Create decisions
    create = results["create"]
    print("【作成判定の評価】")
    print(f"- 総数: {create['total']}件")
    if create["total"] > 0:
        appropriate_pct = create["appropriate"] / create["total"] * 100
        print(f"- 適切（CLOSED/MERGED）: {create['appropriate']}件 ({appropriate_pct:.0f}%)")
        if create["inappropriate"] > 0:
            inappropriate_pct = create["inappropriate"] / create["total"] * 100
            print(
                f"- 不適切（NOT_PLANNED/DUPLICATE）: {create['inappropriate']}件 ({inappropriate_pct:.0f}%)"
            )
        print(f"- 未評価（OPEN）: {create['pending']}件")

    if create["details"]:
        print("\n【不適切だった作成判定】")
        for detail in create["details"]:
            problem = detail["problem"]
            problem_text = f"{problem[:30]}..." if len(problem) > 30 else problem
            print(f"- #{detail['issue']}: 「{problem_text}」→ {detail['stateReason']}")

    # Skip decisions
    skip = results["skip"]
    print("\n【スキップ判定の評価】")
    print(f"- 総数: {skip['total']}件")
    if skip["total"] > 0:
        if skip["potential_misses"] > 0:
            print(f"- 後から類似Issue作成: {skip['potential_misses']}件（見逃しの可能性）")
        print(f"- 適切（類似Issueなし）: {skip['appropriate']}件")

    if skip["details"]:
        print("\n【見逃しの可能性があるスキップ判定】")
        for detail in skip["details"]:
            problem = detail["problem"]
            problem_text = f"{problem[:30]}..." if len(problem) > 30 else problem
            print(f"- 問題: 「{problem_text}」")
            print(f"  理由: {detail['reason']}")
            print("  類似Issue:")
            for issue in detail["similar_issues"]:
                title = issue["title"]
                title_text = f"{title[:40]}..." if len(title) > 40 else title
                print(f"    - #{issue['number']}: {title_text}")


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Evaluate issue creation/skip decisions")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Number of days to evaluate (default: 7 if no session-id)",
    )
    parser.add_argument("--session-id", help="Evaluate specific session only")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    # session-id指定時はdaysフィルタを無効化、未指定時はデフォルト7日
    days = args.days if args.days is not None else (None if args.session_id else 7)
    decisions = load_decisions(days=days, session_id=args.session_id)

    if not decisions:
        print("判定記録が見つかりません。", file=sys.stderr)
        return 0

    results = evaluate_decisions(decisions)
    print_report(results, as_json=args.json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
