#!/usr/bin/env python3
"""mainリポジトリでのブランチ操作をブロックする。

Why:
    mainリポジトリで直接ブランチ操作を行うと、worktreeワークフローをバイパスし、
    他のセッションとの競合やクリーンな環境維持が困難になる。

What:
    - git checkout/switchでmain/master/develop以外へのcheckoutをブロック
    - git branchで新規ブランチ作成をブロック
    - worktree作成手順を提示

Remarks:
    - ブロック型フック（worktreeワークフロー強制）
    - worktree内では発火しない（cwdが.worktrees/内ならスキップ）
    - PreToolUse:Bashで発火

Changelog:
    - silenvx/dekita#891: mainリポジトリでの作業ブランチチェックアウトをブロック
    - silenvx/dekita#905: gitグローバルオプションによるバイパス対策
    - silenvx/dekita#1357: main以外の全ブランチへの切り替え・作成をブロック
    - silenvx/dekita#2427: シェル演算子をブランチ名として誤認識する問題を修正
"""

import os
import re
import subprocess

from lib.constants import TIMEOUT_LIGHT
from lib.execution import log_hook_execution
from lib.results import make_approve_result, make_block_result
from lib.session import create_hook_context, parse_hook_input

# Pattern to match git global options that can appear between 'git' and the subcommand
# Examples: -C <path>, -C<path>, --git-dir=<path>, --git-dir <path>, -c <key>=<value>
# Handles various forms:
# - Short options with space: -C <path>, -c <key>=<value>
# - Short options without space: -C<path>, -c<key>=<value>
# - Long options with =: --git-dir=<path>
# - Long options with space: --git-dir <path>
# - Flag-only options: --no-pager, --bare, --paginate, -p, etc.
# See: https://git-scm.com/docs/git#_options
# Order matters: check value-taking options first, then flag-only options
GIT_GLOBAL_OPTIONS = r"(?:\s+(?:-[CcOo]\s*\S+|--[\w-]+=\S+|--[\w-]+\s+(?!checkout\b|switch\b)\S+|--[\w-]+|-[pPhv]|-\d+))*"

# Allowed branches that can be checked out in main repository
ALLOWED_BRANCHES = ("main", "develop", "master")


def is_in_worktree() -> bool:
    """Check if current directory is inside a worktree."""
    cwd = os.getcwd()
    return "/.worktrees/" in cwd or cwd.endswith("/.worktrees")


def is_main_repository() -> bool:
    """Check if current directory is in the main repository (not a worktree)."""
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
                first_line = lines[0]
                if first_line.startswith("worktree "):
                    main_repo_path = first_line[9:]
                    cwd = os.getcwd()
                    real_cwd = os.path.realpath(cwd)
                    real_main = os.path.realpath(main_repo_path)
                    return real_cwd == real_main or real_cwd.startswith(real_main + "/")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # git コマンドが利用できない、またはタイムアウトした場合は
        # main リポジトリかどうか判定できないため、安全側として False を返す
        pass
    return False


def extract_checkout_target(command: str) -> str | None:
    """Extract the target branch from a git checkout/switch command.

    Args:
        command: The full command string

    Returns:
        The target branch name if found, None otherwise
    """
    # Pattern for git checkout <branch>
    # Handles: git checkout branch, git checkout -b branch, git checkout -t/-T/--track origin/branch
    # Also handles combined flags: -bt, -tb, -Bt, -bT, etc.
    # Issue #905: Also handles git global options (e.g., git -C . checkout branch)
    checkout_pattern = re.compile(
        rf"git{GIT_GLOBAL_OPTIONS}\s+checkout\s+(?:-[bBtT]{{1,2}}\s+|--track\s+)?(?:origin/)?(\S+)"
    )

    # Pattern for git switch <branch>
    # Handles: git switch branch, git switch -c branch, git switch --create branch, git switch -t/--track
    # Also handles combined flags: -ct, -tc, -Ct, -cT, etc.
    # Issue #905: Also handles git global options (e.g., git --git-dir=.git switch branch)
    switch_pattern = re.compile(
        rf"git{GIT_GLOBAL_OPTIONS}\s+switch\s+(?:-[cCtT]{{1,2}}\s+|--create\s+)?(?:origin/)?(\S+)"
    )

    # Try checkout pattern first
    match = checkout_pattern.search(command)
    if match:
        target = match.group(1)
        # Skip if it's an option (starts with -)
        if not target.startswith("-"):
            return target

    # Try switch pattern
    match = switch_pattern.search(command)
    if match:
        target = match.group(1)
        if not target.startswith("-"):
            return target

    return None


