#!/usr/bin/env python3
"""危険な`git push --force`をブロックし、`--force-with-lease`を推奨する。

Why:
    `git push --force`はリモートの状態を確認せずに上書きするため、
    他の変更を消失させる危険がある。`--force-with-lease`は安全に
    force pushできるため、こちらを推奨する。

What:
    - git push --force または -f を検出
    - 検出時はブロックし、--force-with-leaseを推奨
    - --force-with-leaseは許可

Remarks:
    - コマンドチェーン内の各サブコマンドを個別にチェック
    - クォート内の文字列は無視（echo "git push --force"等）

Changelog:
    - silenvx/dekita#941: コマンドチェーン対応
"""

import json
import re
import sys

from lib.execution import log_hook_execution
from lib.results import make_approve_result, make_block_result
from lib.session import parse_hook_input
from lib.strings import split_command_chain, strip_quoted_strings


def _is_dangerous_force_push_single(subcommand: str) -> bool:
    """Check if a single command (not a chain) is a dangerous git push --force.

    This function should only be called with individual commands,
    not command chains (those should be split first).
    """
    if not subcommand.strip():
        return False

    # Strip quoted strings to avoid false positives
    stripped = strip_quoted_strings(subcommand)

    # Must be a git push command
    if not re.search(r"\bgit\s+push\b", stripped):
        return False

    # Check for dangerous --force or -f first (before checking --force-with-lease)
    # Match --force that is NOT part of --force-with-lease
    # Note: --force-with-lease does NOT match this regex because of negative lookahead
    if re.search(r"--force\b(?!-with-lease)", stripped):
        return True

    # Match -f flag, including combined short flags like -uf or -fu
    # Git allows combining short flags: -u -f can be written as -uf or -fu
    # Pattern matches: -f, -uf, -fu, -auf, etc.
    # IMPORTANT: Must NOT match --follow-tags, --filter, etc. (single dash only)
    if re.search(r"(?:^|\s)-(?!-)[a-z]*f[a-z]*(?:\s|$)", stripped):
        return True

    # If we reach here, either no force flags or only --force-with-lease
    return False


def is_dangerous_force_push(command: str) -> bool:
    """Check if command is a dangerous git push --force (without --force-with-lease).

    Issue #941: Handles command chains (&&, ||, ;) by checking each subcommand individually.
    This prevents false positives when --force is used in a different command in the chain.

    Returns True for:
    - git push --force
    - git push -f
    - git push -uf (combined flags containing f)
    - git push origin branch --force
    - git push --force origin branch
    - git push --force-with-lease --force (--force takes precedence)

    Returns False for:
    - git push --force-with-lease (only)
    - git push (normal push)
    - git push -u (no f flag)
    - Commands inside quoted strings (e.g., echo "git push --force")
    - git worktree remove --force && git push (--force belongs to different command)
    """
    if not command.strip():
        return False

    # First, strip quoted strings to avoid splitting on operators inside quotes
    # e.g., 'echo "backup && git push --force"' should not be split
    stripped_for_split = strip_quoted_strings(command)

    # Split the stripped command chain
    subcommands = split_command_chain(stripped_for_split)

    for subcommand in subcommands:
        if _is_dangerous_force_push_single(subcommand):
            return True

    return False


def main():
    """
    PreToolUse hook for Bash commands.

    Blocks `git push --force` and recommends `--force-with-lease`.
    """
    try:
        data = parse_hook_input()
        tool_input = data.get("tool_input", {})
        command = tool_input.get("command", "")

        if is_dangerous_force_push(command):
            reason = (
                "`git push --force`は危険です。他の変更を上書きする可能性があります。\n\n"
                "**代わりに以下の安全な方法を使用してください:**\n\n"
                "```bash\n"
                "# 1. リモートの状態を取得\n"
                "git fetch origin\n\n"
                "# 2. リモートの変更を確認（origin/<ブランチ名> を指定）\n"
                "git log --oneline origin/<ブランチ名> -5\n\n"
                "# 3. 必要ならリベース（origin/<ブランチ名> を指定）\n"
                "git rebase origin/<ブランチ名>\n\n"
                "# 4. 安全なforce push\n"
                "git push --force-with-lease\n"
                "```\n\n"
                "`--force-with-lease`はリモートが予期した状態であることを確認してからプッシュします。\n"
                "これにより、他の人の作業を誤って上書きすることを防げます。"
            )
            result = make_block_result("force-push-guard", reason)
            log_hook_execution("force-push-guard", "block", "dangerous force push detected")
            print(json.dumps(result))
            sys.exit(0)

        result = make_approve_result("force-push-guard")

    except Exception as e:
        print(f"[force-push-guard] Hook error: {e}", file=sys.stderr)
        result = make_approve_result("force-push-guard", f"Hook error: {e}")

    log_hook_execution("force-push-guard", result.get("decision", "approve"))
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
