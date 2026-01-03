#!/usr/bin/env python3
"""Git command parser.

This module provides structured parsing of git commands
for detailed logging and analysis of development workflows.

Issue #2411: Extracted from command_parser.py for better modularity.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

from ..command_utils import (
    COMMAND_WRAPPERS,
    ends_with_shell_separator,
    get_command_name,
    is_command_wrapper,
)

# Issue #1693: Common pattern for extracting conflict files from git output
CONFLICT_FILE_PATTERN = re.compile(r"CONFLICT.*?:\s*(?:Merge conflict in\s+)?(\S+)")


def extract_conflict_info(combined: str, result: dict) -> None:
    """Extract conflict information from command output.

    Issue #1750: Shared helper for rebase/merge conflict detection.

    Args:
        combined: Combined stdout and stderr output
        result: Result dictionary to update with conflict information
    """
    if "CONFLICT" in combined:
        result["conflict_detected"] = True
        conflict_files = CONFLICT_FILE_PATTERN.findall(combined)
        if conflict_files:
            result["conflict_files"] = conflict_files


def parse_git_command(command: str) -> dict[str, Any] | None:
    """Parse git command to extract structured data.

    Args:
        command: The full command string

    Returns:
        Parsed command data with type, operation, and extracted arguments
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    if not tokens:
        return None

    # Find 'git' command (Issue #1258: handle absolute paths like /usr/bin/git)
    # Issue #1258: Skip false positives where 'git' is an argument path
    # Must be in command position: start of line, after shell operator, or after env var
    git_start = None
    git_tokens = []
    search_start = 0
    shell_operators = ("|", ";", "&&", "||")

    while search_start < len(tokens):
        # Find next token whose basename is 'git' AND is in command position
        git_start = None
        for i in range(search_start, len(tokens)):
            if get_command_name(tokens[i]) != "git":
                continue
            # Check if in valid command position
            if i == 0:
                git_start = i
                break
            prev_token = tokens[i - 1]
            if prev_token in shell_operators or ends_with_shell_separator(prev_token):
                git_start = i
                break
            if "=" in prev_token and not prev_token.startswith("-"):
                git_start = i
                break
            if is_command_wrapper(prev_token):
                git_start = i
                break

        if git_start is None:
            return None

        # Extract tokens after 'git' until we hit a separator
        git_tokens = []
        for token in tokens[git_start + 1 :]:
            if token in shell_operators or ends_with_shell_separator(token):
                break
            git_tokens.append(token)

        if git_tokens:
            break
        search_start = git_start + 1
        git_start = None

    if git_start is None or not git_tokens:
        return None

    # Skip global flags to find subcommand
    i = 0
    flags_with_args = {"-C", "-c", "--git-dir", "--work-tree"}
    while i < len(git_tokens):
        token = git_tokens[i]
        if token.startswith("-"):
            if "=" in token:
                i += 1
            elif token in flags_with_args:
                i += 2 if i + 1 < len(git_tokens) else i + 1
            else:
                i += 1
        else:
            break

    if i >= len(git_tokens):
        return None

    subcommand = git_tokens[i]

    result: dict[str, Any] = {
        "type": "git",
        "operation": subcommand,
        "subcommand": subcommand,
        "args": {},
    }

    # Handle specific subcommands
    if subcommand == "push":
        result = _parse_git_push(git_tokens, i, result)
    elif subcommand == "pull":
        result = _parse_git_pull(git_tokens, i, result)
    elif subcommand == "commit":
        result = _parse_git_commit(git_tokens, i, result)
    elif subcommand == "worktree":
        result = _parse_git_worktree(git_tokens, i, result)
    elif subcommand in ("checkout", "switch"):
        result = _parse_git_checkout(git_tokens, i, result)
    elif subcommand == "merge":
        result = _parse_git_merge(git_tokens, i, result)
    elif subcommand == "rebase":
        result = _parse_git_rebase(git_tokens, i, result)

    return result


def _parse_git_push(tokens: list[str], cmd_index: int, result: dict[str, Any]) -> dict[str, Any]:
    """Parse git push command."""
    j = cmd_index + 1
    positional = []

    while j < len(tokens):
        token = tokens[j]
        if token.startswith("-") and not token.startswith("--"):
            # Handle combined short flags like -uf, -fu
            if "u" in token:
                result["args"]["set_upstream"] = True
            if "f" in token:
                result["args"]["force"] = True
            j += 1
        elif token.startswith("--"):
            if token == "--set-upstream":
                result["args"]["set_upstream"] = True
            elif token == "--force":
                result["args"]["force"] = True
            elif token == "--force-with-lease" or token.startswith("--force-with-lease="):
                result["args"]["force_with_lease"] = True
            j += 1
        else:
            positional.append(token)
            j += 1

    if positional:
        result["remote"] = positional[0]
    if len(positional) > 1:
        result["branch"] = positional[1]

    return result


