"""Tests for ai_review_checker module.

This module contains AI reviewer status check functions extracted from merge-check.py.
Full functional tests are in test_merge_check.py.
"""

import sys
from pathlib import Path

# Add parent directory to path for module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


class TestAiReviewCheckerImports:
    """Test that ai_review_checker module can be imported and has expected exports."""

    def test_module_imports(self):
        """Module should be importable."""
        import ai_review_checker

        assert ai_review_checker is not None

    def test_check_ai_reviewing_exists(self):
        """check_ai_reviewing function should exist."""
        from ai_review_checker import check_ai_reviewing

        assert callable(check_ai_reviewing)

    def test_check_ai_review_error_exists(self):
        """check_ai_review_error function should exist."""
        from ai_review_checker import check_ai_review_error

        assert callable(check_ai_review_error)

    def test_request_copilot_review_exists(self):
        """request_copilot_review function should exist."""
        from ai_review_checker import request_copilot_review

        assert callable(request_copilot_review)

    def test_constants_exist(self):
        """Module should export expected constants."""
        from ai_review_checker import (
            AI_REVIEW_ERROR_PATTERN,
            AI_REVIEW_ERROR_RETRY_THRESHOLD,
            COPILOT_REVIEWER_LOGIN,
        )

        assert AI_REVIEW_ERROR_PATTERN is not None
        assert AI_REVIEW_ERROR_RETRY_THRESHOLD >= 1
        assert COPILOT_REVIEWER_LOGIN is not None
