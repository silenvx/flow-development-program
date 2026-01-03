"""GitHub API communication functions.

This module provides low-level GitHub CLI (gh) command execution.
Extracted from ci-monitor.py as part of Issue #1754 refactoring.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def run_gh_command(args: list[str], timeout: int = 30) -> tuple[bool, str]:
    """Run a gh command and return (success, output)."""
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode == 0, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


def run_gh_command_with_error(args: list[str], timeout: int = 30) -> tuple[bool, str, str]:
    """Run a gh command and return (success, stdout, stderr).

    Unlike run_gh_command, this function also returns stderr for error diagnosis.
    Use this when you need to know why a command failed.
    """
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "", "Command timed out"
    except Exception as e:
        return False, "", str(e)


def _remove_urls_from_line(line: str) -> str:
    """Remove URLs from a line to allow rate limit pattern matching.

    Issue #1581: Instead of skipping entire lines with URLs, remove URL parts
    to detect rate limit errors even when URL and error are on the same line.
    """
    return re.sub(r"https?://\S+", "", line, flags=re.IGNORECASE)


def is_rate_limit_error(output: str, stderr: str = "") -> bool:
    """Check if the error is due to GraphQL rate limiting.

    Issue #1096: Detect rate limit errors for automatic fallback.
    Issue #1564: Improved detection to reduce false positives from URLs/docs.
    Issue #1581: Remove URLs from lines instead of skipping entire lines.

    Args:
        output: The stdout from gh command (may contain GraphQL error JSON).
        stderr: The stderr from gh command.

    Returns:
        True if the error is a rate limit error, False otherwise.
    """
    combined = output + stderr
    rate_limit_indicators = [
        "rate_limited",
        "rate limit exceeded",
        "secondary rate limit",
        "abuse detection",
        "too many requests",
    ]

    for line in combined.split("\n"):
        line_without_urls = _remove_urls_from_line(line)
        line_lower = line_without_urls.lower()

        if any(indicator in line_lower for indicator in rate_limit_indicators):
            return True

    return False


def run_graphql_with_fallback(
    args: list[str],
    fallback_fn: Callable[[], tuple[bool, str]] | None = None,
    timeout: int = 30,
    *,
    print_warning_fn: Callable[[], None] | None = None,
) -> tuple[bool, str, bool]:
    """Run a GraphQL command with automatic rate limit detection and optional fallback.

    Issue #1096: Automatic fallback when rate limited.

    Args:
        args: Arguments for gh api graphql command.
        fallback_fn: Optional function to call if rate limited (returns (success, output)).
        timeout: Command timeout in seconds.
        print_warning_fn: Optional function to print rate limit warning.

    Returns:
        Tuple of (success, output, used_fallback).
    """
    success, output, stderr = run_gh_command_with_error(args, timeout)

    if success:
        return True, output, False

    if is_rate_limit_error(output, stderr):
        if print_warning_fn:
            print_warning_fn()
        else:
            print("Warning: GraphQL rate limit reached", file=sys.stderr)

        if fallback_fn:
            print("  -> Falling back to REST API...", file=sys.stderr)
            fb_success, fb_output = fallback_fn()
            if fb_success:
                return True, fb_output, True
            print("  Warning: Fallback also failed", file=sys.stderr)
            # Return used_fallback=True even when fallback failed
            # so callers know fallback was attempted for logging/metrics
            return False, output, True

        return False, output, False

    return False, output, False


def get_repo_info() -> tuple[str, str] | None:
    """Get the owner and repo name from the current git repository.

    Returns:
        Tuple of (owner, repo_name) or None if not in a git repository.
    """
    success, output = run_gh_command(["repo", "view", "--json", "owner,name"])
    if not success:
        return None

    try:
        import json

        data = json.loads(output)
        owner = data.get("owner", {}).get("login")
        name = data.get("name")
        if owner and name:
            return owner, name
    except Exception:
        # gh コマンドの出力が想定外（非 JSON 等）の場合は、リポジトリ情報なしとして扱う
        pass

    return None