def _parse_git_pull(tokens: list[str], cmd_index: int, result: dict[str, Any]) -> dict[str, Any]:
    """Parse git pull command."""
    j = cmd_index + 1
    positional = []

    while j < len(tokens):
        token = tokens[j]
        if token.startswith("-"):
            if token == "--rebase":
                result["args"]["rebase"] = True
            j += 1
        else:
            positional.append(token)
            j += 1

    if positional:
        result["remote"] = positional[0]
    if len(positional) > 1:
        result["branch"] = positional[1]

    return result


def _parse_git_commit(tokens: list[str], cmd_index: int, result: dict[str, Any]) -> dict[str, Any]:
    """Parse git commit command."""
    j = cmd_index + 1

    while j < len(tokens):
        token = tokens[j]
        if token in ("-m", "--message") and j + 1 < len(tokens):
            result["message"] = tokens[j + 1][:100]  # Truncate for logging
            j += 2
        elif token == "--amend":
            result["args"]["amend"] = True
            j += 1
        elif token == "--no-verify":
            result["args"]["no_verify"] = True
            j += 1
        else:
            j += 1

    return result


def _parse_git_worktree(
    tokens: list[str], cmd_index: int, result: dict[str, Any]
) -> dict[str, Any]:
    """Parse git worktree command."""
    if cmd_index + 1 >= len(tokens):
        return result

    subsubcmd = tokens[cmd_index + 1]
    result["operation"] = f"worktree_{subsubcmd}"
    result["worktree_action"] = subsubcmd

    # Find path for add/remove
    j = cmd_index + 2
    positional = []
    while j < len(tokens):
        token = tokens[j]
        if not token.startswith("-"):
            positional.append(token)
        j += 1

    if positional:
        result["path"] = positional[0]
    if len(positional) > 1 and subsubcmd == "add":
        result["branch"] = positional[1]

    return result


def _parse_git_checkout(
    tokens: list[str], cmd_index: int, result: dict[str, Any]
) -> dict[str, Any]:
    """Parse git checkout/switch command."""
    j = cmd_index + 1
    positional = []

    while j < len(tokens):
        token = tokens[j]
        if token in ("-b", "-B", "-c"):
            result["args"]["create_branch"] = True
            j += 1
        elif token.startswith("-"):
            j += 1
        else:
            positional.append(token)
            j += 1

    if positional:
        result["target"] = positional[0]

    return result


def _parse_git_merge(tokens: list[str], cmd_index: int, result: dict[str, Any]) -> dict[str, Any]:
    """Parse git merge command."""
    j = cmd_index + 1
    positional = []

    while j < len(tokens):
        token = tokens[j]
        if token == "--no-ff":
            result["args"]["no_ff"] = True
        elif token == "--squash":
            result["args"]["squash"] = True
        elif not token.startswith("-"):
            positional.append(token)
        j += 1

    if positional:
        result["source"] = positional[0]

    return result


def _parse_git_rebase(tokens: list[str], cmd_index: int, result: dict[str, Any]) -> dict[str, Any]:
    """Parse git rebase command."""
    j = cmd_index + 1
    positional = []

    while j < len(tokens):
        token = tokens[j]
        if token == "--continue":
            result["args"]["continue"] = True
        elif token == "--abort":
            result["args"]["abort"] = True
        elif not token.startswith("-"):
            positional.append(token)
        j += 1

    if positional:
        result["onto"] = positional[0]

    return result


