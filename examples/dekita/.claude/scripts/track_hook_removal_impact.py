#!/usr/bin/env python3
"""フック削除・非推奨化の影響を追跡する。

Why:
    フック削除後のリグレッションを検出し、
    復元または完全削除の判断材料を提供するため。

What:
    - analyze_impact(): 削除後の影響を分析
    - check_related_issues(): 関連Issueを検索
    - check_error_patterns(): エラーパターンを検出

State:
    - reads: .claude/hooks/removal-history.json
    - reads: GitHub Issues/PRs（gh API）

Remarks:
    - --weeks N で分析期間を指定（デフォルト: 4週間）
    - --hook で特定フックに絞り込み
    - キーワードマッチで関連問題を検出

Changelog:
    - silenvx/dekita#1400: フック削除影響追跡機能を追加
    - silenvx/dekita#2762: metadata.json依存を削除
"""

import argparse
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

# Project paths
SCRIPTS_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPTS_DIR.parent.parent
HOOKS_DIR = PROJECT_DIR / ".claude" / "hooks"

REMOVAL_HISTORY_PATH = HOOKS_DIR / "removal-history.json"

# Analysis parameters
DEFAULT_WEEKS = 4
IMPACT_KEYWORDS = {
    "worktree-warning": ["main branch", "直接編集", "wrong branch"],
    "force-push-guard": ["force push", "lost commit", "history rewrite"],
    "merge-check": ["review not", "レビュー未", "unresolved comment"],
    "closes-keyword-check": ["forgot closes", "issue not closed", "close忘れ"],
}


def load_json(path: Path) -> dict:
    """Load JSON file safely."""
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def get_issues_since(date_str: str) -> list[dict]:
    """Get issues created since the given date using gh CLI."""
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--state",
                "all",
                "--json",
                "number,title,body,createdAt,labels",
                "--limit",
                "100",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return []

        issues = json.loads(result.stdout)
        cutoff = datetime.fromisoformat(date_str)

        return [
            issue
            for issue in issues
            if datetime.fromisoformat(issue["createdAt"].rstrip("Z")) >= cutoff
        ]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return []


def get_commits_since(date_str: str) -> list[dict]:
    """Get commits since the given date."""
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                f"--since={date_str}",
                "--pretty=format:%H|%s|%b",
                "--no-merges",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=PROJECT_DIR,
        )
        if result.returncode != 0:
            return []

        commits = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|", 2)
            if len(parts) >= 2:
                commits.append(
                    {
                        "sha": parts[0],
                        "subject": parts[1],
                        "body": parts[2] if len(parts) > 2 else "",
                    }
                )
        return commits
    except (subprocess.TimeoutExpired, OSError):
        return []


def analyze_removal_impact(removal: dict, issues: list[dict], commits: list[dict]) -> dict:
    """Analyze the impact of a single hook removal."""
    hook_name = removal["hook"]
    # Copy to avoid mutating the shared template
    keywords = list(IMPACT_KEYWORDS.get(hook_name, []))

    impact = {
        "hook": hook_name,
        "removal_date": removal["date"],
        "reason": removal.get("reason", ""),
        "related_issues": [],
        "related_commits": [],
        "potential_regressions": [],
        "recommendation": "monitor",
    }

    if not keywords:
        impact["note"] = "No keywords defined for this hook"
        return impact

    # Filter issues/commits to only those created AFTER the removal date
    removal_date_str = removal.get("date", "")
    if removal_date_str:
        removal_dt = datetime.strptime(removal_date_str, "%Y-%m-%d")
        issues = [
            i for i in issues if datetime.fromisoformat(i["createdAt"].rstrip("Z")) >= removal_dt
        ]
        # For commits, we don't have date in the current data structure
        # so we keep all commits (conservative approach)

    # Search issues for related keywords
    for issue in issues:
        text = f"{issue['title']} {issue.get('body', '')}"
        matched_keywords = [kw for kw in keywords if kw.lower() in text.lower()]
        if matched_keywords:
            impact["related_issues"].append(
                {
                    "number": issue["number"],
                    "title": issue["title"],
                    "matched_keywords": matched_keywords,
                }
            )

    # Search commits for related keywords
    for commit in commits:
        text = f"{commit['subject']} {commit.get('body', '')}"
        matched_keywords = [kw for kw in keywords if kw.lower() in text.lower()]
        if matched_keywords:
            impact["related_commits"].append(
                {
                    "sha": commit["sha"][:8],
                    "subject": commit["subject"],
                    "matched_keywords": matched_keywords,
                }
            )

    # Determine recommendation
    issue_count = len(impact["related_issues"])
    commit_count = len(impact["related_commits"])

    if issue_count >= 3 or commit_count >= 5:
        impact["recommendation"] = "restore"
        impact["potential_regressions"].append(
            f"Multiple related issues ({issue_count}) or commits ({commit_count}) found"
        )
    elif issue_count >= 1 or commit_count >= 2:
        impact["recommendation"] = "review"
        impact["potential_regressions"].append(
            f"Some related activity found (issues: {issue_count}, commits: {commit_count})"
        )
    else:
        impact["recommendation"] = "safe_to_remove_permanently"

    return impact


