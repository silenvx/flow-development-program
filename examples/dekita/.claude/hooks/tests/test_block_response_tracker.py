#!/usr/bin/env python3
"""Unit tests for block-response-tracker.py

Issue #2282: Tests for session ID validation in load functions.
"""

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for lib module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Dynamic import for hyphenated module name
HOOK_PATH = Path(__file__).parent.parent / "block-response-tracker.py"
_spec = importlib.util.spec_from_file_location("block_response_tracker", HOOK_PATH)
block_response_tracker = importlib.util.module_from_spec(_spec)
sys.modules["block_response_tracker"] = block_response_tracker
_spec.loader.exec_module(block_response_tracker)

load_block_patterns = block_response_tracker.load_block_patterns
load_recovery_events = block_response_tracker.load_recovery_events


class TestLoadBlockPatternsSecurityValidation:
    """Tests for session ID validation in load_block_patterns (Issue #2282)."""

    def test_rejects_path_traversal_session_id(self):
        """Should return empty list for path traversal session IDs."""
        # Path traversal attempts should be rejected before file access
        assert load_block_patterns("../../../etc/passwd") == []
        assert load_block_patterns("session/../../../etc") == []
        assert load_block_patterns("..") == []

    def test_rejects_empty_session_id(self):
        """Should return empty list for empty session ID."""
        assert load_block_patterns("") == []

    def test_rejects_special_characters_session_id(self):
        """Should return empty list for session IDs with special characters."""
        assert load_block_patterns("session;rm -rf /") == []
        assert load_block_patterns("session$(cat /etc/passwd)") == []
        assert load_block_patterns("session|cat") == []

    def test_accepts_valid_uuid_session_id(self):
        """Should accept valid UUID format session IDs (returns [] when file doesn't exist)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(
                block_response_tracker,
                "get_metrics_log_dir",
                return_value=Path(tmpdir),
            ):
                # Valid UUID should pass validation but return [] if file doesn't exist
                result = load_block_patterns("aac956f9-4701-4bca-98f4-d4f166716c73")
                assert result == []

    def test_loads_blocks_for_valid_session_id(self):
        """Should load block patterns for valid session ID when file exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            session_id = "test-session-123"
            log_file = tmpdir_path / f"block-patterns-{session_id}.jsonl"
            log_file.write_text(
                '{"type": "block", "hook": "test-hook", "block_id": "1"}\n'
                '{"type": "block", "hook": "another-hook", "block_id": "2"}\n'
            )

            with patch.object(
                block_response_tracker,
                "get_metrics_log_dir",
                return_value=tmpdir_path,
            ):
                result = load_block_patterns(session_id)
                assert len(result) == 2
                assert result[0]["hook"] == "test-hook"
                assert result[1]["hook"] == "another-hook"


class TestLoadRecoveryEventsSecurityValidation:
    """Tests for session ID validation in load_recovery_events (Issue #2282)."""

    def test_rejects_path_traversal_session_id(self):
        """Should return empty set for path traversal session IDs."""
        assert load_recovery_events("../../../etc/passwd") == set()
        assert load_recovery_events("session/../../../etc") == set()
        assert load_recovery_events("..") == set()

    def test_rejects_empty_session_id(self):
        """Should return empty set for empty session ID."""
        assert load_recovery_events("") == set()

    def test_rejects_special_characters_session_id(self):
        """Should return empty set for session IDs with special characters."""
        assert load_recovery_events("session;rm -rf /") == set()
        assert load_recovery_events("session$(cat /etc/passwd)") == set()
        assert load_recovery_events("session|cat") == set()

    def test_accepts_valid_uuid_session_id(self):
        """Should accept valid UUID format session IDs (returns empty set when file doesn't exist)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(
                block_response_tracker,
                "get_metrics_log_dir",
                return_value=Path(tmpdir),
            ):
                result = load_recovery_events("aac956f9-4701-4bca-98f4-d4f166716c73")
                assert result == set()

    def test_loads_recovery_events_for_valid_session_id(self):
        """Should load recovery events for valid session ID when file exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            session_id = "test-session-456"
            log_file = tmpdir_path / f"block-patterns-{session_id}.jsonl"
            log_file.write_text(
                '{"type": "block_resolved", "block_id": "block-1"}\n'
                '{"type": "block_recovery", "block_id": "block-2"}\n'
                '{"type": "block", "block_id": "block-3"}\n'  # Not a recovery event
            )

            with patch.object(
                block_response_tracker,
                "get_metrics_log_dir",
                return_value=tmpdir_path,
            ):
                result = load_recovery_events(session_id)
                assert result == {"block-1", "block-2"}
                assert "block-3" not in result