def extract_worktree_add_path(command: str) -> str | None:
    """Extract worktree path from git worktree add command.

    This is the unified implementation for extracting path from git worktree add.
    It handles various edge cases including:
    - Options with arguments (-b, -B, --orphan, --reason)
    - Options without arguments (-d, --detach, -f, --force, --lock, -q, --quiet, etc.)
    - Chained commands (cd /path && git worktree add .worktrees/foo)
    - Environment variable prefixes (SKIP_PLAN=1 git worktree add ...)
    - Quoted paths with spaces
    - Git global options (git -C /path worktree add ...)

    Issue #1543: Consolidated from worktree-creation-marker.py and worktree-path-guard.py.
    Issue #1629: Support git -C <path> and other global options between git and worktree.

    Args:
        command: The full command string

    Returns:
        The worktree path argument or None if not found

    Examples:
        >>> extract_worktree_add_path("git worktree add .worktrees/foo main")
        '.worktrees/foo'
        >>> extract_worktree_add_path("git worktree add -b new-branch /tmp/foo")
        '/tmp/foo'
        >>> extract_worktree_add_path("git -C /path worktree add .worktrees/foo")
        '.worktrees/foo'
        >>> extract_worktree_add_path("git worktree list") is None
        True
    """
    from ..strings import strip_quoted_strings

    # Strip quoted strings to avoid false positives from heredocs/echo
    stripped = strip_quoted_strings(command)

    # Check if this is a git worktree add command
    # Issue #1629: Allow global options between 'git' and 'worktree'
    # Matches: git worktree add, git -C /path worktree add, git --git-dir=x worktree add
    # Note: Restrict match to a single shell command (do not cross ;, &&, ||, |)
    #
    # Issue #1655: Ensure git is at command position (not as argument to echo/printf/etc.)
    # Match patterns where git is a command:
    # - Start of string (with optional leading whitespace): ^\s*git
    # - After shell separators: ; && || | followed by optional whitespace
    # - After subshell/grouping: ( followed by optional whitespace
    # - After env var assignments: VAR=value (can be multiple)
    # - After common wrappers: sudo, env, time, command, etc. (can be chained)
    # - Absolute paths like /usr/bin/git (Issue #1707)
    # Exclude: echo git ..., printf git ..., etc. (git as argument)
    #
    # Use COMMAND_WRAPPERS for consistency with other parsing functions
    wrappers_pattern = "|".join(COMMAND_WRAPPERS)
    # Allow optional path prefix for absolute paths (e.g., /usr/bin/git, /usr/bin/env)
    path_prefix = r"(?:[\w/.-]*/)?"
    if not re.search(
        rf"(?:^\s*|[;&|()]\s*)(?:(?:\w+=\S*)\s+)*(?:{path_prefix}(?:{wrappers_pattern})\s+)*{path_prefix}git\b[^;&|]*\bworktree\s+add\b",
        stripped,
    ):
        return None

    try:
        tokens = shlex.split(command)
    except ValueError:
        # Fall back to simple split if shlex fails (unclosed quotes, etc.)
        tokens = command.split()

    # Shell command separators
    shell_separators = {"&&", "||", ";", "|"}

    # Find "git", "worktree", and "add" positions, ensuring they're in the same command
    # Issue #1629: git and worktree must be in the same command (no separator between them)
    git_idx = None
    worktree_idx = None
    add_idx = None

    for i, token in enumerate(tokens):
        # Reset when we hit a shell separator
        # Note: shlex doesn't split on unquoted `;` (e.g., `git;echo` becomes one token)
        # Use ends_with_shell_separator to catch tokens like 'foo;' without matching
        # paths containing semicolons (e.g., '/path;backup') - Issue #1669
        if token in shell_separators or ends_with_shell_separator(token):
            git_idx = None
            worktree_idx = None
            add_idx = None
            continue

        # Find git command (handle absolute paths like /usr/bin/git)
        if token == "git" or token.endswith("/git"):
            git_idx = i
            worktree_idx = None  # Reset to handle new git command in chain
            add_idx = None
        # Find worktree after git
        elif token == "worktree" and git_idx is not None:
            worktree_idx = i
        # Find add after worktree
        elif token == "add" and worktree_idx is not None:
            add_idx = i
            break

    if add_idx is None:
        return None

    # Options that take an argument
    options_with_arg = {"-b", "-B", "--orphan", "--reason"}
    # Options without argument
    options_no_arg = {
        "-f",
        "--force",
        "-d",
        "--detach",
        "--checkout",
        "--no-checkout",
        "--lock",
        "-q",
        "--quiet",
        "--track",
        "--no-track",
        "--guess-remote",
        "--no-guess-remote",
    }

    # Find the path argument (skip options)
    i = add_idx + 1
    while i < len(tokens):
        token = tokens[i]
        if token.startswith("-"):
            if token in options_with_arg:
                # Skip this option and its argument
                i += 2
            elif token in options_no_arg or token.startswith("--"):
                # Skip this option (including unknown --options)
                i += 1
            else:
                # Unknown short option, skip
                i += 1
        else:
            # This should be the path
            return token
        if i >= len(tokens):
            break

    return None
