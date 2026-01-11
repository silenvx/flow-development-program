#!/usr/bin/env python3
"""仕組み化Issueクローズ時にフック/ツール実装を検証。

Why:
    「仕組み化」を謳うIssueがドキュメント追加のみでクローズされると、
    強制機構がなく問題が再発する。クローズ前に実装を確認する。

What:
    - gh issue close時（PreToolUse:Bash）に発火
    - Issue内容から「仕組み化」系キーワードを検出
    - 関連PRで強制機構ファイル（hooks/workflows/scripts）の変更を確認
    - 強制機構がない場合はクローズをブロック

Remarks:
    - ブロック型フック（強制機構なしの場合はブロック）
    - systematization-checkはセッション終了時、本フックはIssueクローズ時
    - SKIP_SYSTEMATIZATION_CHECK=1でスキップ可能

Changelog:
    - silenvx/dekita#1909: フック追加
    - silenvx/dekita#2607: HookContextパターン移行
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from lib.constants import TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.results import make_block_result, print_continue_and_log_skip
from lib.session import create_hook_context, parse_hook_input
from lib.strings import extract_inline_skip_env, is_skip_env_enabled, strip_quoted_strings

HOOK_NAME = "systematization-issue-close-check"

# 「仕組み化」系キーワード
SYSTEMATIZATION_KEYWORDS = [
    r"仕組み化",
    r"フック(?:を)?(?:作成|追加|実装)",
    r"hook(?:を)?(?:作成|追加|実装)",
    r"CI(?:を)?(?:追加|実装)",
    r"自動(?:化|チェック)",
    r"強制(?:機構|チェック)",
    r"再発防止(?:策)?(?:の)?(?:仕組み|フック)",
]

# 強制機構ファイルのパターン
ENFORCEMENT_FILE_PATTERNS = [
    r"\.claude/hooks/.*\.py$",
    r"\.github/workflows/.*\.ya?ml$",
    r"\.claude/scripts/.*\.(?:py|sh)$",
]


def extract_issue_number(command: str) -> str | None:
    """gh issue close コマンドからIssue番号を抽出."""
    cmd = strip_quoted_strings(command)

    if not re.search(r"gh\s+issue\s+close\b", cmd):
        return None

    match = re.search(r"gh\s+issue\s+close\s+(.+)", cmd)
    if not match:
        return None

    args = match.group(1)

    for part in args.split():
        if part.startswith("-"):
            continue
        num_match = re.match(r"#?(\d+)$", part)
        if num_match:
            return num_match.group(1)

    return None


def get_issue_content(issue_number: str) -> tuple[str, str] | None:
    """IssueのタイトルとボディをGitHubから取得.

    Returns:
        (title, body) のタプル、取得失敗時は None
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                issue_number,
                "--json",
                "title,body",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )

        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        return data.get("title", ""), data.get("body", "")

    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        return None


def has_systematization_keyword(title: str, body: str) -> bool:
    """タイトルまたはボディに「仕組み化」系キーワードがあるか."""
    text = f"{title}\n{body}"
    for pattern in SYSTEMATIZATION_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def get_pr_files(pr_number: int | str) -> list[str]:
    """PRの変更ファイル一覧を取得."""
    files: list[str] = []
    try:
        files_result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--json",
                "files",
                "--jq",
                ".files[].path",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )

        if files_result.returncode == 0:
            for line in files_result.stdout.strip().split("\n"):
                if line:
                    files.append(line)
    except (subprocess.TimeoutExpired, OSError):
        # gh CLI失敗時は空リストを返す（ベストエフォート）
        pass

    return files


def search_prs_by_issue(issue_number: str) -> list[int]:
    """Issue番号でPRを検索（open/merged両方）.

    Returns:
        見つかったPR番号のリスト（最新順）
    """
    pr_numbers: list[int] = []

    try:
        # --state all でopen/closed/merged全てを検索
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "all",
                "--search",
                issue_number,
                "--json",
                "number",
                "--jq",
                ".[] | .number",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )

        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if line and line.isdigit():
                    pr_numbers.append(int(line))

    except (subprocess.TimeoutExpired, OSError):
        # gh CLI失敗時は空リストを返す（ベストエフォート）
        pass

    return pr_numbers


def get_linked_pr_files(issue_number: str) -> list[str]:
    """Issueに紐づくPRで変更されたファイル一覧を取得.

    1. まずlinkedPullRequestsを確認
    2. 見つからない場合はIssue番号でPR検索（マージ済み含む）
    """
    files: list[str] = []
    pr_numbers: list[int] = []

    try:
        # 1. Issueに正式にリンクされたPRの情報を取得
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                issue_number,
                "--json",
                "linkedPullRequests",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
            check=False,
        )

        if result.returncode == 0:
            issue_data = json.loads(result.stdout)
            prs = issue_data.get("linkedPullRequests", [])
            for pr in prs:
                pr_num = pr.get("number")
                if pr_num:
                    pr_numbers.append(pr_num)

    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        # gh CLI失敗時は空リストで続行（フォールバック検索へ）
        pass

    # 2. linkedPullRequestsが空の場合、Issue番号でPR検索
    if not pr_numbers:
        pr_numbers = search_prs_by_issue(issue_number)

    # 3. 各PRの変更ファイルを取得
    for pr_number in pr_numbers:
        pr_files = get_pr_files(pr_number)
        files.extend(pr_files)

    return files


