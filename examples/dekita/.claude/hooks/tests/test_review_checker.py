"""Tests for review_checker module.

This module contains review comment/thread check functions extracted from merge-check.py.
Full functional tests are in test_merge_check.py.
"""

import sys
from pathlib import Path

# Add parent directory to path for module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


class TestReviewCheckerImports:
    """Test that review_checker module can be imported and has expected exports."""

    def test_module_imports(self):
        """Module should be importable."""
        import review_checker

        assert review_checker is not None

    def test_check_dismissal_without_issue_exists(self):
        """check_dismissal_without_issue function should exist."""
        from review_checker import check_dismissal_without_issue

        assert callable(check_dismissal_without_issue)

    def test_check_resolved_without_response_exists(self):
        """check_resolved_without_response function should exist."""
        from review_checker import check_resolved_without_response

        assert callable(check_resolved_without_response)

    def test_check_unresolved_ai_threads_exists(self):
        """check_unresolved_ai_threads function should exist."""
        from review_checker import check_unresolved_ai_threads

        assert callable(check_unresolved_ai_threads)

    def test_constants_exist(self):
        """Module should export expected constants."""
        from review_checker import (
            ACTION_KEYWORDS,
            DISMISSAL_EXCLUSIONS,
            DISMISSAL_KEYWORDS,
        )

        assert isinstance(DISMISSAL_KEYWORDS, list)
        assert isinstance(ACTION_KEYWORDS, list)
        assert isinstance(DISMISSAL_EXCLUSIONS, list)
        assert len(DISMISSAL_KEYWORDS) > 0
