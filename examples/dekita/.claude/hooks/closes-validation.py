#!/usr/bin/env python3
"""コミットメッセージのCloses/Fixesキーワードの整合性をチェックする。

Why:
    大規模Issueを小さな変更でCloseしようとすると、タスクの一部のみが完了した状態で
    Issueがクローズされる。コミット時にサイズ乖離を警告する。

What:
    - git commitコマンドを検出
    - コミットメッセージからCloses/Fixes #xxxを抽出
    - Issue情報（タイトル、ラベル、本文）を取得して表示
    - コミットサイズとIssueサイズの乖離を警告（ブロックはしない）

Remarks:
    - 警告型フック（ブロックしない、systemMessageで警告）
    - PreToolUse:Bashで発火（git commitコマンド）
    - -a/-amフラグ対応（HEADとの差分を確認）
    - Issue本文/ラベル/タイトルからサイズを推定

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import json
import re
import subprocess

from lib.constants import TIMEOUT_LIGHT, TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.session import parse_hook_input
from lib.strings import strip_quoted_strings

# Closes/Fixes パターン
CLOSES_PATTERN = re.compile(r"(?:closes?|fixes?|resolves?)\s*#(\d+)", re.IGNORECASE)

# サイズ乖離検出の閾値
# 大規模Issueを小さな変更でCloseしようとする場合の警告閾値
SIZE_MISMATCH_LINE_THRESHOLD = 50  # 変更行数がこれ未満で大規模Issueなら警告
SIZE_MISMATCH_FILE_THRESHOLD = 3  # 変更ファイル数がこれ未満で大規模Issueなら警告


def get_issue_info(issue_number: int) -> dict | None:
    """GitHub APIでIssue情報を取得"""
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                str(issue_number),
                "--json",
                "title,labels,body",
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass  # Best effort - gh command may fail
    return None


def has_all_flag(command: str) -> bool:
    """Check if command has -a or --all flag (auto-staging modified files).

    Uses common.strip_quoted_strings() to avoid false positives from
    -a appearing inside commit messages.

    Pattern aligned with ui-check-reminder.py for consistency.
    """
    stripped_command = strip_quoted_strings(command)
    # Match -a, -am, -ma, or --all flags
    return bool(re.search(r"git\s+commit\s+.*(-a\b|--all\b|-[a-z]*a[a-z]*\b)", stripped_command))


def get_changed_files(use_head: bool = False) -> list[str]:
    """現在の変更ファイル一覧を取得

    Args:
        use_head: Trueの場合、git diff HEAD を使用（-a/-am フラグ対応）
    """
    try:
        cmd = ["git", "diff", "--name-only"]
        if use_head:
            cmd.append("HEAD")
        else:
            cmd.append("--cached")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            return [f for f in result.stdout.strip().split("\n") if f]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # Best effort - git command may fail
    return []


def get_diff_stats(use_head: bool = False) -> dict:
    """変更の統計情報を取得

    Args:
        use_head: Trueの場合、git diff HEAD を使用（-a/-am フラグ対応）
    """
    try:
        cmd = ["git", "diff", "--stat"]
        if use_head:
            cmd.append("HEAD")
        else:
            cmd.append("--cached")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if lines:
                # 最後の行から統計を抽出
                last_line = lines[-1]
                insertions = 0
                deletions = 0
                if "insertion" in last_line:
                    match = re.search(r"(\d+)\s+insertion", last_line)
                    if match:
                        insertions = int(match.group(1))
                if "deletion" in last_line:
                    match = re.search(r"(\d+)\s+deletion", last_line)
                    if match:
                        deletions = int(match.group(1))
                return {"insertions": insertions, "deletions": deletions}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # Best effort - git command may fail
    return {"insertions": 0, "deletions": 0}


def extract_commit_message(command: str) -> str | None:
    """コマンドからコミットメッセージを抽出"""
    # -m "message" パターン
    match = re.search(r'-m\s+["\'](.+?)["\']', command)
    if match:
        return match.group(1)

    # -m "$(cat <<'EOF' ... EOF)" パターン（HEREDOC）
    if "<<" in command and "EOF" in command:
        # HEREDOCの内容を抽出
        heredoc_match = re.search(r"<<['\"]?EOF['\"]?\s*\n?(.*?)EOF", command, re.DOTALL)
        if heredoc_match:
            return heredoc_match.group(1)

    return None


def estimate_issue_size(issue_info: dict) -> str:
    """Issueのサイズを推定する.

    Returns:
        "large": 大規模な機能追加、システム設計など.
        "medium": 中規模な変更.
        "small": 小さな修正、ドキュメント更新など.
    """
    labels = [label.get("name", "").lower() for label in issue_info.get("labels", [])]
    title = issue_info.get("title", "").lower()
    body = issue_info.get("body", "") or ""

    # ラベルによる判定
    large_labels = {"enhancement", "feature", "architecture", "refactor", "breaking"}
    medium_labels = {"bug", "improvement", "performance"}
    small_labels = {"documentation", "docs", "typo", "chore", "style"}

    if any(label in large_labels for label in labels):
        return "large"
    if any(label in small_labels for label in labels):
        return "small"
    if any(label in medium_labels for label in labels):
        return "medium"

    # タイトルによる判定
    large_keywords = [
        "システム",
        "機能",
        "アーキテクチャ",
        "リファクタ",
        "設計",
        "実装",
        "implementation",
        "feature",
    ]
    small_keywords = ["typo", "ドキュメント", "docs", "readme", "コメント"]

    if any(kw in title for kw in large_keywords):
        return "large"
    if any(kw in title for kw in small_keywords):
        return "small"

    # 本文の長さによる推定（長い説明 = 大きなタスク）
    if len(body) > 1000:
        return "large"
    if len(body) > 300:
        return "medium"

    return "medium"  # デフォルトは中規模


def check_size_mismatch(issue_info: dict, diff_stats: dict, file_count: int) -> str | None:
    """Issue サイズとコミットサイズの乖離をチェック.

    Returns:
        警告メッセージ（乖離がある場合）、None（問題なし）.
    """
    issue_size = estimate_issue_size(issue_info)
    total_changes = diff_stats.get("insertions", 0) + diff_stats.get("deletions", 0)

    # 大規模Issueを小さな変更でCloseしようとしている
    if (
        issue_size == "large"
        and total_changes < SIZE_MISMATCH_LINE_THRESHOLD
        and file_count < SIZE_MISMATCH_FILE_THRESHOLD
    ):
        return (
            "⚠️ **サイズ乖離の警告**: このIssueは大規模な変更を示唆していますが、"
            f"コミットは {total_changes} 行/{file_count} ファイルのみです。\n"
            "Issueの一部のみを実装した場合は、部分的なCloseではなく進捗報告として "
            "コミットメッセージから `Closes` を削除することを検討してください。"
        )

    return None


def main():
    """PreToolUse hook for Bash commands.

    Checks Closes/Fixes keywords in commit messages and warns about
    potential Issue/commit content mismatches.
    """
    result = {"decision": "approve"}

    try:
        data = parse_hook_input()
    except json.JSONDecodeError:
        # パースできない場合は許可
        log_hook_execution("closes-validation", "approve", None)
        print(json.dumps(result))
        return

    # Bashツールのみを対象
    tool_name = data.get("tool_name", "")
    if tool_name != "Bash":
        log_hook_execution("closes-validation", "approve", None)
        print(json.dumps(result))
        return

    tool_input = data.get("tool_input", {})
    command = tool_input.get("command", "")

    # git commit コマンドかチェック
    if "git commit" not in command:
        log_hook_execution("closes-validation", "approve", None)
        print(json.dumps(result))
        return

    # コミットメッセージを抽出
    commit_message = extract_commit_message(command)
    if not commit_message:
        log_hook_execution("closes-validation", "approve", None)
        print(json.dumps(result))
        return

    # Closes/Fixes パターンを検索
    matches = CLOSES_PATTERN.findall(commit_message)
    if not matches:
        log_hook_execution("closes-validation", "approve", None)
        print(json.dumps(result))
        return

    # 変更内容の統計を取得
    # -a/-am フラグがある場合は git diff HEAD を使用
    use_head = has_all_flag(command)
    changed_files = get_changed_files(use_head)
    diff_stats = get_diff_stats(use_head)

    # 各Issueの情報を取得して確認
    warnings = []
    size_warnings = []

    for issue_num in matches:
        issue_info = get_issue_info(int(issue_num))
        if issue_info:
            title = issue_info.get("title", "（タイトル取得失敗）")
            labels = [label.get("name", "") for label in issue_info.get("labels", [])]
            labels_str = ", ".join(labels) if labels else "なし"

            # Issue本文の最初の200文字を表示
            body = issue_info.get("body", "") or ""
            body_preview = body[:200] + "..." if len(body) > 200 else body
            body_preview = body_preview.replace("\n", " ")

            warnings.append(f"Issue #{issue_num}: {title}")
            warnings.append(f"  ラベル: {labels_str}")
            if body_preview:
                warnings.append(f"  概要: {body_preview}")

            # サイズ乖離チェック
            mismatch_warning = check_size_mismatch(issue_info, diff_stats, len(changed_files))
            if mismatch_warning:
                size_warnings.append(mismatch_warning)

    warnings.append("")
    warnings.append("【現在のコミット内容】")
    warnings.append(f"  変更ファイル数: {len(changed_files)}")
    warnings.append(f"  変更行数: +{diff_stats['insertions']} / -{diff_stats['deletions']}")
    if changed_files:
        warnings.append(f"  ファイル: {', '.join(changed_files[:5])}")
        if len(changed_files) > 5:
            warnings.append(f"    ... 他 {len(changed_files) - 5} ファイル")

    # サイズ乖離警告を追加
    if size_warnings:
        warnings.append("")
        warnings.extend(size_warnings)

    warnings.append("")
    warnings.append("⚠️ 上記のIssueをこのコミットでCloseしようとしています。")
    warnings.append("Issue内容とコミット内容が一致していることを確認してください。")

    # systemMessage として出力（ブロックはしない）
    result = {
        "decision": "approve",
        "systemMessage": "[closes-validation] Closes/Fixes キーワードを検出\n\n"
        + "\n".join(warnings),
    }
    log_hook_execution("closes-validation", "approve", "closes_keyword_detected")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
