#!/usr/bin/env python3
"""パターン検出フック作成のための実データ分析を行う。

Why:
    フックのパターン設計時に、実際のPR/Issueコメントから
    パターンの有効性を検証するデータが必要。

What:
    - search: PR/Issueコメントからパターンを検索
    - analyze: パターンの頻度分析
    - validate: パターンリストの検出率・誤検知率を検証

Remarks:
    - GitHub CLIを使用してコメントを取得
    - --pattern で正規表現パターンを指定
    - --patterns-file でパターンリストファイルを指定

Changelog:
    - silenvx/dekita#1912: パターンデータ分析機能を追加
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class PatternMatch:
    """パターンマッチ結果"""

    source: str  # "pr_comment", "issue_comment", "session_log"
    source_id: str  # PR番号、Issue番号、セッションID
    matched_text: str
    context: str
    url: str | None = None


def run_gh_command(args: list[str]) -> str | None:
    """GitHub CLIコマンドを実行"""
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            print(f"Error: {result.stderr}", file=sys.stderr)
            return None
        return result.stdout
    except subprocess.TimeoutExpired:
        print("Timeout running gh command", file=sys.stderr)
        return None
    except FileNotFoundError:
        print("GitHub CLI (gh) not found", file=sys.stderr)
        return None


_REPO_BASE_URL: str | None = None


def get_repo_base_url() -> str:
    """Get the base URL of the GitHub repository."""
    global _REPO_BASE_URL
    if _REPO_BASE_URL is not None:
        return _REPO_BASE_URL

    output = run_gh_command(["repo", "view", "--json", "url"])
    if output:
        try:
            _REPO_BASE_URL = json.loads(output)["url"]
            return _REPO_BASE_URL
        except (json.JSONDecodeError, KeyError):
            pass

    # Fallback
    print(
        "Warning: Could not determine repository URL. URLs in output may be incorrect.",
        file=sys.stderr,
    )
    _REPO_BASE_URL = "https://github.com/unknown/unknown"
    return _REPO_BASE_URL


def search_pr_comments(pattern: str, days: int = 30, limit: int = 100) -> list[PatternMatch]:
    """PR コメントからパターンを検索"""
    matches: list[PatternMatch] = []

    # 最近のPRを取得（daysパラメータでフィルタリング）
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    output = run_gh_command(
        [
            "pr",
            "list",
            "--state",
            "all",
            "--limit",
            str(limit),
            "--search",
            f"created:>={since}",
            "--json",
            "number,title,createdAt",
        ]
    )

    if not output:
        return matches

    prs = json.loads(output)

    repo_url = get_repo_base_url()

    for pr in prs:
        pr_number = pr["number"]

        # PRのコメントを取得（JSON配列として返すことで改行を含むコメントを正しく処理）
        comments_output = run_gh_command(
            [
                "api",
                f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/comments",
                "--jq",
                "[.[] | .body]",
            ]
        )

        if not comments_output:
            continue

        try:
            comments = json.loads(comments_output)
        except json.JSONDecodeError:
            continue

        for comment in comments:
            if not comment:
                continue

            for match in re.finditer(pattern, comment, re.IGNORECASE):
                start = max(0, match.start() - 50)
                end = min(len(comment), match.end() + 50)
                matches.append(
                    PatternMatch(
                        source="pr_comment",
                        source_id=f"PR #{pr_number}",
                        matched_text=match.group(),
                        context=comment[start:end],
                        url=f"{repo_url}/pull/{pr_number}",
                    )
                )

    return matches


def search_issue_comments(pattern: str, days: int = 30, limit: int = 100) -> list[PatternMatch]:
    """Issue コメントからパターンを検索"""
    matches: list[PatternMatch] = []

    # 最近のIssueを取得（daysパラメータでフィルタリング）
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    output = run_gh_command(
        [
            "issue",
            "list",
            "--state",
            "all",
            "--limit",
            str(limit),
            "--search",
            f"created:>={since}",
            "--json",
            "number,title,body",
        ]
    )

    if not output:
        return matches

    issues = json.loads(output)

    repo_url = get_repo_base_url()

    for issue in issues:
        issue_number = issue["number"]
        body = issue.get("body", "") or ""

        # Issue本文を検索
        for match in re.finditer(pattern, body, re.IGNORECASE):
            start = max(0, match.start() - 50)
            end = min(len(body), match.end() + 50)
            matches.append(
                PatternMatch(
                    source="issue_body",
                    source_id=f"Issue #{issue_number}",
                    matched_text=match.group(),
                    context=body[start:end],
                    url=f"{repo_url}/issues/{issue_number}",
                )
            )

        # Issueのコメントを取得（JSON配列として返すことで改行を含むコメントを正しく処理）
        comments_output = run_gh_command(
            [
                "api",
                f"repos/{{owner}}/{{repo}}/issues/{issue_number}/comments",
                "--jq",
                "[.[] | .body]",
            ]
        )

        if not comments_output:
            continue

        try:
            comments = json.loads(comments_output)
        except json.JSONDecodeError:
            continue

        for comment in comments:
            if not comment:
                continue

            for match in re.finditer(pattern, comment, re.IGNORECASE):
                start = max(0, match.start() - 50)
                end = min(len(comment), match.end() + 50)
                matches.append(
                    PatternMatch(
                        source="issue_comment",
                        source_id=f"Issue #{issue_number}",
                        matched_text=match.group(),
                        context=comment[start:end],
                        url=f"{repo_url}/issues/{issue_number}",
                    )
                )

    return matches


def search_session_logs(pattern: str, days: int = 7) -> list[PatternMatch]:
    """セッションログからパターンを検索"""
    matches: list[PatternMatch] = []

    logs_dir = os.path.expanduser("~/.claude/logs")
    if not os.path.exists(logs_dir):
        return matches

    cutoff = datetime.now() - timedelta(days=days)

    for filename in os.listdir(logs_dir):
        if not filename.endswith(".jsonl"):
            continue

        filepath = os.path.join(logs_dir, filename)
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
            if mtime < cutoff:
                continue

            with open(filepath, encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        content = entry.get("message", {}).get("content", "")
                        if isinstance(content, str):
                            for match in re.finditer(pattern, content, re.IGNORECASE):
                                start = max(0, match.start() - 50)
                                end = min(len(content), match.end() + 50)
                                matches.append(
                                    PatternMatch(
                                        source="session_log",
                                        source_id=filename,
                                        matched_text=match.group(),
                                        context=content[start:end],
                                    )
                                )
                    except json.JSONDecodeError:
                        continue
        except (OSError, PermissionError):
            continue

    return matches


def cmd_search(args: argparse.Namespace) -> None:
    """パターン検索コマンド"""
    pattern = args.pattern
    days = args.days
    limit = args.limit

    print(f"Searching for pattern: {pattern}")
    print(f"Time range: last {days} days")
    print("-" * 60)

    all_matches: list[PatternMatch] = []

    if not args.skip_pr:
        print("Searching PR comments...")
        pr_matches = search_pr_comments(pattern, days, limit)
        all_matches.extend(pr_matches)
        print(f"  Found {len(pr_matches)} matches in PR comments")

    if not args.skip_issue:
        print("Searching Issue comments...")
        issue_matches = search_issue_comments(pattern, days, limit)
        all_matches.extend(issue_matches)
        print(f"  Found {len(issue_matches)} matches in Issue comments")

    if not args.skip_logs:
        print("Searching session logs...")
        log_matches = search_session_logs(pattern, min(days, 7))
        all_matches.extend(log_matches)
        print(f"  Found {len(log_matches)} matches in session logs")

    print("-" * 60)
    print(f"Total matches: {len(all_matches)}")

    if all_matches and args.show_matches:
        print("\nMatches:")
        for i, m in enumerate(all_matches[:20], 1):
            print(f"\n{i}. [{m.source}] {m.source_id}")
            print(f"   Matched: {m.matched_text}")
            print(f"   Context: ...{m.context}...")
            if m.url:
                print(f"   URL: {m.url}")

    # 出力ファイルに保存
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {
                        "source": m.source,
                        "source_id": m.source_id,
                        "matched_text": m.matched_text,
                        "context": m.context,
                        "url": m.url,
                    }
                    for m in all_matches
                ],
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"\nResults saved to: {args.output}")


def cmd_analyze(args: argparse.Namespace) -> None:
    """パターン頻度分析コマンド"""
    pattern = args.pattern
    days = args.days
    limit = args.limit

    print(f"Analyzing pattern frequency: {pattern}")
    print("-" * 60)

    all_matches: list[PatternMatch] = []
    all_matches.extend(search_pr_comments(pattern, days, limit))
    all_matches.extend(search_issue_comments(pattern, days, limit))
    all_matches.extend(search_session_logs(pattern, min(days, 7)))

    if not all_matches:
        print("No matches found.")
        return

    # 頻度分析
    text_counter = Counter(m.matched_text.lower() for m in all_matches)
    source_counter = Counter(m.source for m in all_matches)

    print(f"\nTotal matches: {len(all_matches)}")

    print("\nMatched text frequency:")
    for text, count in text_counter.most_common(10):
        print(f"  {text}: {count}")

    print("\nSource distribution:")
    for source, count in source_counter.items():
        print(f"  {source}: {count}")

    # コンテキスト分析（前後の単語）
    print("\nCommon context patterns:")
    context_patterns: list[str] = []
    for m in all_matches:
        # マッチの前後の単語を抽出
        words = re.findall(r"\w+", m.context)
        if len(words) >= 3:
            context_patterns.append(" ".join(words[:3]))

    context_counter = Counter(context_patterns)
    for pattern, count in context_counter.most_common(5):
        print(f"  {pattern}: {count}")


def cmd_validate(args: argparse.Namespace) -> None:
    """パターンリスト検証コマンド"""
    if not os.path.exists(args.patterns_file):
        print(f"Error: File not found: {args.patterns_file}", file=sys.stderr)
        sys.exit(1)

    with open(args.patterns_file, encoding="utf-8") as f:
        patterns = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    print(f"Validating {len(patterns)} patterns")
    print("-" * 60)

    results: list[dict] = []

    for pattern in patterns:
        print(f"\nPattern: {pattern}")

        # 各パターンで検索
        matches: list[PatternMatch] = []
        matches.extend(search_pr_comments(pattern, args.days, args.limit))
        matches.extend(search_issue_comments(pattern, args.days, args.limit))

        # 検出数
        match_count = len(matches)

        # 誤検知の推定（コードブロック内など）
        false_positives = sum(1 for m in matches if "```" in m.context or "`" in m.matched_text)

        false_positive_rate = false_positives / match_count if match_count > 0 else 0

        print(f"  Matches: {match_count}")
        print(f"  Estimated false positives: {false_positives} ({false_positive_rate:.1%})")

        results.append(
            {
                "pattern": pattern,
                "matches": match_count,
                "false_positives": false_positives,
                "false_positive_rate": false_positive_rate,
            }
        )

    # サマリー
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    total_matches = sum(r["matches"] for r in results)
    total_fp = sum(r["false_positives"] for r in results)

    print(f"Total patterns: {len(patterns)}")
    print(f"Total matches: {total_matches}")
    print(f"Total estimated false positives: {total_fp}")

    if total_matches > 0:
        print(f"Overall false positive rate: {total_fp / total_matches:.1%}")

    # 推奨事項
    print("\nRecommendations:")
    low_match_patterns = [r["pattern"] for r in results if r["matches"] < 3]
    high_fp_patterns = [r["pattern"] for r in results if r["false_positive_rate"] > 0.3]

    if low_match_patterns:
        print(f"  - Low match patterns (consider removing): {low_match_patterns[:3]}")
    if high_fp_patterns:
        print(f"  - High false positive patterns (refine): {high_fp_patterns[:3]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="パターン検出フック作成時の実データ分析ツール")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # search コマンド
    search_parser = subparsers.add_parser("search", help="パターンを検索")
    search_parser.add_argument("--pattern", "-p", required=True, help="検索パターン（正規表現）")
    search_parser.add_argument("--days", "-d", type=int, default=30, help="検索対象の日数")
    search_parser.add_argument("--limit", "-l", type=int, default=100, help="PR/Issue検索数の上限")
    search_parser.add_argument("--output", "-o", help="結果出力ファイル（JSON）")
    search_parser.add_argument("--show-matches", "-s", action="store_true", help="マッチを表示")
    search_parser.add_argument("--skip-pr", action="store_true", help="PRコメント検索をスキップ")
    search_parser.add_argument(
        "--skip-issue", action="store_true", help="Issueコメント検索をスキップ"
    )
    search_parser.add_argument(
        "--skip-logs", action="store_true", help="セッションログ検索をスキップ"
    )
    search_parser.set_defaults(func=cmd_search)

    # analyze コマンド
    analyze_parser = subparsers.add_parser("analyze", help="パターン頻度を分析")
    analyze_parser.add_argument("--pattern", "-p", required=True, help="分析パターン（正規表現）")
    analyze_parser.add_argument("--days", "-d", type=int, default=30, help="分析対象の日数")
    analyze_parser.add_argument("--limit", "-l", type=int, default=100, help="PR/Issue検索数の上限")
    analyze_parser.set_defaults(func=cmd_analyze)

    # validate コマンド
    validate_parser = subparsers.add_parser("validate", help="パターンリストを検証")
    validate_parser.add_argument("--patterns-file", "-f", required=True, help="パターンファイル")
    validate_parser.add_argument("--days", "-d", type=int, default=30, help="検証対象の日数")
    validate_parser.add_argument("--limit", "-l", type=int, default=50, help="PR/Issue検索数の上限")
    validate_parser.set_defaults(func=cmd_validate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
