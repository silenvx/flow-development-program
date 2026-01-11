#!/usr/bin/env python3
"""Issue作成前に類似Issueを検索して重複を警告する。

Why:
    同じ問題のIssueが重複作成されると、議論が分散し対応が遅れる。
    作成前に類似Issueを表示することで重複を防止する。

What:
    - gh issue createコマンドを検出
    - タイトルからキーワードを抽出して類似Issueを検索
    - 類似Issueがあれば警告メッセージを表示

Remarks:
    - 警告型フック（ブロックしない、systemMessageで警告）
    - PreToolUse:Bashで発火（gh issue createコマンド）
    - ストップワード除外でキーワード抽出精度を向上
    - 最大5件の類似Issueを表示

Changelog:
    - silenvx/dekita#1980: フック追加
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent
sys.path.insert(0, str(HOOKS_DIR))
from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.session import parse_hook_input

# 検索結果の最大件数
MAX_SEARCH_RESULTS = 5

# キーワードの最小文字数
# Issueタイトルは短い傾向があるため、pr_related_issue_check.py（=3）より短く設定
MIN_KEYWORD_LENGTH = 2

# タイトルから除外するストップワード（日本語・英語）
STOP_WORDS = {
    # 日本語
    "の",
    "を",
    "に",
    "は",
    "が",
    "で",
    "と",
    "も",
    "や",
    "へ",
    "から",
    "まで",
    "より",
    "など",
    "について",
    "ため",
    "こと",
    "もの",
    "これ",
    "それ",
    "あれ",
    "この",
    "その",
    "ある",
    "いる",
    "する",
    "なる",
    "できる",
    # 英語
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "must",
    "can",
    "to",
    "of",
    "in",
    "for",
    "on",
    "with",
    "at",
    "by",
    "from",
    "as",
    "into",
    "through",
    "during",
    "before",
    "after",
    "above",
    "below",
    "between",
    "under",
    "again",
    "further",
    "then",
    "once",
    "here",
    "there",
    "when",
    "where",
    "why",
    "how",
    "all",
    "each",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "no",
    "nor",
    "not",
    "only",
    "own",
    "same",
    "so",
    "than",
    "too",
    "very",
    "just",
    "and",
    "but",
    "if",
    "or",
    "because",
    "until",
    "while",
    # プレフィックス（conventional commits）
    "feat",
    "fix",
    "docs",
    "style",
    "refactor",
    "test",
    "chore",
    "perf",
    "ci",
    "build",
    "revert",
    # アクション動詞（検索精度向上のため追加）
    "add",
    "update",
    "remove",
    "delete",
    "change",
    "modify",
    "improve",
    "create",
    "implement",
    "enable",
    "disable",
}


def extract_title_from_command(command: str) -> str | None:
    """gh issue create コマンドからタイトルを抽出する。

    Args:
        command: 実行するコマンド文字列

    Returns:
        タイトル文字列、見つからない場合は None
    """
    try:
        args = shlex.split(command)
    except ValueError:
        return None

    # --title または -t オプションを探す
    for i, arg in enumerate(args):
        if arg in ("--title", "-t") and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith("--title="):
            return arg[len("--title=") :]
        if arg.startswith("-t="):
            return arg[len("-t=") :]

    return None


def extract_keywords(title: str) -> list[str]:
    """タイトルから検索用キーワードを抽出する。

    Args:
        title: Issue タイトル

    Returns:
        キーワードのリスト
    """
    # プレフィックス（feat:, fix:, feat(scope): など）を除去
    title = re.sub(
        r"^(feat|fix|docs|style|refactor|test|chore|perf|ci|build|revert)(?:\([^)]*\))?\s*:\s*",
        "",
        title,
        flags=re.IGNORECASE,
    )

    # 記号を除去してトークン化
    tokens = re.findall(r"[\w\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]+", title.lower())

    # ストップワードと短すぎるトークンを除外
    keywords = [t for t in tokens if t not in STOP_WORDS and len(t) >= MIN_KEYWORD_LENGTH]

    # 重複を除去して最大5つ
    seen = set()
    unique = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)

    return unique[:5]


def search_similar_issues(keywords: list[str]) -> list[dict]:
    """類似Issueを検索する。

    Args:
        keywords: 検索キーワードのリスト

    Returns:
        類似Issueのリスト
    """
    if not keywords:
        return []

    # キーワードをスペース区切りで連結して検索クエリを構築
    query = " ".join(keywords)

    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--search",
                query,
                "--state",
                "open",
                "--limit",
                str(MAX_SEARCH_RESULTS),
                "--json",
                "number,title",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )

        if result.returncode != 0:
            return []

        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []


def format_warning_message(similar_issues: list[dict]) -> str:
    """警告メッセージをフォーマットする。

    Args:
        similar_issues: 類似Issueのリスト

    Returns:
        警告メッセージ
    """
    lines = ["⚠️ **類似Issueが存在する可能性があります**:", ""]

    for issue in similar_issues:
        number = issue.get("number", "?")
        title = issue.get("title", "No title")
        # タイトルが長すぎる場合は切り詰め
        if len(title) > 60:
            title = title[:57] + "..."
        lines.append(f"  - #{number}: {title}")

    lines.append("")
    lines.append("重複でないことを確認してから作成してください。")

    return "\n".join(lines)


def main():
    """
    PreToolUse:Bash hook for gh issue create commands.

    Searches for similar issues before creation and warns if found.
    """
    result = {"decision": "approve"}

    try:
        input_data = parse_hook_input()
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        # Only process Bash commands
        if tool_name != "Bash":
            print(json.dumps(result))
            return

        command = tool_input.get("command", "")

        # Check if this is a gh issue create command
        # shlex.split() でトークン化して gh issue create の連続を確認（誤検知防止）
        try:
            tokens = shlex.split(command)
        except ValueError:
            # コマンドが正しくパースできない場合はスキップ
            print(json.dumps(result))
            return

        # tokens 内で "gh", "issue", "create" が連続して出現するか確認
        # チェーンコマンド（cd repo && gh issue create）にも対応
        found = False
        for i in range(len(tokens) - 2):
            if tokens[i] == "gh" and tokens[i + 1] == "issue" and tokens[i + 2] == "create":
                found = True
                break

        if not found:
            print(json.dumps(result))
            return

        # Extract title from command
        title = extract_title_from_command(command)
        if not title:
            # タイトルが抽出できない場合はスキップ
            log_hook_execution(
                "duplicate-issue-check",
                "approve",
                "no title found in command",
            )
            print(json.dumps(result))
            return

        # Extract keywords from title
        keywords = extract_keywords(title)
        if not keywords:
            log_hook_execution(
                "duplicate-issue-check",
                "approve",
                "no keywords extracted from title",
            )
            print(json.dumps(result))
            return

        # Search for similar issues
        similar_issues = search_similar_issues(keywords)
        if similar_issues:
            result["systemMessage"] = format_warning_message(similar_issues)
            log_hook_execution(
                "duplicate-issue-check",
                "approve",
                f"found {len(similar_issues)} similar issues",
                {"keywords": keywords, "similar_count": len(similar_issues)},
            )
        else:
            log_hook_execution(
                "duplicate-issue-check",
                "approve",
                "no similar issues found",
                {"keywords": keywords},
            )

    except Exception as e:
        print(f"[duplicate-issue-check] Error: {e}", file=sys.stderr)
        log_hook_execution("duplicate-issue-check", "approve", f"error: {e}")

    print(json.dumps(result))


if __name__ == "__main__":
    main()
