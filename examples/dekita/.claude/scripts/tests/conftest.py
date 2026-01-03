#!/usr/bin/env python3
"""Pytest configuration for scripts tests.

This conftest.py ensures required dependencies are available for testing.

Required dependencies:
- pyyaml: For test_validate_lefthook.py

Install with: pip install pyyaml
Or run with: uv run --with pyyaml pytest .claude/scripts/tests/
"""


def pytest_ignore_collect(collection_path, config):
    """Ignore test modules that require missing dependencies."""
    # Check if pyyaml is available for validate_lefthook tests
    if collection_path.name == "test_validate_lefthook.py":
        try:
            import yaml  # noqa: F401

            return False  # Don't ignore, collect normally
        except ImportError:
            return True  # Ignore this file
    return False  # Don't ignore other files
