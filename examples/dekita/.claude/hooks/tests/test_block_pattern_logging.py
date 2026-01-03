"""Tests for block pattern logging functionality.

Issue #1361: Tests for tracking block→success patterns for learning.
"""

from __future__ import annotations

import json
import time

# Note: Using 'import as' pattern for modules that need monkeypatch.setattr()
# This is necessary because monkeypatch needs module reference to patch attributes
import common
import lib.block_patterns as block_patterns
import pytest
from lib.execution import log_hook_execution


class TestGenerateBlockId:
    """Tests for _generate_block_id function."""

    TEST_SESSION_ID = "test-session-12345678"

    def test_format(self):
        """Block ID should have correct format."""
        # Issue #2496: Pass session_id directly instead of mocking
        block_id = block_patterns._generate_block_id(self.TEST_SESSION_ID)
        assert block_id.startswith("blk_")
        parts = block_id.split("-")
        # Format: blk_YYYYMMDD-HHMMSS-ffffff-session
        assert len(parts) >= 4
        assert parts[0].startswith("blk_")

    def test_uniqueness(self):
        """Block IDs should be unique."""
        # Issue #2496: Pass session_id directly instead of mocking
        id1 = block_patterns._generate_block_id(self.TEST_SESSION_ID)
        id2 = block_patterns._generate_block_id(self.TEST_SESSION_ID)
        assert id1 != id2


class TestComputeCommandHash:
    """Tests for _compute_command_hash function."""

    def test_same_input_same_hash(self):
        """Same hook and command should produce same hash."""
        hash1 = block_patterns._compute_command_hash("test-hook", "git push")
        hash2 = block_patterns._compute_command_hash("test-hook", "git push")
        assert hash1 == hash2

    def test_different_hook_different_hash(self):
        """Different hook should produce different hash."""
        hash1 = block_patterns._compute_command_hash("hook-a", "git push")
        hash2 = block_patterns._compute_command_hash("hook-b", "git push")
        assert hash1 != hash2

    def test_different_command_different_hash(self):
        """Different command should produce different hash."""
        hash1 = block_patterns._compute_command_hash("test-hook", "git push")
        hash2 = block_patterns._compute_command_hash("test-hook", "git pull")
        assert hash1 != hash2

    def test_none_command(self):
        """None command should be handled."""
        hash1 = block_patterns._compute_command_hash("test-hook", None)
        assert len(hash1) == 16

    def test_hash_length(self):
        """Hash should be 16 characters."""
        hash1 = block_patterns._compute_command_hash("test-hook", "git push")
        assert len(hash1) == 16

    def test_long_command_truncated(self):
        """Long commands should be truncated to first 50 chars."""
        long_cmd = "a" * 100
        hash1 = block_patterns._compute_command_hash("test-hook", long_cmd)
        hash2 = block_patterns._compute_command_hash("test-hook", "a" * 50)
        assert hash1 == hash2


