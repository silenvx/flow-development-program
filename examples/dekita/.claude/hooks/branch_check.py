#!/usr/bin/env python3
"""セッション開始時にメインリポジトリのブランチ状態を確認する。

Why:
    メインリポジトリがmain以外のブランチの状態でセッションを開始すると、
    worktreeワークフローを無視した作業につながる可能性がある。

What:
    - 現在のディレクトリがworktree内かどうか確認
    - worktree内でなければ、現在のブランチを確認
    - mainでない場合はセッション開始をブロック
    - mainブランチに戻す手順を提示

Remarks:
    - ブロック型フック（mainでない場合はブロック）
    - worktree内の場合はスキップ（worktreeでは任意ブランチを許可）
    - SessionStartで発火

Changelog:
    - silenvx/dekita#xxx: フック追加
"""

import os
import subprocess
import sys

from lib.constants import TIMEOUT_LIGHT
from lib.execution import log_hook_execution
from lib.session import parse_hook_input


def get_current_branch() -> str | None:
    """現在のブランチ名を取得する。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # gitコマンドのタイムアウトまたは未インストール時は警告をスキップ
        pass
    return None


def is_in_worktree() -> bool:
    """現在のディレクトリがworktree内かどうかを確認する。"""
    cwd = os.getcwd()
    # .worktrees ディレクトリ内にいる場合はworktree
    return "/.worktrees/" in cwd or cwd.endswith("/.worktrees")


def is_main_repository() -> bool:
    """現在のディレクトリがメインリポジトリかどうかを確認する。

    git worktree listの最初のエントリがメインリポジトリ。
    現在のディレクトリがそれと一致するかを確認する。
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if lines:
                # 最初のworktreeエントリのパスを取得
                first_line = lines[0]
                if first_line.startswith("worktree "):
                    main_repo_path = first_line[9:]  # "worktree " の後
                    cwd = os.getcwd()
                    # サブディレクトリも含めてメインリポジトリ内かチェック
                    real_cwd = os.path.realpath(cwd)
                    real_main = os.path.realpath(main_repo_path)
                    return real_cwd == real_main or real_cwd.startswith(real_main + "/")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # gitコマンドのタイムアウトまたは未インストール時は警告をスキップ
        pass
    return False


def main() -> None:
    """メインリポジトリのブランチ状態を確認し、main以外ならブロックする。"""
    # セッションIDの取得のためparse_hook_inputを呼び出す
    parse_hook_input()

    # worktree内にいる場合は正常なので何もしない
    if is_in_worktree():
        return

    # メインリポジトリ以外（サブworktree等）の場合も何もしない
    if not is_main_repository():
        return

    # 現在のブランチを確認
    branch = get_current_branch()
    if branch is None:
        return

    # mainブランチでない場合はブロック
    if branch != "main":
        log_hook_execution(
            "branch-check",
            "block",
            f"Main repository is on '{branch}' branch instead of 'main'",
            {"current_branch": branch},
        )
        print(f"""🚫 [branch-check] メインリポジトリが '{branch}' ブランチになっています。

メインリポジトリは常にmainブランチに保つ必要があります。
セッション開始前にmainブランチに戻してください:

  git checkout main

未コミットの変更がある場合:
  git stash && git checkout main

別ブランチで作業する場合はworktreeを使用してください:
  git worktree add --lock .worktrees/<name> -b <branch-name>
""")
        sys.exit(2)  # exit 2 = blocking error (shows stderr to Claude)


if __name__ == "__main__":
    main()
