"""Tests for repeated block warning feature.

Issue #2401: Detect repeated blocks and add enhanced warning.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest


class TestCountRecentBlocks:
    """Test _count_recent_blocks function."""

    @pytest.fixture
    def mock_log_dir(self, tmp_path: Path) -> Path:
        """Create a temporary log directory."""
        log_dir = tmp_path / ".claude" / "logs" / "execution"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir

    @pytest.fixture
    def mock_session_id(self) -> str:
        """Return a mock session ID."""
        return "test-session-id-12345"

    def _create_log_entries(
        self,
        log_dir: Path,
        session_id: str,
        entries: list[dict],
    ) -> None:
        """Create log entries in session-specific log file."""
        log_file = log_dir / f"hook-execution-{session_id}.jsonl"
        with open(log_file, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def test_returns_zero_when_no_session_id(self) -> None:
        """Returns 0 when session_id is None."""
        from lib.results import _count_recent_blocks

        result = _count_recent_blocks("test-hook", None)
        assert result == 0

    def test_returns_zero_when_no_log_file(
        self,
        mock_log_dir: Path,
        mock_session_id: str,
    ) -> None:
        """Returns 0 when log file doesn't exist."""
        from lib.results import _count_recent_blocks

        with mock.patch("lib.results._get_execution_log_dir", return_value=mock_log_dir):
            result = _count_recent_blocks("test-hook", mock_session_id)

        assert result == 0

    def test_counts_recent_blocks_same_hook(
        self,
        mock_log_dir: Path,
        mock_session_id: str,
    ) -> None:
        """Counts blocks from the same hook within the time window."""
        from lib.results import _count_recent_blocks

        now = datetime.now(UTC)
        entries = [
            {
                "timestamp": (now - timedelta(seconds=10)).isoformat(),
                "hook": "test-hook",
                "decision": "block",
                "reason": "First block",
            },
            {
                "timestamp": (now - timedelta(seconds=5)).isoformat(),
                "hook": "test-hook",
                "decision": "block",
                "reason": "Second block",
            },
        ]
        self._create_log_entries(mock_log_dir, mock_session_id, entries)

        with mock.patch("lib.results._get_execution_log_dir", return_value=mock_log_dir):
            result = _count_recent_blocks("test-hook", mock_session_id)

        assert result == 2

    def test_ignores_blocks_from_different_hook(
        self,
        mock_log_dir: Path,
        mock_session_id: str,
    ) -> None:
        """Does not count blocks from different hooks."""
        from lib.results import _count_recent_blocks

        now = datetime.now(UTC)
        entries = [
            {
                "timestamp": (now - timedelta(seconds=10)).isoformat(),
                "hook": "other-hook",
                "decision": "block",
                "reason": "Other hook block",
            },
            {
                "timestamp": (now - timedelta(seconds=5)).isoformat(),
                "hook": "test-hook",
                "decision": "block",
                "reason": "Test hook block",
            },
        ]
        self._create_log_entries(mock_log_dir, mock_session_id, entries)

        with mock.patch("lib.results._get_execution_log_dir", return_value=mock_log_dir):
            result = _count_recent_blocks("test-hook", mock_session_id)

        assert result == 1

    def test_ignores_approve_decisions(
        self,
        mock_log_dir: Path,
        mock_session_id: str,
    ) -> None:
        """Does not count approve decisions."""
        from lib.results import _count_recent_blocks

        now = datetime.now(UTC)
        entries = [
            {
                "timestamp": (now - timedelta(seconds=10)).isoformat(),
                "hook": "test-hook",
                "decision": "approve",
                "reason": "Approved",
            },
            {
                "timestamp": (now - timedelta(seconds=5)).isoformat(),
                "hook": "test-hook",
                "decision": "block",
                "reason": "Blocked",
            },
        ]
        self._create_log_entries(mock_log_dir, mock_session_id, entries)

        with mock.patch("lib.results._get_execution_log_dir", return_value=mock_log_dir):
            result = _count_recent_blocks("test-hook", mock_session_id)

        assert result == 1

    def test_ignores_old_blocks(
        self,
        mock_log_dir: Path,
        mock_session_id: str,
    ) -> None:
        """Does not count blocks outside the time window."""
        from lib.results import REPEATED_BLOCK_WINDOW_SECONDS, _count_recent_blocks

        now = datetime.now(UTC)
        entries = [
            {
                "timestamp": (
                    now - timedelta(seconds=REPEATED_BLOCK_WINDOW_SECONDS + 10)
                ).isoformat(),
                "hook": "test-hook",
                "decision": "block",
                "reason": "Old block",
            },
            {
                "timestamp": (now - timedelta(seconds=5)).isoformat(),
                "hook": "test-hook",
                "decision": "block",
                "reason": "Recent block",
            },
        ]
        self._create_log_entries(mock_log_dir, mock_session_id, entries)

        with mock.patch("lib.results._get_execution_log_dir", return_value=mock_log_dir):
            result = _count_recent_blocks("test-hook", mock_session_id)

        assert result == 1


