#!/usr/bin/env python3
"""セッション開始時にgit設定の整合性を確認し、問題があれば自動修正する。

Why:
    Worktree操作後にgit configのcore.bare=trueになることがあり、
    gitコマンドが`fatal: this operation must be run in a work tree`で
    失敗する。この既知の問題を自動検出・修正する。

What:
    - core.bareの値を確認
    - trueになっている場合は自動的にfalseに修正
    - 修正した場合は警告を出力

Remarks:
    - ブロックはしない（自動修正のみ）
    - git設定のチェックは他のフックにない（重複なし）

Changelog:
    - silenvx/dekita#975: フック追加
"""

import subprocess

from lib.constants import TIMEOUT_LIGHT
from lib.execution import log_hook_execution
from lib.session import parse_hook_input

HOOK_NAME = "git-config-check"


def get_core_bare() -> str | None:
    """core.bareの設定値を取得する。"""
    try:
        result = subprocess.run(
            ["git", "config", "core.bare"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            return result.stdout.strip().lower()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # Best effort - git command may fail
    return None


def fix_core_bare() -> bool:
    """core.bareをfalseに修正する。"""
    try:
        result = subprocess.run(
            ["git", "config", "core.bare", "false"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass  # Best effort - git config fix may fail
    return False


def main() -> None:
    """git設定の整合性を確認し、問題があれば自動修正する。"""
    # セッションIDの取得のためparse_hook_inputを呼び出す
    parse_hook_input()

    bare_value = get_core_bare()

    if bare_value == "true":
        # 自動修正を試みる
        if fix_core_bare():
            log_hook_execution(
                HOOK_NAME,
                "approve",
                "Auto-fixed core.bare=true to false",
                {"fixed": True},
            )
            print(f"""⚠️ [{HOOK_NAME}] git設定の問題を自動修正しました

**修正内容:**
- `core.bare=true` → `core.bare=false`

**原因:**
worktree操作後にgit設定が壊れることがある既知の問題です。
詳細: Issue #975

**影響:**
この問題により、gitコマンドが `fatal: this operation must be run in a work tree` で
失敗する可能性がありました。自動修正により正常に動作するようになりました。
""")
        else:
            log_hook_execution(
                HOOK_NAME,
                "warn",
                "Failed to auto-fix core.bare=true",
                {"fixed": False},
            )
            print(f"""⚠️ [{HOOK_NAME}] git設定に問題がありますが、自動修正に失敗しました

**問題:**
- `core.bare=true` が設定されています

**手動修正方法:**
```bash
git config core.bare false
```

詳細: Issue #975
""")


if __name__ == "__main__":
    main()
