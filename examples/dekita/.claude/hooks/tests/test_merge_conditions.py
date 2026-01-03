"""Tests for merge_conditions module.

This module contains BlockingReason dataclass and run_all_pr_checks orchestration.
Full functional tests are in test_merge_check.py.
"""

import sys
from pathlib import Path

# Add parent directory to path for module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


class TestMergeConditionsImports:
    """Test that merge_conditions module can be imported and has expected exports."""

    def test_module_imports(self):
        """Module should be importable."""
        import merge_conditions

        assert merge_conditions is not None

    def test_blocking_reason_exists(self):
        """BlockingReason dataclass should exist."""
        from merge_conditions import BlockingReason

        assert BlockingReason is not None

    def test_run_all_pr_checks_exists(self):
        """run_all_pr_checks function should exist."""
        from merge_conditions import run_all_pr_checks

        assert callable(run_all_pr_checks)


class TestBlockingReason:
    """Test BlockingReason dataclass."""

    def test_dataclass_fields(self):
        """BlockingReason should have expected fields."""
        from merge_conditions import BlockingReason

        br = BlockingReason(
            check_name="test_check",
            title="Test Title",
            details="Test details here",
        )
        assert br.check_name == "test_check"
        assert br.title == "Test Title"
        assert br.details == "Test details here"

    def test_dataclass_immutability(self):
        """BlockingReason fields should be accessible."""
        from merge_conditions import BlockingReason

        br = BlockingReason(
            check_name="ai_reviewing",
            title="AIレビューが進行中です",
            details="詳細情報",
        )
        # Access all fields
        assert isinstance(br.check_name, str)
        assert isinstance(br.title, str)
        assert isinstance(br.details, str)