class TestMakeBlockResultRepeatedWarning:
    """Test make_block_result with repeated block warning."""

    @pytest.fixture
    def mock_log_dir(self, tmp_path: Path) -> Path:
        """Create a temporary log directory."""
        log_dir = tmp_path / ".claude" / "logs" / "execution"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir

    @pytest.fixture
    def mock_session_id(self) -> str:
        """Return a mock session ID."""
        return "test-session-id-12345"

    def _create_log_entries(
        self,
        log_dir: Path,
        session_id: str,
        entries: list[dict],
    ) -> None:
        """Create log entries in session-specific log file."""
        log_file = log_dir / f"hook-execution-{session_id}.jsonl"
        with open(log_file, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def test_no_warning_when_first_block(
        self,
        mock_log_dir: Path,
        mock_session_id: str,
        capsys,
    ) -> None:
        """No repeated warning when it's the first block.

        Issue #2496: Updated to use HookContext instead of global state.
        """
        from lib.results import make_block_result
        from lib.session import HookContext

        ctx = HookContext(session_id=mock_session_id)

        with mock.patch("lib.results._get_execution_log_dir", return_value=mock_log_dir):
            with mock.patch("lib.execution._get_execution_log_dir", return_value=mock_log_dir):
                result = make_block_result("test-hook", "Test reason", ctx=ctx)

        assert "連続ブロック" not in result["reason"]
        assert "連続ブロック" not in result["systemMessage"]

    def test_warning_when_repeated_blocks(
        self,
        mock_log_dir: Path,
        mock_session_id: str,
        capsys,
    ) -> None:
        """Adds warning when repeated blocks are detected.

        Issue #2496: Updated to use HookContext instead of global state.
        """
        from lib.results import REPEATED_BLOCK_THRESHOLD, make_block_result
        from lib.session import HookContext

        ctx = HookContext(session_id=mock_session_id)

        now = datetime.now(UTC)
        # Create previous blocks (equal to threshold)
        entries = [
            {
                "timestamp": (now - timedelta(seconds=i * 5)).isoformat(),
                "hook": "test-hook",
                "decision": "block",
                "reason": f"Block {i}",
            }
            for i in range(REPEATED_BLOCK_THRESHOLD)
        ]
        self._create_log_entries(mock_log_dir, mock_session_id, entries)

        with mock.patch("lib.results._get_execution_log_dir", return_value=mock_log_dir):
            with mock.patch("lib.execution._get_execution_log_dir", return_value=mock_log_dir):
                result = make_block_result("test-hook", "Another block", ctx=ctx)

        # Check that warning is included in reason
        assert "連続ブロック" in result["reason"]
        assert "AGENTS.md" in result["reason"]

        # Check that short warning is included in systemMessage
        assert "連続ブロック" in result["systemMessage"]

    def test_warning_includes_block_count(
        self,
        mock_log_dir: Path,
        mock_session_id: str,
        capsys,
    ) -> None:
        """Warning includes the actual block count (previous + current).

        Issue #2496: Updated to use HookContext instead of global state.
        """
        from lib.results import make_block_result
        from lib.session import HookContext

        ctx = HookContext(session_id=mock_session_id)

        now = datetime.now(UTC)
        # Create 3 previous blocks
        entries = [
            {
                "timestamp": (now - timedelta(seconds=i * 5)).isoformat(),
                "hook": "test-hook",
                "decision": "block",
                "reason": f"Block {i}",
            }
            for i in range(3)
        ]
        self._create_log_entries(mock_log_dir, mock_session_id, entries)

        with mock.patch("lib.results._get_execution_log_dir", return_value=mock_log_dir):
            with mock.patch("lib.execution._get_execution_log_dir", return_value=mock_log_dir):
                result = make_block_result("test-hook", "Another block", ctx=ctx)

        # 3 previous blocks + 1 current = 4 total
        assert "4回連続ブロック" in result["reason"]
        assert "4回連続ブロック" in result["systemMessage"]

    def test_stderr_includes_warning(
        self,
        mock_log_dir: Path,
        mock_session_id: str,
        capsys,
    ) -> None:
        """Warning is also output to stderr.

        Issue #2496: Updated to use HookContext instead of global state.
        """
        from lib.results import make_block_result
        from lib.session import HookContext

        ctx = HookContext(session_id=mock_session_id)

        now = datetime.now(UTC)
        entries = [
            {
                "timestamp": (now - timedelta(seconds=i * 5)).isoformat(),
                "hook": "test-hook",
                "decision": "block",
                "reason": f"Block {i}",
            }
            for i in range(2)
        ]
        self._create_log_entries(mock_log_dir, mock_session_id, entries)

        with mock.patch("lib.results._get_execution_log_dir", return_value=mock_log_dir):
            with mock.patch("lib.execution._get_execution_log_dir", return_value=mock_log_dir):
                make_block_result("test-hook", "Another block", ctx=ctx)

        captured = capsys.readouterr()
        assert "連続ブロック" in captured.err