class TestBlockPatternLogging:
    """Tests for block pattern logging integration.

    Issue #1840: Updated to use session-specific log files.
    Issue #2496: Updated to use _get_session_id_with_fallback for PPID-based fallback.
    """

    TEST_SESSION_ID = "test-session-12345678"

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        """Set up test environment."""
        # Mock log directory
        self.log_dir = tmp_path / "logs" / "metrics"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # Issue #1840: Use session-specific log file
        self.block_patterns_log = self.log_dir / f"block-patterns-{self.TEST_SESSION_ID}.jsonl"

        # Mock SESSION_DIR for file-based block tracking
        self.session_dir = tmp_path / "session"
        self.session_dir.mkdir(parents=True, exist_ok=True)

        # Issue #2496: Mock _get_session_id_with_fallback for PPID-based fallback
        monkeypatch.setattr(
            block_patterns,
            "_get_session_id_with_fallback",
            lambda session_id=None: self.TEST_SESSION_ID,
        )

        # Mock block_patterns module's path functions
        monkeypatch.setattr(block_patterns, "_get_session_dir", lambda: self.session_dir)
        monkeypatch.setattr(
            block_patterns, "_get_metrics_log_dir", lambda project_dir=None: self.log_dir
        )

        # Mock EXECUTION_LOG_DIR to prevent actual log writes
        exec_log_dir = tmp_path / "logs" / "execution"
        exec_log_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(common, "EXECUTION_LOG_DIR", exec_log_dir)

    def test_block_creates_entry(self):
        """Block event should create entry in recent blocks file."""
        block_patterns.record_block("test-hook", "Block reason", {"command": "git push"})

        cmd_hash = block_patterns._compute_command_hash("test-hook", "git push")
        recent_blocks = block_patterns._load_recent_blocks()
        assert cmd_hash in recent_blocks
        assert recent_blocks[cmd_hash]["hook"] == "test-hook"
        assert recent_blocks[cmd_hash]["block_id"].startswith("blk_")

    def test_block_logs_to_file(self):
        """Block event should log to block-patterns.jsonl."""
        block_patterns.record_block("test-hook", "Block reason", {"command": "git push"})

        log_file = self.block_patterns_log
        assert log_file.exists()

        with log_file.open() as f:
            entry = json.loads(f.read().strip())
            assert entry["type"] == "block"
            assert entry["hook"] == "test-hook"
            assert entry["reason"] == "Block reason"
            assert entry["command_preview"] == "git push"

    def test_success_after_block_logs_resolved(self):
        """Success within window should log block_resolved."""
        # Record block
        block_patterns.record_block("test-hook", "Block reason", {"command": "git push"})

        # Simulate success immediately
        block_patterns.check_block_resolution("test-hook", {"command": "git push"})

        log_file = self.block_patterns_log
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2

        resolved_entry = json.loads(lines[1])
        assert resolved_entry["type"] == "block_resolved"
        assert resolved_entry["hook"] == "test-hook"
        assert "resolution" in resolved_entry
        assert resolved_entry["resolution"]["retry_count"] == 1

    def test_success_after_timeout_logs_expired(self):
        """Success after window should log block_expired."""
        # Record block with old timestamp
        block_patterns.record_block("test-hook", "Block reason", {"command": "git push"})

        # Manipulate timestamp to simulate timeout
        cmd_hash = block_patterns._compute_command_hash("test-hook", "git push")
        recent_blocks = block_patterns._load_recent_blocks()
        recent_blocks[cmd_hash]["timestamp"] = time.time() - 120  # 2 minutes ago
        block_patterns._save_recent_blocks(recent_blocks)

        # Simulate success
        block_patterns.check_block_resolution("test-hook", {"command": "git push"})

        log_file = self.block_patterns_log
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2

        expired_entry = json.loads(lines[1])
        assert expired_entry["type"] == "block_expired"
        assert expired_entry["hook"] == "test-hook"
        assert expired_entry["elapsed_seconds"] > 60

    def test_different_command_no_match(self):
        """Different command should not match block."""
        # Record block
        block_patterns.record_block("test-hook", "Block reason", {"command": "git push"})

        # Simulate success with different command
        block_patterns.check_block_resolution("test-hook", {"command": "git pull"})

        log_file = self.block_patterns_log
        lines = log_file.read_text().strip().split("\n")
        # Only the block entry, no resolved entry
        assert len(lines) == 1

    def test_block_removed_after_resolution(self):
        """Block entry should be removed from file after resolution."""
        # Record block
        block_patterns.record_block("test-hook", "Block reason", {"command": "git push"})
        cmd_hash = block_patterns._compute_command_hash("test-hook", "git push")
        assert cmd_hash in block_patterns._load_recent_blocks()

        # Simulate success
        block_patterns.check_block_resolution("test-hook", {"command": "git push"})

        # Should be removed
        assert cmd_hash not in block_patterns._load_recent_blocks()

    def test_none_details_handled(self):
        """None details should be handled gracefully."""
        block_patterns.record_block("test-hook", "Block reason", None)

        cmd_hash = block_patterns._compute_command_hash("test-hook", None)
        assert cmd_hash in block_patterns._load_recent_blocks()

    def test_log_hook_execution_triggers_block_tracking(self):
        """log_hook_execution should trigger block tracking for blocks."""
        log_hook_execution(
            "test-hook",
            "block",
            reason="Block reason",
            details={"command": "git push"},
        )

        cmd_hash = block_patterns._compute_command_hash("test-hook", "git push")
        assert cmd_hash in block_patterns._load_recent_blocks()

    def test_log_hook_execution_triggers_resolution_check(self):
        """log_hook_execution should check for resolution on approve."""
        # First, record a block
        block_patterns.record_block("test-hook", "Block reason", {"command": "git push"})

        # Then, log an approve
        log_hook_execution(
            "test-hook",
            "approve",
            details={"command": "git push"},
        )

        # Block should be resolved
        cmd_hash = block_patterns._compute_command_hash("test-hook", "git push")
        assert cmd_hash not in block_patterns._load_recent_blocks()

    def test_cross_process_persistence(self):
        """Blocks should persist across simulated process boundaries."""
        # Record block (simulates first hook process)
        block_patterns.record_block("test-hook", "Block reason", {"command": "git push"})

        # Verify file was created
        blocks_file = block_patterns._get_recent_blocks_file()
        assert blocks_file.exists()

        # Load fresh (simulates second hook process reading the file)
        recent_blocks = block_patterns._load_recent_blocks()
        cmd_hash = block_patterns._compute_command_hash("test-hook", "git push")
        assert cmd_hash in recent_blocks
        assert recent_blocks[cmd_hash]["hook"] == "test-hook"


