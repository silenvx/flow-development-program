#!/usr/bin/env python3
"""git branch -m/-Mコマンド（ブランチリネーム）をブロックする。

Why:
    ブランチリネームはmain/masterのgit設定破損、リモートとの不整合、
    他のセッションやCIとの競合を引き起こす可能性がある。

What:
    - git branch -m/-M/--moveコマンドを検出
    - 検出時にブロック
    - 意図的なリネーム用のスキップ方法を提示

Remarks:
    - ブロック型フック（ブランチリネームはブロック）
    - SKIP_BRANCH_RENAME=1で意図的なリネームを許可
    - PreToolUse:Bashで発火

Changelog:
    - silenvx/dekita#996: 繰り返されるブランチリネーム問題への対策
"""

import json
import re

from lib.execution import log_hook_execution
from lib.input_context import extract_input_context, merge_details_with_context
from lib.results import check_skip_env, make_block_result
from lib.session import create_hook_context, parse_hook_input
from lib.strings import strip_quoted_strings

HOOK_NAME = "branch-rename-guard"

# Pattern to match git global options that can appear between 'git' and the subcommand
# Examples: -C <path>, -C<path>, --git-dir=<path>, --git-dir <path>, -c <key>=<value>
# See: https://git-scm.com/docs/git#_options
# Borrowed from checkout-block.py for consistency
GIT_GLOBAL_OPTIONS = (
    r"(?:\s+(?:-[CcOo]\s*\S+|--[\w-]+=\S+|--[\w-]+\s+(?!branch\b)\S+|--[\w-]+|-[pPhv]|-\d+))*"
)

# Pattern to match branch options that can appear between 'branch' and '-m/-M/--move'
# Examples: --color, --no-color, --list, -v, -vv, -f, --force, --color=always, --sort=-date
# Supports options with or without values (--opt, --opt=value)
# Note: -f/--force is included for cases like 'git branch -f -m' (separate args)
# Issue #996 Codex review: Extended to handle options with =value to prevent bypass
BRANCH_OPTIONS = r"(?:\s+(?:--[\w-]+=\S*|--[\w-]+|-[vVqarlf]+))*"

# Pattern to match the rename flag itself
# Supports:
# - '-m', '-M' (simple case)
# - '-fm', '-fM', '-afm' (combined flags like 'git branch -fm old new')
# - '--move' (long form)
# Issue #996 review: Added support for combined flags to prevent bypass
# Note: This also matches invalid combinations like '-dm' (delete+move).
# This is intentional - blocking an invalid command is harmless since
# git itself would reject it anyway.
RENAME_FLAG = r"(?:-[a-zA-Z]*[mM]|--move)"


def check_branch_rename(command: str) -> tuple[bool, str | None]:
    """コマンドがブランチリネームを含むか確認する。

    Args:
        command: チェックするコマンド

    Returns:
        (is_rename, target_branch) のタプル。
        is_rename: リネームコマンドならTrue
        target_branch: リネーム対象のブランチ名（検出できた場合）
    """
    # 引用符内のテキストを除去
    stripped = strip_quoted_strings(command)

    # git branch -m/-M パターンを検出
    # -m: rename, -M: force rename
    # グローバルオプション（-C, --git-dir等）にも対応
    # ブランチオプション（--color, -v等）が-m/-Mの前にあっても検出
    # RENAME_FLAGで結合フラグ（-fm等）もサポート
    patterns = [
        rf"\bgit{GIT_GLOBAL_OPTIONS}\s+branch{BRANCH_OPTIONS}\s+{RENAME_FLAG}",
    ]

    for pattern in patterns:
        if re.search(pattern, stripped):
            # リネーム対象のブランチ名を抽出（可能な場合）
            # git branch -m old-name new-name
            # git branch -M new-name (現在のブランチをリネーム)
            # git branch -fm old-name new-name (結合フラグ)
            match = re.search(
                rf"\bgit{GIT_GLOBAL_OPTIONS}\s+branch{BRANCH_OPTIONS}\s+{RENAME_FLAG}\s+(\S+)",
                stripped,
            )
            target = match.group(1) if match else None
            return True, target

    return False, None


def main() -> None:
    """ブランチリネームコマンドをブロックする。"""
    hook_input = parse_hook_input()
    input_context = extract_input_context(hook_input)

    # Issue #2456: HookContext DI移行
    ctx = create_hook_context(hook_input)

    # Bashコマンドのみチェック
    if hook_input.get("tool_name") != "Bash":
        result = {"decision": "approve"}
        print(json.dumps(result))
        return

    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "")

    # スキップ環境変数チェック
    # Issue #1260: Use check_skip_env for centralized logging
    # Pass input_context (input_preview already captures command preview)
    if check_skip_env(HOOK_NAME, "SKIP_BRANCH_RENAME_GUARD", input_context):
        result = {"decision": "approve"}
        print(json.dumps(result))
        return

    is_rename, target_branch = check_branch_rename(command)

    if not is_rename:
        log_hook_execution(
            HOOK_NAME,
            "approve",
            "Not a branch rename command",
            merge_details_with_context(None, input_context),
        )
        result = {"decision": "approve"}
        print(json.dumps(result))
        return

    # ブロック
    target_info = f"（対象: {target_branch}）" if target_branch else ""
    reason = f"""ブランチリネーム操作をブロックしました{target_info}

**理由:**
ブランチリネームは以下の問題を引き起こす可能性があります:
- main/masterのリネームによるgit設定の破損
- リモートとの不整合
- 他のセッションやCIとの競合

**意図的にリネームする場合:**
```bash
SKIP_BRANCH_RENAME_GUARD=1 {command}
```

詳細: Issue #996"""

    log_hook_execution(
        HOOK_NAME,
        "block",
        f"Branch rename blocked: {target_branch or 'unknown'}",
        merge_details_with_context({"target_branch": target_branch}, input_context),
        session_id=ctx.get_session_id(),
    )

    result = make_block_result(HOOK_NAME, reason, ctx=ctx)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
