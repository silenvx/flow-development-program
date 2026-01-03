"""Tests for ci_monitor.state module."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ci_monitor.state import (
    clear_monitor_state,
    get_state_file_path,
    load_monitor_state,
    save_monitor_state,
)


class TestGetStateFilePath:
    """Tests for get_state_file_path function."""

    def test_valid_pr_number(self):
        """Test with valid PR number."""
        with patch("ci_monitor.state._get_main_repo_path") as mock:
            mock.return_value = Path("/repo")
            path = get_state_file_path("123")
            assert path == Path("/repo/.claude/state/ci-monitor-123.json")

    def test_invalid_pr_number_raises(self):
        """Test that invalid PR number raises ValueError."""
        with pytest.raises(ValueError):
            get_state_file_path("../escape")

    def test_alphanumeric_pr_number(self):
        """Test with alphanumeric PR number."""
        with patch("ci_monitor.state._get_main_repo_path") as mock:
            mock.return_value = Path("/repo")
            path = get_state_file_path("abc123")
            assert "abc123" in str(path)


class TestSaveMonitorState:
    """Tests for save_monitor_state function."""

    def test_saves_state(self, tmp_path):
        """Test saving monitor state."""
        with patch("ci_monitor.state._get_main_repo_path") as mock:
            mock.return_value = tmp_path
            result = save_monitor_state("123", {"status": "running"})
            assert result is True

            state_file = tmp_path / ".claude" / "state" / "ci-monitor-123.json"
            assert state_file.exists()

            data = json.loads(state_file.read_text())
            assert data["status"] == "running"
            assert data["pr_number"] == "123"
            assert "updated_at" in data

    def test_invalid_pr_number_returns_false(self):
        """Test that invalid PR number returns False."""
        result = save_monitor_state("../bad", {"status": "running"})
        assert result is False


class TestLoadMonitorState:
    """Tests for load_monitor_state function."""

    def test_loads_existing_state(self, tmp_path):
        """Test loading existing state."""
        with patch("ci_monitor.state._get_main_repo_path") as mock:
            mock.return_value = tmp_path

            # Create state file
            state_dir = tmp_path / ".claude" / "state"
            state_dir.mkdir(parents=True)
            state_file = state_dir / "ci-monitor-123.json"
            state_file.write_text('{"status": "completed"}')

            result = load_monitor_state("123")
            assert result == {"status": "completed"}

    def test_returns_none_for_nonexistent(self, tmp_path):
        """Test returning None for nonexistent state."""
        with patch("ci_monitor.state._get_main_repo_path") as mock:
            mock.return_value = tmp_path
            result = load_monitor_state("999")
            assert result is None


class TestClearMonitorState:
    """Tests for clear_monitor_state function."""

    def test_clears_existing_state(self, tmp_path):
        """Test clearing existing state."""
        with patch("ci_monitor.state._get_main_repo_path") as mock:
            mock.return_value = tmp_path

            # Create state file
            state_dir = tmp_path / ".claude" / "state"
            state_dir.mkdir(parents=True)
            state_file = state_dir / "ci-monitor-123.json"
            state_file.write_text('{"status": "completed"}')

            result = clear_monitor_state("123")
            assert result is True
            assert not state_file.exists()

    def test_returns_true_for_nonexistent(self, tmp_path):
        """Test returning True for nonexistent state."""
        with patch("ci_monitor.state._get_main_repo_path") as mock:
            mock.return_value = tmp_path
            result = clear_monitor_state("999")
            assert result is True