class TestRetryCount:
    """Tests for retry count tracking (Issue #1640).

    Issue #1840: Updated to use session-specific log files.
    Issue #2496: Updated to use _get_session_id_with_fallback for PPID-based fallback.
    """

    TEST_SESSION_ID = "test-session-12345678"

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        """Set up test environment."""
        # Mock log directory
        self.log_dir = tmp_path / "logs" / "metrics"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # Issue #1840: Use session-specific log file
        self.block_patterns_log = self.log_dir / f"block-patterns-{self.TEST_SESSION_ID}.jsonl"

        # Mock SESSION_DIR for file-based block tracking
        self.session_dir = tmp_path / "session"
        self.session_dir.mkdir(parents=True, exist_ok=True)

        # Issue #2496: Mock _get_session_id_with_fallback for PPID-based fallback
        monkeypatch.setattr(
            block_patterns,
            "_get_session_id_with_fallback",
            lambda session_id=None: self.TEST_SESSION_ID,
        )

        # Mock block_patterns module's path functions
        monkeypatch.setattr(block_patterns, "_get_session_dir", lambda: self.session_dir)
        monkeypatch.setattr(
            block_patterns, "_get_metrics_log_dir", lambda project_dir=None: self.log_dir
        )

        # Mock EXECUTION_LOG_DIR to prevent actual log writes
        exec_log_dir = tmp_path / "logs" / "execution"
        exec_log_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(common, "EXECUTION_LOG_DIR", exec_log_dir)

    def test_first_block_has_retry_count_1(self):
        """First block should have retry_count=1."""
        block_patterns.record_block("test-hook", "Block reason", {"command": "git push"})

        cmd_hash = block_patterns._compute_command_hash("test-hook", "git push")
        recent_blocks = block_patterns._load_recent_blocks()
        assert recent_blocks[cmd_hash]["retry_count"] == 1

    def test_second_block_increments_retry_count(self):
        """Second block of same command should have retry_count=2."""
        block_patterns.record_block("test-hook", "Block reason", {"command": "git push"})
        block_patterns.record_block("test-hook", "Block reason 2", {"command": "git push"})

        cmd_hash = block_patterns._compute_command_hash("test-hook", "git push")
        recent_blocks = block_patterns._load_recent_blocks()
        assert recent_blocks[cmd_hash]["retry_count"] == 2

    def test_multiple_retries_tracked(self):
        """Multiple blocks should increment retry_count correctly."""
        for i in range(5):
            block_patterns.record_block("test-hook", f"Block reason {i}", {"command": "git push"})

        cmd_hash = block_patterns._compute_command_hash("test-hook", "git push")
        recent_blocks = block_patterns._load_recent_blocks()
        assert recent_blocks[cmd_hash]["retry_count"] == 5

    def test_retry_count_in_block_resolved_log(self):
        """Block resolved log should include actual retry_count."""
        # Block twice
        block_patterns.record_block("test-hook", "Block reason 1", {"command": "git push"})
        block_patterns.record_block("test-hook", "Block reason 2", {"command": "git push"})

        # Resolve
        block_patterns.check_block_resolution("test-hook", {"command": "git push"})

        # Check log
        with self.block_patterns_log.open() as f:
            lines = f.readlines()
            resolved_logs = [json.loads(line) for line in lines if "block_resolved" in line]
            assert len(resolved_logs) == 1
            assert resolved_logs[0]["resolution"]["retry_count"] == 2


