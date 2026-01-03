"""Tests for uv-run-guard.py hook.

Issue #2145
"""

import json
import os
from unittest.mock import patch

import pytest

# Import the module under test
from uv_run_guard import (
    UV_RUN_PATTERN,
    extract_tool_from_uv_run,
    is_in_worktree,
    main,
)


class TestUvRunPattern:
    """Tests for UV_RUN_PATTERN regex."""

    def test_matches_basic_uv_run(self) -> None:
        assert UV_RUN_PATTERN.search("uv run ruff check .")

    def test_matches_uv_run_with_options(self) -> None:
        assert UV_RUN_PATTERN.search("uv run --with foo bar")

    def test_matches_uv_run_in_middle(self) -> None:
        assert UV_RUN_PATTERN.search("cd /path && uv run pytest")

    def test_no_match_uvx(self) -> None:
        assert not UV_RUN_PATTERN.search("uvx ruff check .")

    def test_no_match_uv_other(self) -> None:
        assert not UV_RUN_PATTERN.search("uv sync")
        assert not UV_RUN_PATTERN.search("uv pip install")


class TestIsInWorktree:
    """Tests for is_in_worktree function."""

    def test_detects_worktree_path(self) -> None:
        with patch.dict(os.environ, {"PWD": ""}):
            with patch("os.getcwd", return_value="/repo/.worktrees/issue-123"):
                assert is_in_worktree()

    def test_detects_main_repo(self) -> None:
        with patch.dict(os.environ, {"PWD": ""}):
            with patch("os.getcwd", return_value="/repo/main"):
                assert not is_in_worktree()

    # Issue #2150: Windows path tests
    def test_detects_worktree_path_windows(self) -> None:
        """Windows-style path with backslashes."""
        with patch.dict(os.environ, {"PWD": ""}):
            with patch("os.getcwd", return_value=r"C:\repo\.worktrees\issue-123"):
                assert is_in_worktree()

    def test_detects_main_repo_windows(self) -> None:
        """Windows-style path for main repo."""
        with patch.dict(os.environ, {"PWD": ""}):
            with patch("os.getcwd", return_value=r"C:\repo\main"):
                assert not is_in_worktree()


class TestExtractToolFromUvRun:
    """Tests for extract_tool_from_uv_run function."""

    def test_extracts_simple_tool(self) -> None:
        assert extract_tool_from_uv_run("uv run ruff check .") == "ruff"

    def test_extracts_tool_with_options(self) -> None:
        # --with takes an argument, so the tool name is 'bar' (after skipping --with foo)
        result = extract_tool_from_uv_run("uv run --with foo bar")
        assert result == "bar"

    def test_extracts_python(self) -> None:
        assert extract_tool_from_uv_run("uv run python -m pytest") == "python"

    # Issue #2150: Edge case tests
    def test_options_only_returns_none(self) -> None:
        """When only options are provided without a tool name."""
        assert extract_tool_from_uv_run("uv run --with foo") is None
        assert extract_tool_from_uv_run("uv run --python 3.11") is None

    def test_opt_equals_value_format(self) -> None:
        """Handle --opt=value format correctly."""
        assert extract_tool_from_uv_run("uv run --python=3.11 ruff") == "ruff"
        assert extract_tool_from_uv_run("uv run --with=foo bar") == "bar"

    def test_multiple_options(self) -> None:
        """Handle multiple options before tool name."""
        result = extract_tool_from_uv_run("uv run --with foo --python 3.11 ruff")
        assert result == "ruff"
        result = extract_tool_from_uv_run("uv run --with=foo --python=3.11 pytest")
        assert result == "pytest"

    def test_short_flags(self) -> None:
        """Handle short flags like -v, -q."""
        assert extract_tool_from_uv_run("uv run -v ruff") == "ruff"
        assert extract_tool_from_uv_run("uv run -q pytest") == "pytest"


class TestMain:
    """Tests for main function."""

    def test_allows_uv_run_in_main_repo(self, capsys: pytest.CaptureFixture) -> None:
        input_data = {"tool_input": {"command": "uv run ruff check ."}}
        with patch("uv_run_guard.parse_hook_input", return_value=input_data):
            with patch("os.getcwd", return_value="/repo/main"):
                main()
        output = json.loads(capsys.readouterr().out)
        assert output["allow"] is True

    def test_blocks_uv_run_in_worktree(self, capsys: pytest.CaptureFixture) -> None:
        input_data = {"tool_input": {"command": "uv run ruff check ."}}
        with patch("uv_run_guard.parse_hook_input", return_value=input_data):
            with patch("os.getcwd", return_value="/repo/.worktrees/issue-123"):
                main()
        output = json.loads(capsys.readouterr().out)
        assert output["allow"] is False
        assert "uvx" in output["reason"]
        assert "ruff" in output["reason"]

    def test_allows_uvx_in_worktree(self, capsys: pytest.CaptureFixture) -> None:
        input_data = {"tool_input": {"command": "uvx ruff check ."}}
        with patch("uv_run_guard.parse_hook_input", return_value=input_data):
            with patch("os.getcwd", return_value="/repo/.worktrees/issue-123"):
                main()
        output = json.loads(capsys.readouterr().out)
        assert output["allow"] is True

    def test_allows_other_commands_in_worktree(self, capsys: pytest.CaptureFixture) -> None:
        input_data = {"tool_input": {"command": "git status"}}
        with patch("uv_run_guard.parse_hook_input", return_value=input_data):
            with patch("os.getcwd", return_value="/repo/.worktrees/issue-123"):
                main()
        output = json.loads(capsys.readouterr().out)
        assert output["allow"] is True