def format_report(impacts: list[dict]) -> str:
    """Format impact analysis as markdown report."""
    lines = [
        "# Hook Removal Impact Report",
        f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    if not impacts:
        lines.append("No removed/deprecated hooks to analyze.")
        return "\n".join(lines)

    # Summary
    restore_count = len([i for i in impacts if i["recommendation"] == "restore"])
    review_count = len([i for i in impacts if i["recommendation"] == "review"])
    safe_count = len([i for i in impacts if i["recommendation"] == "safe_to_remove_permanently"])

    lines.extend(
        [
            "## Summary",
            f"- **Recommend restore**: {restore_count}",
            f"- **Need review**: {review_count}",
            f"- **Safe to remove**: {safe_count}",
            "",
            "## Details",
            "",
        ]
    )

    for impact in impacts:
        rec_emoji = {
            "restore": "🔴",
            "review": "🟡",
            "safe_to_remove_permanently": "🟢",
            "monitor": "⚪",
        }.get(impact["recommendation"], "⚪")

        lines.extend(
            [
                f"### {rec_emoji} {impact['hook']}",
                f"- **Removed**: {impact['removal_date']}",
                f"- **Reason**: {impact['reason']}",
                f"- **Recommendation**: {impact['recommendation']}",
                "",
            ]
        )

        if impact["related_issues"]:
            lines.append("**Related Issues**:")
            for issue in impact["related_issues"][:5]:
                lines.append(f"- #{issue['number']}: {issue['title']}")
            if len(impact["related_issues"]) > 5:
                lines.append(f"  ... and {len(impact['related_issues']) - 5} more")
            lines.append("")

        if impact["related_commits"]:
            lines.append("**Related Commits**:")
            for commit in impact["related_commits"][:5]:
                lines.append(f"- `{commit['sha']}`: {commit['subject'][:60]}")
            if len(impact["related_commits"]) > 5:
                lines.append(f"  ... and {len(impact['related_commits']) - 5} more")
            lines.append("")

        if impact["potential_regressions"]:
            lines.append("**Potential Regressions**:")
            for regression in impact["potential_regressions"]:
                lines.append(f"- {regression}")
            lines.append("")

        if impact.get("note"):
            lines.append(f"*Note: {impact['note']}*")
            lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Track hook removal impact")
    parser.add_argument("--weeks", type=int, default=DEFAULT_WEEKS, help="Weeks to analyze")
    parser.add_argument("--hook", help="Analyze specific hook only")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    # Load removal history
    history = load_json(REMOVAL_HISTORY_PATH)
    removals = history.get("removals", [])

    if not removals:
        print("No removal history found.")
        return

    # Filter to specific hook if requested
    if args.hook:
        removals = [r for r in removals if r["hook"] == args.hook]
        if not removals:
            print(f"No removal history found for hook: {args.hook}")
            return

    # Filter to deprecated/removed actions only
    removals = [r for r in removals if r.get("action") in ("deprecated", "removed")]

    if not removals:
        print("No deprecated/removed hooks found in history.")
        return

    # Get data for analysis
    cutoff_date = (datetime.now() - timedelta(weeks=args.weeks)).strftime("%Y-%m-%d")
    issues = get_issues_since(cutoff_date)
    commits = get_commits_since(cutoff_date)

    # Analyze each removal
    impacts = []
    for removal in removals:
        impact = analyze_removal_impact(removal, issues, commits)
        impacts.append(impact)

    # Output
    if args.json:
        print(json.dumps(impacts, ensure_ascii=False, indent=2))
    else:
        print(format_report(impacts))


if __name__ == "__main__":
    main()