def extract_branch_create_target(command: str) -> str | None:
    """Extract the target branch from a git branch create command.

    Args:
        command: The full command string

    Returns:
        The target branch name if creating a new branch, None otherwise

    Examples:
        >>> extract_branch_create_target("git branch new-feature")
        'new-feature'
        >>> extract_branch_create_target("git branch -d old-branch")
        >>> extract_branch_create_target("git branch --list")
        >>> extract_branch_create_target("git branch -m old new")
        >>> extract_branch_create_target("git -C /path branch new-feature")
        'new-feature'
        >>> extract_branch_create_target("git branch && echo done")
        >>> extract_branch_create_target("pwd && git branch && git log")
        >>> extract_branch_create_target("git branch")
    """
    # Pattern for git branch <name> (new branch creation)
    # Excludes: -d/-D (delete), -m/-M (move/rename), -l/--list, -a/--all, -r/--remotes
    # Also excludes -v/--verbose, --show-current, --contains, --merged, --no-merged
    # Issue #1357: Block new branch creation in main repository
    # Issue #2427: Exclude shell operators (&&, ||, ;, |, >, >>, <, <<, &) from being captured as branch names
    branch_pattern = re.compile(
        rf"git{GIT_GLOBAL_OPTIONS}\s+branch\s+"
        r"(?!-[dDmMlarvV]|--delete|--move|--list|--all|--remotes|"
        r"--verbose|--show-current|--contains|--merged|--no-merged)"
        r"(\S+)"
    )

    match = branch_pattern.search(command)
    if match:
        target = match.group(1)
        # Skip if it's an option (starts with -)
        if target.startswith("-"):
            return None
        # Skip shell operators (Issue #2427)
        if target in ("&&", "||", ";", "|", ">", ">>", "<", "<<", "&"):
            return None
        return target

    return None


def is_allowed_branch(branch: str) -> bool:
    """Check if the branch is allowed to be checked out in main repository."""
    return branch in ALLOWED_BRANCHES


# Hook name for logging and block messages
HOOK_NAME = "checkout-block"


def _output_result(result: dict) -> None:
    """Output hook result as JSON."""
    import json

    print(json.dumps(result))


def _make_block_reason(target_branch: str, operation: str) -> str:
    """Generate block reason message.

    Args:
        target_branch: The target branch name
        operation: The operation type (checkout/switch/branch)

    Returns:
        Formatted block reason message
    """
    return f"""mainリポジトリでのブランチ操作がブロックされました。

操作: git {operation}
ターゲットブランチ: {target_branch}

worktreeを使用してください:

  git worktree add .worktrees/<issue-name> -b {target_branch} main

理由:
- mainリポジトリで直接作業すると、他のセッションとの競合リスク
- 作業状態の追跡が困難
- クリーンな環境維持が難しい

許可されているブランチ: {", ".join(ALLOWED_BRANCHES)}"""


def main() -> None:
    """Main entry point for the hook."""
    data = parse_hook_input()
    if not data:
        # If we can't parse input, approve (fail open)
        log_hook_execution(HOOK_NAME, "approve", reason="parse_error")
        _output_result(make_approve_result(HOOK_NAME))
        return

    # Issue #2456: HookContext DI移行
    ctx = create_hook_context(data)

    tool_input = data.get("tool_input", {})
    command = tool_input.get("command", "")

    # Check git checkout/switch/branch commands
    # Issue #905: Use regex to handle git global options (e.g., git -C . checkout)
    # Issue #1357: Also check git branch for new branch creation
    # Require whitespace before subcommand to avoid matching config keys, file names, etc.
    if not re.search(r"\bgit\b.*\s+(?:checkout|switch|branch)(?:\s+|$)", command):
        _output_result(make_approve_result(HOOK_NAME))
        return

    # If in worktree, allow all operations
    if is_in_worktree():
        log_hook_execution(HOOK_NAME, "approve", reason="in_worktree")
        _output_result(make_approve_result(HOOK_NAME))
        return

    # If not in main repository, allow
    if not is_main_repository():
        log_hook_execution(HOOK_NAME, "approve", reason="not_main_repository")
        _output_result(make_approve_result(HOOK_NAME))
        return

    # Check for git branch create command first
    branch_target = extract_branch_create_target(command)
    if branch_target:
        # Block new branch creation in main repository
        reason = _make_block_reason(branch_target, "branch")
        log_hook_execution(
            HOOK_NAME,
            "block",
            reason="branch_create",
            details={"branch": branch_target},
            session_id=ctx.get_session_id(),
        )
        _output_result(make_block_result(HOOK_NAME, reason, ctx=ctx))
        return

    # Extract target branch for checkout/switch
    target_branch = extract_checkout_target(command)
    if not target_branch:
        # Can't determine target, approve (fail open)
        log_hook_execution(HOOK_NAME, "approve", reason="unknown_target")
        _output_result(make_approve_result(HOOK_NAME))
        return

    # Allow checkout to allowed branches (main, develop, etc.)
    if is_allowed_branch(target_branch):
        log_hook_execution(
            HOOK_NAME, "approve", reason="allowed_branch", details={"branch": target_branch}
        )
        _output_result(make_approve_result(HOOK_NAME))
        return

    # Block checkout/switch to any non-allowed branch
    # Issue #1357: Block all branches except main/master/develop
    reason = _make_block_reason(target_branch, "checkout/switch")
    log_hook_execution(
        HOOK_NAME,
        "block",
        reason="non_allowed_branch",
        details={"branch": target_branch},
        session_id=ctx.get_session_id(),
    )
    _output_result(make_block_result(HOOK_NAME, reason, ctx=ctx))


if __name__ == "__main__":
    main()