class TestRecoveryAction:
    """Tests for recovery action tracking (Issue #1640).

    Issue #1840: Updated to use session-specific log files.
    Issue #2496: Updated to use _get_session_id_with_fallback for PPID-based fallback.
    """

    TEST_SESSION_ID = "test-session-12345678"

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        """Set up test environment."""
        # Mock log directory
        self.log_dir = tmp_path / "logs" / "metrics"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # Issue #1840: Use session-specific log file
        self.block_patterns_log = self.log_dir / f"block-patterns-{self.TEST_SESSION_ID}.jsonl"

        # Mock SESSION_DIR for file-based block tracking
        self.session_dir = tmp_path / "session"
        self.session_dir.mkdir(parents=True, exist_ok=True)

        # Issue #2496: Mock _get_session_id_with_fallback for PPID-based fallback
        monkeypatch.setattr(
            block_patterns,
            "_get_session_id_with_fallback",
            lambda session_id=None: self.TEST_SESSION_ID,
        )

        # Mock block_patterns module's path functions
        monkeypatch.setattr(block_patterns, "_get_session_dir", lambda: self.session_dir)
        monkeypatch.setattr(
            block_patterns, "_get_metrics_log_dir", lambda project_dir=None: self.log_dir
        )

        # Mock EXECUTION_LOG_DIR to prevent actual log writes
        exec_log_dir = tmp_path / "logs" / "execution"
        exec_log_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(common, "EXECUTION_LOG_DIR", exec_log_dir)

    def test_last_block_recorded(self):
        """Block should record __last_block__ entry."""
        block_patterns.record_block("test-hook", "Block reason", {"command": "gh pr checks"})

        recent_blocks = block_patterns._load_recent_blocks()
        assert "__last_block__" in recent_blocks
        assert recent_blocks["__last_block__"]["hook"] == "test-hook"

    def test_recovery_action_logged_for_different_command(self):
        """Recovery action should be logged when different command succeeds."""
        # Block with one command
        block_patterns.record_block(
            "ci-wait-check", "Use ci-monitor.py", {"command": "gh pr checks --watch"}
        )

        # Success with different command
        block_patterns.check_block_resolution(
            "flow-state-updater", {"command": "python3 ci-monitor.py 1234"}
        )

        # Check for recovery log
        with self.block_patterns_log.open() as f:
            lines = f.readlines()
            recovery_logs = [json.loads(line) for line in lines if "block_recovery" in line]
            assert len(recovery_logs) == 1
            assert recovery_logs[0]["blocked_hook"] == "ci-wait-check"
            assert recovery_logs[0]["recovery"]["recovery_hook"] == "flow-state-updater"
            assert "ci-monitor.py" in recovery_logs[0]["recovery"]["recovery_action"]

    def test_no_recovery_for_same_command(self):
        """Same command retry should not be logged as recovery."""
        command = "gh pr checks --watch 1234"
        block_patterns.record_block("ci-wait-check", "Use ci-monitor.py", {"command": command})

        # Success with same command
        block_patterns.check_block_resolution("ci-wait-check", {"command": command})

        # Should not have recovery log (only block_resolved)
        with self.block_patterns_log.open() as f:
            lines = f.readlines()
            recovery_logs = [json.loads(line) for line in lines if "block_recovery" in line]
            assert len(recovery_logs) == 0

    def test_recovery_clears_last_block(self):
        """Recovery action should clear __last_block__ entry."""
        block_patterns.record_block(
            "ci-wait-check", "Use ci-monitor.py", {"command": "gh pr checks --watch"}
        )

        # Verify __last_block__ exists
        assert "__last_block__" in block_patterns._load_recent_blocks()

        # Success with different command
        block_patterns.check_block_resolution(
            "flow-state-updater", {"command": "python3 ci-monitor.py 1234"}
        )

        # __last_block__ should be cleared
        assert "__last_block__" not in block_patterns._load_recent_blocks()

    def test_stale_last_block_cleared(self):
        """Stale __last_block__ should be cleared without logging.

        Note: Uses _check_block_resolution to test the actual execution flow,
        which loads and cleans recent blocks before checking recovery.
        """
        # Record block with old timestamp (outside recovery window)
        recent_blocks = block_patterns._load_recent_blocks()
        recent_blocks["__last_block__"] = {
            "block_id": "old-block",
            "hook": "test-hook",
            "timestamp": time.time() - 120,  # 2 minutes ago (past 60s window)
            "command_preview": "old command",
        }
        block_patterns._save_recent_blocks(recent_blocks)

        # Check via normal flow (which loads & cleans recent blocks)
        block_patterns.check_block_resolution("different-hook", {"command": "new command"})

        # __last_block__ should be cleared but no recovery logged
        log_file = self.block_patterns_log
        if log_file.exists():
            with log_file.open() as f:
                lines = f.readlines()
                recovery_logs = [json.loads(line) for line in lines if "block_recovery" in line]
                assert len(recovery_logs) == 0
        # If file doesn't exist, no recovery was logged (expected behavior)

    def test_no_false_recovery_after_resolution(self):
        """After block is resolved, subsequent approvals should not cause false recovery logs.

        Codex CLI review P2 fix: __last_block__ should be cleared when block is resolved,
        preventing false recovery logs for unrelated subsequent approvals.
        """
        # Block a command
        block_patterns.record_block("test-hook", "Block reason", {"command": "git push"})

        # Resolve by same command succeeding
        block_patterns.check_block_resolution("test-hook", {"command": "git push"})

        # __last_block__ should be cleared after resolution
        assert "__last_block__" not in block_patterns._load_recent_blocks()

        # Another approval from different hook should NOT be logged as recovery
        block_patterns.check_block_resolution("different-hook", {"command": "different command"})

        # Check no recovery logs exist
        with self.block_patterns_log.open() as f:
            lines = f.readlines()
            recovery_logs = [json.loads(line) for line in lines if "block_recovery" in line]
            assert len(recovery_logs) == 0
