#!/usr/bin/env python3
"""Test runner for Claude Code hooks.

Uses pytest to run all tests in the hooks/tests directory.
Also runs doctests from lib/ modules to verify docstring examples.

Issue #1228: Added doctest support to automatically validate docstring examples.
"""

import subprocess
import sys
from pathlib import Path


def main():
    """Run all hook tests using pytest."""
    tests_dir = Path(__file__).parent
    hooks_dir = tests_dir.parent
    lib_dir = hooks_dir / "lib"

    # Run unit tests from tests/ directory (without doctest)
    result1 = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(tests_dir),
            "-v",
            "--tb=short",
        ],
        cwd=str(hooks_dir),
    )

    # Run doctests from lib/ directory
    result2 = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(lib_dir),
            "-v",
            "--tb=short",
            "--doctest-modules",
        ],
        cwd=str(hooks_dir),
    )

    # Exit with failure if either failed
    sys.exit(result1.returncode or result2.returncode)


if __name__ == "__main__":
    main()
