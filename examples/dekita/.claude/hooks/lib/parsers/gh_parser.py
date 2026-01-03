#!/usr/bin/env python3
"""GitHub CLI command parser.

This module provides structured parsing of gh CLI commands
for detailed logging and analysis of development workflows.

Issue #2411: Extracted from command_parser.py for better modularity.
"""

from __future__ import annotations

import shlex
from typing import Any

from ..command_utils import (
    ends_with_shell_separator,
    get_command_name,
    is_command_wrapper,
)
from ..github import parse_gh_pr_command


def parse_gh_command(command: str) -> dict[str, Any] | None:
    """Parse gh CLI command to extract structured data.

    Handles gh pr, gh issue, gh api, and other gh subcommands.

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

    # Find 'gh' command (Issue #1258: handle absolute paths like /usr/bin/gh)
    # Issue #1258: Skip false positives where 'gh' is an argument path
    # (e.g., `test -x /usr/bin/gh && gh pr list` - skip the first /usr/bin/gh)
    # Must be in command position: start of line, after shell operator, or after env var
    gh_start = None
    gh_tokens = []
    search_start = 0
    shell_operators = ("|", ";", "&&", "||")

    while search_start < len(tokens):
        # Find next token whose basename is 'gh' AND is in command position
        gh_start = None
        for i in range(search_start, len(tokens)):
            if get_command_name(tokens[i]) != "gh":
                continue
            # Check if in valid command position
            if i == 0:
                gh_start = i
                break
            prev_token = tokens[i - 1]
            # After shell operator (including tokens ending with separator)
            if prev_token in shell_operators or ends_with_shell_separator(prev_token):
                gh_start = i
                break
            # After env var assignment (VAR=value)
            if "=" in prev_token and not prev_token.startswith("-"):
                gh_start = i
                break
            # After command wrapper (sudo, time, etc.)
            if is_command_wrapper(prev_token):
                gh_start = i
                break
            # Not in command position, continue searching

        if gh_start is None:
            # No more valid matches found
            return None

        # Extract tokens after 'gh' until we hit a separator
        gh_tokens = []
        for token in tokens[gh_start + 1 :]:
            if token in shell_operators or ends_with_shell_separator(token):
                break
            gh_tokens.append(token)

        # If gh_tokens is non-empty, this is the real gh command
        # If empty, continue searching (gh might be the last token before &&)
        if gh_tokens:
            break
        search_start = gh_start + 1
        gh_start = None

    if gh_start is None or not gh_tokens:
        return None

    # Skip global flags to find subcommand
    i = 0
    flags_with_args = {"--repo", "-R", "--hostname", "--config"}
    while i < len(gh_tokens):
        token = gh_tokens[i]
        if token.startswith("-"):
            if "=" in token:
                i += 1
            elif token in flags_with_args:
                i += 2 if i + 1 < len(gh_tokens) else i + 1
            else:
                i += 1
        else:
            break

    if i >= len(gh_tokens):
        return None

    main_command = gh_tokens[i]

    # Handle different gh subcommands
    if main_command == "pr":
        return _parse_gh_pr(command, gh_tokens, i)
    elif main_command == "issue":
        return _parse_gh_issue(command, gh_tokens, i)
    elif main_command == "api":
        return _parse_gh_api(command, gh_tokens, i)
    elif main_command == "run":
        return _parse_gh_run(command, gh_tokens, i)
    elif main_command == "auth":
        return _parse_gh_auth(command, gh_tokens, i)
    else:
        # Generic gh command
        subcommand = gh_tokens[i + 1] if i + 1 < len(gh_tokens) else None
        return {
            "type": "gh",
            "operation": f"{main_command}_{subcommand}" if subcommand else main_command,
            "main_command": main_command,
            "subcommand": subcommand,
            "args": {},
        }


def _parse_gh_pr(command: str, gh_tokens: list[str], pr_index: int) -> dict[str, Any] | None:
    """Parse gh pr command."""
    # Use existing parser for subcommand and PR number
    subcommand, pr_number = parse_gh_pr_command(command)

    if not subcommand:
        return None

    result: dict[str, Any] = {
        "type": "gh",
        "operation": f"pr_{subcommand}",
        "main_command": "pr",
        "subcommand": subcommand,
        "args": {},
    }

    if pr_number:
        result["pr_number"] = int(pr_number)

    # Extract common flags
    args = _extract_gh_args(gh_tokens, pr_index + 2)
    result["args"] = args

    return result


def _parse_gh_issue(command: str, gh_tokens: list[str], issue_index: int) -> dict[str, Any] | None:
    """Parse gh issue command."""
    if issue_index + 1 >= len(gh_tokens):
        return None

    subcommand = gh_tokens[issue_index + 1]

    result: dict[str, Any] = {
        "type": "gh",
        "operation": f"issue_{subcommand}",
        "main_command": "issue",
        "subcommand": subcommand,
        "args": {},
    }

    # Find issue number
    j = issue_index + 2
    while j < len(gh_tokens):
        token = gh_tokens[j]
        if token.startswith("-"):
            if j + 1 < len(gh_tokens) and not gh_tokens[j + 1].startswith("-"):
                j += 2
            else:
                j += 1
            continue
        if token.isdigit():
            result["issue_number"] = int(token)
            break
        if token.startswith("#") and len(token) > 1 and token[1:].isdigit():
            result["issue_number"] = int(token[1:])
            break
        j += 1

    # Extract common flags
    args = _extract_gh_args(gh_tokens, issue_index + 2)
    result["args"] = args

    return result


def _parse_gh_api(command: str, gh_tokens: list[str], api_index: int) -> dict[str, Any] | None:
    """Parse gh api command.

    Issue #1269: Added api_type field to distinguish GraphQL vs REST API calls.
    This enables fallback pattern analysis (GraphQL → REST on rate limit).
    """
    endpoint = None

    # Find endpoint (first non-flag argument after 'api')
    j = api_index + 1
    while j < len(gh_tokens):
        token = gh_tokens[j]
        if token.startswith("-"):
            if j + 1 < len(gh_tokens) and not gh_tokens[j + 1].startswith("-"):
                j += 2
            else:
                j += 1
            continue
        endpoint = token
        break

    result: dict[str, Any] = {
        "type": "gh",
        "operation": "api",
        "main_command": "api",
        "args": {},
    }

    if endpoint:
        result["endpoint"] = endpoint
        # Issue #1269: Detect GraphQL vs REST API type
        # GraphQL endpoint is "graphql", everything else is REST
        result["api_type"] = "graphql" if endpoint == "graphql" else "rest"
    else:
        # Issue #1269: Default to "unknown" when endpoint is not specified
        # (e.g., "gh api -X GET" without endpoint)
        result["api_type"] = "unknown"

    # Extract method if specified
    for k, token in enumerate(gh_tokens):
        if token in ("-X", "--method") and k + 1 < len(gh_tokens):
            result["method"] = gh_tokens[k + 1]
            break

    return result


def _parse_gh_run(command: str, gh_tokens: list[str], run_index: int) -> dict[str, Any] | None:
    """Parse gh run command."""
    if run_index + 1 >= len(gh_tokens):
        return None

    subcommand = gh_tokens[run_index + 1]

    result: dict[str, Any] = {
        "type": "gh",
        "operation": f"run_{subcommand}",
        "main_command": "run",
        "subcommand": subcommand,
        "args": {},
    }

    # Find run ID
    j = run_index + 2
    while j < len(gh_tokens):
        token = gh_tokens[j]
        if token.startswith("-"):
            if j + 1 < len(gh_tokens) and not gh_tokens[j + 1].startswith("-"):
                j += 2
            else:
                j += 1
            continue
        if token.isdigit():
            result["run_id"] = int(token)
            break
        j += 1

    return result


def _parse_gh_auth(command: str, gh_tokens: list[str], auth_index: int) -> dict[str, Any] | None:
    """Parse gh auth command."""
    if auth_index + 1 >= len(gh_tokens):
        return None

    subcommand = gh_tokens[auth_index + 1]

    return {
        "type": "gh",
        "operation": f"auth_{subcommand}",
        "main_command": "auth",
        "subcommand": subcommand,
        "args": {},
    }


def _extract_gh_args(tokens: list[str], start: int) -> dict[str, str | bool]:
    """Extract common gh command arguments."""
    args: dict[str, str | bool] = {}

    flags_with_args = {
        "--title",
        "--body",
        "--label",
        "--assignee",
        "--reviewer",
        "--base",
        "--head",
        "--state",
        "--json",
        "--jq",
    }

    i = start
    while i < len(tokens):
        token = tokens[i]
        if token.startswith("--"):
            if "=" in token:
                key, value = token.split("=", 1)
                args[key] = value
                i += 1
            elif token in flags_with_args and i + 1 < len(tokens):
                args[token] = tokens[i + 1]
                i += 2
            else:
                # Boolean flag
                args[token] = True
                i += 1
        else:
            i += 1

    return args
