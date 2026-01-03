"""Tests for fix_verification_checker module.

This module contains fix claim verification functions extracted from merge-check.py.
Full functional tests are in test_merge_check.py.
"""

import sys
from pathlib import Path

# Add parent directory to path for module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


class TestFixVerificationCheckerImports:
    """Test that fix_verification_checker module can be imported and has expected exports."""

    def test_module_imports(self):
        """Module should be importable."""
        import fix_verification_checker

        assert fix_verification_checker is not None

    def test_check_resolved_without_verification_exists(self):
        """check_resolved_without_verification function should exist."""
        from fix_verification_checker import check_resolved_without_verification

        assert callable(check_resolved_without_verification)

    def test_check_numeric_claims_verified_exists(self):
        """check_numeric_claims_verified function should exist."""
        from fix_verification_checker import check_numeric_claims_verified

        assert callable(check_numeric_claims_verified)

    def test_has_valid_verification_exists(self):
        """has_valid_verification function should exist."""
        from fix_verification_checker import has_valid_verification

        assert callable(has_valid_verification)

    def test_is_specific_fix_claim_exists(self):
        """is_specific_fix_claim function should exist."""
        from fix_verification_checker import is_specific_fix_claim

        assert callable(is_specific_fix_claim)

    def test_patterns_exist(self):
        """Module should export expected patterns."""
        from fix_verification_checker import (
            EXPLICIT_NOT_VERIFIED_PATTERN,
            NUMERIC_CLAIM_PATTERN,
            NUMERIC_VERIFICATION_PATTERN,
            VERIFICATION_NEGATION_PATTERN,
            VERIFICATION_POSITIVE_PATTERN,
        )

        assert EXPLICIT_NOT_VERIFIED_PATTERN is not None
        assert NUMERIC_CLAIM_PATTERN is not None
        assert NUMERIC_VERIFICATION_PATTERN is not None
        assert VERIFICATION_NEGATION_PATTERN is not None
        assert VERIFICATION_POSITIVE_PATTERN is not None

    def test_fix_claim_keywords_exist(self):
        """FIX_CLAIM_KEYWORDS should exist and be a list."""
        from fix_verification_checker import FIX_CLAIM_KEYWORDS

        assert isinstance(FIX_CLAIM_KEYWORDS, list)
        assert len(FIX_CLAIM_KEYWORDS) > 0


class TestFixClaimKeyword:
    """Test FixClaimKeyword dataclass."""

    def test_dataclass_exists(self):
        """FixClaimKeyword dataclass should exist."""
        from fix_verification_checker import FixClaimKeyword

        assert FixClaimKeyword is not None

    def test_dataclass_fields(self):
        """FixClaimKeyword should have expected fields."""
        from fix_verification_checker import FixClaimKeyword

        kw = FixClaimKeyword(pattern="fixed:", display_name="Fixed", trailing_char=":")
        assert kw.pattern == "fixed:"
        assert kw.display_name == "Fixed"
        assert kw.trailing_char == ":"
