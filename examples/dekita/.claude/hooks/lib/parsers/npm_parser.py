#!/usr/bin/env python3
"""npm/pnpm command parser.

This module provides structured parsing of npm/pnpm commands
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


def parse_npm_command(command: str) -> dict[str, Any] | None:
    """Parse npm/pnpm command to extract structured data.

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

    # Find npm/pnpm command (Issue #1258: handle absolute paths like /usr/bin/npm)
    # Issue #1258: Skip false positives where npm/pnpm is an argument path
    # Must be in command position: start of line, after shell operator, or after env var
    pkg_manager = None
    pkg_start = None
    npm_tokens = []
    search_start = 0
    shell_operators = ("|", ";", "&&", "||")

    while search_start < len(tokens):
        # Find next token whose basename is npm/pnpm AND is in command position
        pkg_start = None
        pkg_manager = None
        for i in range(search_start, len(tokens)):
            cmd_name = get_command_name(tokens[i])
            if cmd_name not in ("npm", "pnpm"):
                continue
            # Check if in valid command position
            if i == 0:
                pkg_manager = cmd_name
                pkg_start = i
                break
            prev_token = tokens[i - 1]
            if prev_token in shell_operators or ends_with_shell_separator(prev_token):
                pkg_manager = cmd_name
                pkg_start = i
                break
            if "=" in prev_token and not prev_token.startswith("-"):
                pkg_manager = cmd_name
                pkg_start = i
                break
            if is_command_wrapper(prev_token):
                pkg_manager = cmd_name
                pkg_start = i
                break

        if pkg_start is None:
            return None

        # Extract tokens after npm/pnpm until we hit a separator
        npm_tokens = []
        for token in tokens[pkg_start + 1 :]:
            if token in shell_operators or ends_with_shell_separator(token):
                break
            npm_tokens.append(token)

        if npm_tokens:
            break
        search_start = pkg_start + 1
        pkg_start = None
        pkg_manager = None

    if pkg_start is None or not npm_tokens:
        return None

    subcommand = npm_tokens[0]

    result: dict[str, Any] = {
        "type": "npm",
        "package_manager": pkg_manager,
        "operation": subcommand,
        "subcommand": subcommand,
        "args": {},
    }

    # Handle run commands (npm run xxx)
    if subcommand == "run" and len(npm_tokens) > 1:
        result["script"] = npm_tokens[1]
        result["operation"] = f"run_{npm_tokens[1]}"

    # Handle specific subcommands
    if subcommand in ("install", "i", "add"):
        result["operation"] = "install"
        # Check for packages being installed
        packages = [t for t in npm_tokens[1:] if not t.startswith("-")]
        if packages:
            result["packages"] = packages
    elif subcommand in ("test", "t"):
        result["operation"] = "test"
    elif subcommand == "build":
        result["operation"] = "build"

    return result