def has_enforcement_file(files: list[str]) -> list[str]:
    """変更ファイルリストから強制機構ファイルを検出.

    Returns:
        強制機構ファイルのリスト
    """
    enforcement_files = []
    for file_path in files:
        for pattern in ENFORCEMENT_FILE_PATTERNS:
            if re.search(pattern, file_path):
                enforcement_files.append(file_path)
                break
    return enforcement_files


def main() -> None:
    """フックのエントリポイント."""
    result = {"decision": "approve"}

    try:
        input_data = parse_hook_input()
        # Issue #2607: Create context for session_id logging
        ctx = create_hook_context(input_data)
        tool_name = input_data.get("tool_name", "")

        if tool_name != "Bash":
            print_continue_and_log_skip(HOOK_NAME, f"not Bash: {tool_name}", ctx=ctx)
            return

        tool_input = input_data.get("tool_input", {})
        command = tool_input.get("command", "")

        # スキップ環境変数のチェック
        skip_env_name = "SKIP_SYSTEMATIZATION_CHECK"
        if is_skip_env_enabled(os.environ.get(skip_env_name)):
            log_hook_execution(
                HOOK_NAME,
                "approve",
                f"{skip_env_name} でスキップ（環境変数）",
            )
            print(json.dumps(result))
            return

        inline_value = extract_inline_skip_env(command, skip_env_name)
        if is_skip_env_enabled(inline_value):
            log_hook_execution(
                HOOK_NAME,
                "approve",
                f"{skip_env_name} でスキップ（インライン）",
            )
            print(json.dumps(result))
            return

        # gh issue close コマンドを検出
        issue_number = extract_issue_number(command)
        if not issue_number:
            print_continue_and_log_skip(HOOK_NAME, "no issue number found", ctx=ctx)
            return

        # Issue内容を取得
        content = get_issue_content(issue_number)
        if not content:
            log_hook_execution(
                HOOK_NAME,
                "approve",
                f"Issue #{issue_number} の内容取得失敗",
            )
            print(json.dumps(result))
            return

        title, body = content

        # ドキュメント更新Issueは対象外（docs: または docs(...) プレフィックス）
        if re.match(r"^docs[:\(]", title, re.IGNORECASE):
            log_hook_execution(
                HOOK_NAME,
                "approve",
                f"Issue #{issue_number} はドキュメント更新Issue（対象外）",
            )
            print(json.dumps(result))
            return

        # 「仕組み化」系キーワードをチェック
        if not has_systematization_keyword(title, body):
            log_hook_execution(
                HOOK_NAME,
                "approve",
                f"Issue #{issue_number} は仕組み化Issueではない",
            )
            print(json.dumps(result))
            return

        # 関連PRの変更ファイルを取得
        pr_files = get_linked_pr_files(issue_number)

        # 強制機構ファイルがあるかチェック
        enforcement_files = has_enforcement_file(pr_files)

        if enforcement_files:
            files_list = ", ".join(enforcement_files[:3])
            if len(enforcement_files) > 3:
                files_list += f" 他{len(enforcement_files) - 3}件"
            log_hook_execution(
                HOOK_NAME,
                "approve",
                f"Issue #{issue_number} に強制機構ファイルあり: {files_list}",
            )
            print(json.dumps(result))
            return

        # 強制機構ファイルがない場合はブロック
        reason_lines = [
            f"Issue #{issue_number} は「仕組み化」を謳っていますが、",
            "強制機構（フック/CI/ツール）の実装が確認できません。",
            "",
            "**仕組み化の定義**:",
            "  - ドキュメント追加だけでは不十分",
            "  - 違反を**ブロック**するフック/CI/ツールが必要",
            "",
            "**確認された変更ファイル**:",
        ]

        if pr_files:
            for f in pr_files[:5]:
                reason_lines.append(f"  - {f}")
            if len(pr_files) > 5:
                reason_lines.append(f"  ... 他 {len(pr_files) - 5} 件")
        else:
            reason_lines.append("  (関連PRが見つからないか、変更ファイルがありません)")

        reason_lines.extend(
            [
                "",
                "**対応方法**:",
                "  1. フック/CI/スクリプトを実装してからクローズ",
                "  2. 強制機構が不要と判断した場合はコメントで理由を説明",
                "",
                "**スキップ方法（確認済みの場合）**:",
                f"  SKIP_SYSTEMATIZATION_CHECK=1 gh issue close {issue_number}",
            ]
        )

        reason = "\n".join(reason_lines)
        result = make_block_result(HOOK_NAME, reason)

        log_hook_execution(
            HOOK_NAME,
            "block",
            f"Issue #{issue_number} に強制機構ファイルなし",
        )

    except Exception as e:
        result = {"decision": "approve"}
        log_hook_execution(HOOK_NAME, "error", f"フックエラー: {e}")

    print(json.dumps(result))


if __name__ == "__main__":
    main()
