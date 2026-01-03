#!/usr/bin/env python3
"""Unit tests for ci_monitor.session module."""

import sys
from pathlib import Path

# Add scripts directory to path so we can import ci_monitor package
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the ci_monitor package for attribute access (Issue #2624)

# Import directly from ci_monitor package (Issue #2624)


# Issue #2454: TestArgumentParsing class removed
# These options have been removed: --json/--no-json, --wait-review/--no-wait-review
# JSON output and wait_review are now always enabled (hardcoded)


class TestSessionIdArgument:
    """Tests for --session-id argument parsing and validation (Issue #2310).

    These tests verify that the --session-id argument correctly validates UUID
    format and sets the CLAUDE_SESSION_ID environment variable.
    """

    @staticmethod
    def _create_test_parser():
        """Create a parser matching ci-monitor.py's session-id configuration."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("pr_numbers", nargs="+")
        parser.add_argument("--session-id", type=str, default=None)
        return parser

    def test_session_id_defaults_to_none(self):
        """Default should be session_id=None when not specified."""
        parser = self._create_test_parser()
        args = parser.parse_args(["123"])
        assert args.session_id is None

    def test_session_id_accepts_valid_uuid(self):
        """--session-id should accept a valid UUID."""
        parser = self._create_test_parser()
        valid_uuid = "8ea2a2a0-ad70-4eb8-92d0-20912e119f94"
        args = parser.parse_args(["123", "--session-id", valid_uuid])
        assert args.session_id == valid_uuid

    def test_is_valid_session_id_accepts_valid_uuid(self):
        """is_valid_session_id() should accept valid UUID format."""
        # Import the function from the actual module
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hooks"))
        from lib.session import is_valid_session_id

        # Valid UUIDs (standard hyphenated format)
        assert is_valid_session_id("8ea2a2a0-ad70-4eb8-92d0-20912e119f94") is True
        assert is_valid_session_id("00000000-0000-0000-0000-000000000000") is True

    def test_is_valid_session_id_rejects_invalid_format(self):
        """is_valid_session_id() should reject invalid UUID format."""
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hooks"))
        from lib.session import is_valid_session_id

        # Invalid formats
        assert is_valid_session_id("not-a-uuid") is False
        assert is_valid_session_id("ppid-12345") is False
        assert is_valid_session_id("") is False
        # UUID without hyphens (should be rejected for strict format)
        assert is_valid_session_id("8ea2a2a0ad704eb892d020912e119f94") is False

    def test_handle_session_id_arg_validates_and_returns(self):
        """handle_session_id_arg should validate and return session ID (Issue #2496).

        Issue #2496: Updated to test handle_session_id_arg instead of removed
        global state functions (set_hook_session_id, get_claude_session_id).
        """
        valid_uuid = "8ea2a2a0-ad70-4eb8-92d0-20912e119f94"

        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hooks"))
        from lib.session import handle_session_id_arg

        # handle_session_id_arg returns the validated session_id or None
        result = handle_session_id_arg(valid_uuid)
        assert result == valid_uuid

        # Invalid session_id returns None
        result = handle_session_id_arg("not-a-uuid")
        assert result is None

        # None returns None
        result = handle_session_id_arg(None)
        assert result is None
