"""Common test utilities for hook tests.

Provides shared functionality for loading hyphenated Python modules
and other test utilities.

This module also provides automatic test environment cleanup fixtures
to prevent flaky tests caused by leftover files from previous runs.
"""

from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

import pytest


@contextmanager
def _subtest_compat(**kwargs):
    """Compatibility shim for unittest's subTest in pytest."""
    yield


@pytest.fixture(autouse=True)
def add_subtest(request):
    """Add subTest method to test instances for unittest compatibility."""
    if request.instance is not None and hasattr(request.instance, "__class__"):
        request.instance.subTest = _subtest_compat


@pytest.fixture(autouse=True)
def isolated_home_directory(tmp_path, monkeypatch):
    """Isolate tests from the real home directory.

    This fixture automatically mocks Path.home() and HOME environment variable
    to return a temporary directory, preventing tests from being affected by
    files in the real home directory.

    This fixes flaky test failures caused by:
    - ~/.claude/plans/ containing plan files from actual development work
    - Other user-specific configuration files

    The fixture mocks both:
    - Path.home() - used by pathlib operations
    - HOME environment variable - used by Path.expanduser() and os.path.expanduser()

    The fixture is autouse=True so it applies to all tests automatically.
    Tests that need to mock Path.home() themselves can still do so - their
    mock will take precedence within their test scope.

    Related Issues:
    - #1105: test_planning_enforcement fails when plan file exists
    - #1112: Test environment cleanup automation
    """
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir(parents=True, exist_ok=True)

    # Mock Path.home() to return fake_home
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    # Mock HOME environment variable for expanduser() support
    # This ensures Path("~").expanduser() also uses the fake home
    monkeypatch.setenv("HOME", str(fake_home))

    yield fake_home


# Hook and script directories
HOOKS_DIR = Path(__file__).parent.parent
SCRIPTS_DIR = HOOKS_DIR.parent / "scripts"

# Ensure hooks directory is in path for common module imports
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))


def _load_module(module_name: str, module_path: Path) -> ModuleType:
    """Load a Python module from a file path.

    Args:
        module_name: Name to register in sys.modules
        module_path: Path to the .py file

    Returns:
        The loaded module object

    Raises:
        FileNotFoundError: If the module file doesn't exist
        ImportError: If the module spec cannot be loaded
    """
    if not module_path.exists():
        raise FileNotFoundError(f"Module not found: {module_path}")

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Failed to load spec for {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    return module


def load_hook_module(hook_name: str) -> ModuleType:
    """Load a hook module by name (handles hyphenated filenames).

    Args:
        hook_name: Name of the hook file without .py extension
                   (e.g., "ci-wait-check", "worktree-warning")

    Returns:
        The loaded module object

    Example:
        >>> ci_wait_check = load_hook_module("ci-wait-check")
        >>> ci_wait_check.main()
    """
    module_name = hook_name.replace("-", "_")
    module_path = HOOKS_DIR / f"{hook_name}.py"
    return _load_module(module_name, module_path)


def load_script_module(script_name: str) -> ModuleType:
    """Load a script module by name (handles hyphenated filenames).

    Args:
        script_name: Name of the script file without .py extension
                     (e.g., "session-report-generator", "ci-monitor")

    Returns:
        The loaded module object

    Example:
        >>> report_gen = load_script_module("session-report-generator")
        >>> report_gen.main()
    """
    module_name = script_name.replace("-", "_")
    module_path = SCRIPTS_DIR / f"{script_name}.py"
    return _load_module(module_name, module_path)
