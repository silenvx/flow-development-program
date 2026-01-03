#!/usr/bin/env python3
"""セッション開始時にローカルmainブランチの同期状態を確認する。

Why:
    ローカルmainがリモートより遅れていると、新しいフック/修正が
    適用されず問題が発生する。同期状態を確認し早期に警告する。

What:
    - git fetchでリモート情報を更新
    - ローカルmainとorigin/mainのコミット差分を確認
    - 大きく遅れている場合は警告を出力
    - 不審なコミットパターン（同一メッセージ連続）を検出

Remarks:
    - 警告型フック（ブロックしない、stderrで警告）
    - SessionStartで発火（セッション毎に1回）
    - 閾値: 5コミット以上遅れで警告、3回以上同一メッセージで不審判定
    - ネットワークエラー時はサイレント（フェッチ失敗は警告しない）

Changelog:
    - silenvx/dekita#996: フック追加
"""

import subprocess

from common import check_and_update_session_marker
from lib.constants import TIMEOUT_LIGHT, TIMEOUT_MEDIUM
from lib.execution import log_hook_execution
from lib.session import parse_hook_input

HOOK_NAME = "main-sync-check"

# 警告を出すコミット数の閾値
BEHIND_THRESHOLD = 5

# 不審なコミットパターンの閾値（同じメッセージの連続）
SUSPICIOUS_COMMIT_THRESHOLD = 3


def fetch_remote() -> bool:
    """リモート情報をフェッチする。

    Returns:
        成功したらTrue
    """
    try:
        result = subprocess.run(
            ["git", "fetch", "origin"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_MEDIUM,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def get_main_divergence() -> tuple[int, int]:
    """ローカルmainとorigin/mainの差分を取得する。

    Returns:
        (behind, ahead) のタプル。ローカルが何コミット遅れているか、進んでいるか。
    """
    try:
        # ローカルmainが存在するか確認
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "main"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode != 0:
            return 0, 0

        # behind: origin/mainにあってmainにないコミット数
        behind_result = subprocess.run(
            ["git", "rev-list", "--count", "main..origin/main"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        behind_str = behind_result.stdout.strip()
        behind = int(behind_str) if behind_result.returncode == 0 and behind_str else 0

        # ahead: mainにあってorigin/mainにないコミット数
        ahead_result = subprocess.run(
            ["git", "rev-list", "--count", "origin/main..main"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        ahead_str = ahead_result.stdout.strip()
        ahead = int(ahead_str) if ahead_result.returncode == 0 and ahead_str else 0

        return behind, ahead
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        return 0, 0


def check_suspicious_commits() -> tuple[bool, int, str | None]:
    """ローカルmainに不審なコミットパターンがないか確認する。

    同じコミットメッセージが連続している場合は異常の可能性がある。

    Returns:
        (has_suspicious, count, message) のタプル。
        has_suspicious: 不審なパターンがあればTrue
        count: 連続した回数
        message: 繰り返されているメッセージ
    """
    try:
        result = subprocess.run(
            ["git", "log", "--format=%s", "-20", "main"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode != 0:
            return False, 0, None

        raw_output = result.stdout.strip()
        if not raw_output:
            return False, 0, None
        messages = raw_output.split("\n")

        # 連続した同じメッセージをカウント
        current_msg = messages[0]
        count = 1
        max_count = 1
        max_msg = current_msg

        for msg in messages[1:]:
            if msg == current_msg:
                count += 1
                if count > max_count:
                    max_count = count
                    max_msg = current_msg
            else:
                current_msg = msg
                count = 1

        if max_count >= SUSPICIOUS_COMMIT_THRESHOLD:
            return True, max_count, max_msg

        return False, 0, None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, 0, None


def main() -> None:
    """セッション開始時にmain同期状態をチェックする。"""
    # セッションIDの取得のためparse_hook_inputを呼び出す
    parse_hook_input()

    # セッション毎に1回だけ実行
    if not check_and_update_session_marker(HOOK_NAME):
        return

    # リモート情報を更新
    if not fetch_remote():
        # フェッチ失敗は警告しない（ネットワーク問題の可能性）
        return

    # 差分を確認
    behind, ahead = get_main_divergence()

    warnings = []

    if behind >= BEHIND_THRESHOLD:
        warnings.append(
            f"⚠️ ローカルmainがorigin/mainより{behind}コミット遅れています。\n"
            f"   `git pull` でmainを更新することを推奨します。"
        )

    if ahead > 0:
        warnings.append(
            f"⚠️ ローカルmainがorigin/mainより{ahead}コミット進んでいます。\n"
            f"   これは異常な状態の可能性があります。`git status` で確認してください。"
        )

    # 不審なコミットパターンをチェック
    has_suspicious, count, msg = check_suspicious_commits()
    if has_suspicious:
        warnings.append(
            f"⚠️ mainに不審なコミットパターンを検出しました。\n"
            f"   「{msg}」が{count}回連続しています。\n"
            f"   `git reset --hard origin/main` での復旧を検討してください。"
        )

    if warnings:
        log_hook_execution(
            HOOK_NAME,
            "warn",
            f"Main sync warnings detected: behind={behind}, ahead={ahead}",
            {"behind": behind, "ahead": ahead, "has_suspicious": has_suspicious},
        )
        print(f"[{HOOK_NAME}] main同期状態の警告:\n")
        for warning in warnings:
            print(warning)
            print()


if __name__ == "__main__":
    main()
