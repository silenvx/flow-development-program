#!/usr/bin/env python3
"""Tests for merge-check.py - verification patterns module.

Covers:
- FixClaimKeyword dataclass
- VERIFICATION_POSITIVE_PATTERN and VERIFICATION_NEGATION_PATTERN
- has_valid_verification function
- NUMERIC_CLAIM_PATTERN and NUMERIC_VERIFICATION_PATTERN
- check_numeric_claims_verified function
"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

HOOK_PATH = Path(__file__).parent.parent / "merge-check.py"

# Import submodules for correct mock targets after modularization (Issue #1756)
# These imports enable tests to mock functions at their actual definition locations
import fix_verification_checker


class TestFixClaimKeyword:
    """Tests for FixClaimKeyword dataclass (Issue #462)."""

    def setup_method(self):
        """Load the module."""

        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_dataclass_is_frozen(self):
        """FixClaimKeyword should be immutable (frozen)."""
        keyword = self.module.FixClaimKeyword("test:", "Test", ":")
        with pytest.raises(AttributeError):
            keyword.pattern = "modified"

    def test_keywords_have_correct_structure(self):
        """All FIX_CLAIM_KEYWORDS should have pattern and display_name."""
        for keyword in self.module.FIX_CLAIM_KEYWORDS:
            assert isinstance(keyword.pattern, str)
            assert isinstance(keyword.display_name, str)
            assert len(keyword.pattern) > 0
            assert len(keyword.display_name) > 0

    def test_display_names_are_readable(self):
        """Display names should be human-readable (not raw patterns)."""
        expected_display_names = {
            "fixed:": "Fixed",
            "already addressed:": "Already addressed",
            "added ": "Added",
            "updated ": "Updated",
            "changed ": "Changed",
            "implemented ": "Implemented",
            "修正済み": "修正済み",
            "対応済み": "対応済み",
        }
        for keyword in self.module.FIX_CLAIM_KEYWORDS:
            assert keyword.display_name == expected_display_names[keyword.pattern]


class TestVerificationPatterns:
    """Tests for VERIFICATION_POSITIVE_PATTERN and VERIFICATION_NEGATION_PATTERN (Issue #462)."""

    def setup_method(self):
        """Load the module."""

        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_positive_pattern_matches_verified(self):
        """VERIFICATION_POSITIVE_PATTERN should match 'verified:'."""
        assert self.module.VERIFICATION_POSITIVE_PATTERN.search("Verified: confirmed") is not None
        assert self.module.VERIFICATION_POSITIVE_PATTERN.search("verified: works") is not None
        assert self.module.VERIFICATION_POSITIVE_PATTERN.search("VERIFIED: OK") is not None

    def test_positive_pattern_word_boundary(self):
        """VERIFICATION_POSITIVE_PATTERN should use word boundary to exclude 'unverified:'."""
        # Word boundary \b in \bverified: means there must be a non-word character
        # (or start of string) before "verified". In "unverified:", "n" and "v" are
        # both word characters, so no boundary exists there.
        # Therefore, \bverified: should NOT match "unverified:".
        assert self.module.VERIFICATION_POSITIVE_PATTERN.search("unverified: not done") is None

        # But it SHOULD match when there's a proper word boundary
        assert self.module.VERIFICATION_POSITIVE_PATTERN.search("verified: done") is not None
        assert self.module.VERIFICATION_POSITIVE_PATTERN.search("Was verified: OK") is not None

    def test_negation_pattern_matches_not_verified(self):
        """VERIFICATION_NEGATION_PATTERN should match 'not verified:'."""
        assert self.module.VERIFICATION_NEGATION_PATTERN.search("Not verified: skipped") is not None
        assert self.module.VERIFICATION_NEGATION_PATTERN.search("not verified: todo") is not None

    def test_negation_pattern_matches_unverified(self):
        """VERIFICATION_NEGATION_PATTERN should match 'unverified:'."""
        assert self.module.VERIFICATION_NEGATION_PATTERN.search("Unverified: pending") is not None
        assert (
            self.module.VERIFICATION_NEGATION_PATTERN.search("unverified: needs work") is not None
        )

    def test_negation_pattern_matches_never_verified(self):
        """VERIFICATION_NEGATION_PATTERN should match 'never verified:'."""
        assert (
            self.module.VERIFICATION_NEGATION_PATTERN.search("never verified: blocked") is not None
        )

    def test_negation_pattern_matches_havent_verified(self):
        """VERIFICATION_NEGATION_PATTERN should match "haven't verified:" (Issue #462)."""
        assert (
            self.module.VERIFICATION_NEGATION_PATTERN.search("haven't verified: no time")
            is not None
        )

    def test_negation_pattern_matches_couldnt_verified(self):
        """VERIFICATION_NEGATION_PATTERN should match "couldn't verified:" (Issue #462)."""
        assert (
            self.module.VERIFICATION_NEGATION_PATTERN.search("couldn't verified: error") is not None
        )

    def test_negation_pattern_matches_could_not_verified(self):
        """VERIFICATION_NEGATION_PATTERN should match "could not verified:" (Issue #462)."""
        assert (
            self.module.VERIFICATION_NEGATION_PATTERN.search("could not verified: blocked")
            is not None
        )

    def test_negation_pattern_matches_did_not_verified(self):
        """VERIFICATION_NEGATION_PATTERN should match "did not verified:" (Issue #462)."""
        assert (
            self.module.VERIFICATION_NEGATION_PATTERN.search("did not verified: skipped")
            is not None
        )

    def test_negation_pattern_matches_didnt_verified(self):
        """VERIFICATION_NEGATION_PATTERN should match "didn't verified:" (Issue #462)."""
        assert (
            self.module.VERIFICATION_NEGATION_PATTERN.search("didn't verified: forgot") is not None
        )

    def test_negation_pattern_matches_cannot_verified(self):
        """VERIFICATION_NEGATION_PATTERN should match "cannot verified:" (Issue #462)."""
        assert (
            self.module.VERIFICATION_NEGATION_PATTERN.search("cannot verified: no access")
            is not None
        )

    def test_negation_pattern_matches_cant_verified(self):
        """VERIFICATION_NEGATION_PATTERN should match "can't verified:" (Issue #462)."""
        assert (
            self.module.VERIFICATION_NEGATION_PATTERN.search("can't verified: locked") is not None
        )

    def test_negation_pattern_matches_wont_verified(self):
        """VERIFICATION_NEGATION_PATTERN should match "won't verified:" (Issue #462)."""
        assert (
            self.module.VERIFICATION_NEGATION_PATTERN.search("won't verified: not needed")
            is not None
        )

    def test_negation_pattern_matches_will_not_verified(self):
        """VERIFICATION_NEGATION_PATTERN should match "will not verified:" (Issue #462)."""
        assert (
            self.module.VERIFICATION_NEGATION_PATTERN.search("will not verified: later") is not None
        )

    def test_negation_pattern_does_not_match_plain_verified(self):
        """VERIFICATION_NEGATION_PATTERN should NOT match plain 'verified:'."""
        assert self.module.VERIFICATION_NEGATION_PATTERN.search("Verified: confirmed") is None
        assert self.module.VERIFICATION_NEGATION_PATTERN.search("verified: works") is None


class TestHasValidVerification:
    """Tests for has_valid_verification function (Issue #462).

    This function checks if text contains at least one valid (non-negated) verification.
    """

    def setup_method(self):
        """Load the module."""

        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_plain_verified_returns_true(self):
        """Plain 'verified:' should return True."""
        assert self.module.has_valid_verification("Verified: confirmed")
        assert self.module.has_valid_verification("verified: works")

    def test_negated_verified_returns_false(self):
        """Negated 'verified:' should return False."""
        assert not self.module.has_valid_verification("Not verified: skipped")
        assert not self.module.has_valid_verification("unverified: pending")
        assert not self.module.has_valid_verification("haven't verified: no time")

    def test_mixed_negated_and_valid_returns_true(self):
        """Comment with both negated and valid verification should return True.

        This is the key fix from Codex review: a comment like
        "Previously unverified: pending. Verified: confirmed." should count
        as verified because there's at least one non-negated "verified:".
        """
        text = "Previously unverified: pending. Verified: confirmed."
        assert self.module.has_valid_verification(text)

    def test_multiple_negated_returns_false(self):
        """Multiple negated verifications should return False."""
        text = "Not verified: first check. couldn't verified: second check."
        assert not self.module.has_valid_verification(text)

    def test_no_verified_returns_false(self):
        """Text without 'verified:' should return False."""
        assert not self.module.has_valid_verification("This is a test comment")
        assert not self.module.has_valid_verification("")

    def test_case_insensitive(self):
        """Should be case-insensitive."""
        assert self.module.has_valid_verification("VERIFIED: confirmed")
        assert self.module.has_valid_verification("Verified: OK")
        assert not self.module.has_valid_verification("NOT VERIFIED: skipped")

    def test_word_ending_in_un_not_treated_as_negation(self):
        """Words ending in 'un' like 'run' should NOT trigger negation.

        This is a regression test from Codex review: 'run verified:' should be
        treated as valid verification, not negated by matching 'un verified:'.
        """
        assert self.module.has_valid_verification("run verified: passed")
        assert self.module.has_valid_verification("Manual run verified: OK")
        assert self.module.has_valid_verification("fun verified: test")


class TestNumericClaimPatterns:
    """Tests for NUMERIC_CLAIM_PATTERN and NUMERIC_VERIFICATION_PATTERN."""

    def setup_method(self):
        """Load the module."""

        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_numeric_claim_pattern_should_be(self):
        """Should match 'should be X' patterns."""
        assert self.module.NUMERIC_CLAIM_PATTERN.search("should be 33 characters")
        assert self.module.NUMERIC_CLAIM_PATTERN.search("This should be 100")

    def test_numeric_claim_pattern_japanese(self):
        """Should match Japanese numeric patterns."""
        assert self.module.NUMERIC_CLAIM_PATTERN.search("値は10に変更")
        assert self.module.NUMERIC_CLAIM_PATTERN.search("32文字になっている")
        assert self.module.NUMERIC_CLAIM_PATTERN.search("5行追加")
        assert self.module.NUMERIC_CLAIM_PATTERN.search("3個の要素")

    def test_numeric_claim_pattern_units(self):
        """Should match numeric patterns with units."""
        assert self.module.NUMERIC_CLAIM_PATTERN.search("10 characters")
        assert self.module.NUMERIC_CLAIM_PATTERN.search("100 bytes")
        assert self.module.NUMERIC_CLAIM_PATTERN.search("5 lines")
        assert self.module.NUMERIC_CLAIM_PATTERN.search("3 items")

    def test_numeric_claim_pattern_no_match(self):
        """Should not match non-numeric text."""
        assert not self.module.NUMERIC_CLAIM_PATTERN.search("Please fix this issue")
        assert not self.module.NUMERIC_CLAIM_PATTERN.search("Consider using a different approach")

    def test_numeric_claim_pattern_not_match_line_references(self):
        """Should NOT match line references like '行目' or '行付近' (Issue #889)."""
        # Line references are positional, not numeric claims
        assert not self.module.NUMERIC_CLAIM_PATTERN.search("4-7行目付近")
        assert not self.module.NUMERIC_CLAIM_PATTERN.search("10行目に移動")
        assert not self.module.NUMERIC_CLAIM_PATTERN.search("100行目")
        assert not self.module.NUMERIC_CLAIM_PATTERN.search("5行付近")
        # But should still match actual line count claims
        assert self.module.NUMERIC_CLAIM_PATTERN.search("5行追加")
        assert self.module.NUMERIC_CLAIM_PATTERN.search("10行削除すべき")

    def test_numeric_verification_pattern_japanese(self):
        """Should match Japanese verification patterns."""
        assert self.module.NUMERIC_VERIFICATION_PATTERN.search("検証済み: 実際は32文字")
        assert self.module.NUMERIC_VERIFICATION_PATTERN.search("確認済み: OK")
        assert self.module.NUMERIC_VERIFICATION_PATTERN.search("実際は32")

    def test_numeric_verification_pattern_english(self):
        """Should match English verification patterns."""
        assert self.module.NUMERIC_VERIFICATION_PATTERN.search("Verified: confirmed")
        assert self.module.NUMERIC_VERIFICATION_PATTERN.search("counted 32 characters")
        assert self.module.NUMERIC_VERIFICATION_PATTERN.search("actually 10 items")

    def test_numeric_verification_pattern_issue_reference(self):
        """Should match Issue reference patterns (Issue #1679).

        When a numeric claim is deferred to a separate Issue, it's considered
        valid as the issue has been acknowledged and tracked for follow-up.
        """
        # Japanese patterns
        assert self.module.NUMERIC_VERIFICATION_PATTERN.search("Issue #1652に記録済み")
        assert self.module.NUMERIC_VERIFICATION_PATTERN.search("#1652 に記録しました")
        assert self.module.NUMERIC_VERIFICATION_PATTERN.search("Issue #123 として追跡")
        # Gemini review: Also cover "#123 として追跡" pattern without "Issue" prefix
        assert self.module.NUMERIC_VERIFICATION_PATTERN.search("#123 として追跡しました")
        # English patterns
        assert self.module.NUMERIC_VERIFICATION_PATTERN.search("recorded in #456")
        # Issue #1735: Support "recorded in issue #" variation
        assert self.module.NUMERIC_VERIFICATION_PATTERN.search("recorded in issue #456")
        assert self.module.NUMERIC_VERIFICATION_PATTERN.search("recorded  in    issue  #789")
        assert self.module.NUMERIC_VERIFICATION_PATTERN.search("Issue #789 for follow-up")
        # Copilot review: Also test followup without hyphen
        assert self.module.NUMERIC_VERIFICATION_PATTERN.search("Issue #789 for followup")
        # Codex review: Should NOT match plain issue references without tracking context
        assert not self.module.NUMERIC_VERIFICATION_PATTERN.search("see Issue #123")
        assert not self.module.NUMERIC_VERIFICATION_PATTERN.search("Issue #456 について")
        assert not self.module.NUMERIC_VERIFICATION_PATTERN.search("refer to #789")
        # Codex review: Should NOT match negated or compound words
        assert not self.module.NUMERIC_VERIFICATION_PATTERN.search("unrecorded in #123")
        # Issue #1738: Should NOT match "not recorded" phrase
        assert not self.module.NUMERIC_VERIFICATION_PATTERN.search("not recorded in #123")
        # Issue #1744: Should NOT match "never recorded" phrase
        assert not self.module.NUMERIC_VERIFICATION_PATTERN.search("never recorded in #123")
        assert not self.module.NUMERIC_VERIFICATION_PATTERN.search("never recorded in issue #456")


class TestCheckNumericClaimsVerified:
    """Tests for check_numeric_claims_verified function (Issue #858).

    This function checks if AI review comments with numeric claims have proper verification.
    Background: PR #851 - Copilot claimed "33 characters" but it was actually 32.
    """

    def setup_method(self):
        """Load the module."""

        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def _make_graphql_response(self, threads: list[dict]) -> str:
        """Create a mock GraphQL response JSON string."""
        return json.dumps(
            {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": threads}}}}}
        )

    def _make_thread(
        self,
        first_author: str,
        first_body: str,
        recent_comments: list[dict],
        is_resolved: bool = True,
        thread_id: str = "thread_1",
    ) -> dict:
        """Create a mock thread object."""
        return {
            "id": thread_id,
            "isResolved": is_resolved,
            "firstComment": {"nodes": [{"body": first_body, "author": {"login": first_author}}]},
            "recentComments": {"nodes": recent_comments},
        }

    def test_detect_numeric_claim_without_verification(self):
        """Should detect AI numeric claim without verification comment."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="This should be 33 characters",
                recent_comments=[{"body": "修正しました\n\n-- Claude Code"}],
                is_resolved=True,
            )
        ]

        with patch.object(
            fix_verification_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_numeric_claims_verified("123")

        assert len(result) == 1
        assert "33 characters" in result[0]["body"]

    def test_ignore_numeric_claim_with_verification(self):
        """Should ignore numeric claim that has verification."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="This should be 33 characters",
                recent_comments=[
                    {"body": "検証済み: 実際は32文字でした\n修正しました\n\n-- Claude Code"}
                ],
                is_resolved=True,
            )
        ]

        with patch.object(
            fix_verification_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_numeric_claims_verified("123")

        assert len(result) == 0

    def test_ignore_numeric_claim_with_issue_reference(self):
        """Should ignore numeric claim when response contains Issue reference (Issue #1679, #1736).

        When a numeric claim is deferred to a separate Issue (e.g., "Issue #1652に記録済み"),
        it means the claim has been acknowledged and tracked for follow-up.
        """
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="This should be 33 characters",
                recent_comments=[{"body": "Issue #1652に記録済み\n\n-- Claude Code"}],
                is_resolved=True,
            )
        ]

        with patch.object(
            fix_verification_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_numeric_claims_verified("123")

        assert len(result) == 0

    def test_ignore_numeric_claim_with_recorded_in_issue(self):
        """Should ignore numeric claim with 'recorded in issue #' response (Issue #1735)."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="This should be 33 characters",
                recent_comments=[{"body": "recorded in issue #456\n\n-- Claude Code"}],
                is_resolved=True,
            )
        ]

        with patch.object(
            fix_verification_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_numeric_claims_verified("123")

        assert len(result) == 0

    def test_ignore_non_ai_reviewer(self):
        """Should ignore threads not started by AI reviewers."""
        threads = [
            self._make_thread(
                first_author="user123",
                first_body="This should be 33 characters",
                recent_comments=[{"body": "Modified\n\n-- Claude Code"}],
                is_resolved=True,
            )
        ]

        with patch.object(
            fix_verification_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_numeric_claims_verified("123")

        assert len(result) == 0

    def test_ignore_non_numeric_claims(self):
        """Should ignore AI comments without numeric claims."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this variable naming",
                recent_comments=[{"body": "修正しました\n\n-- Claude Code"}],
                is_resolved=True,
            )
        ]

        with patch.object(
            fix_verification_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_numeric_claims_verified("123")

        assert len(result) == 0

    def test_ignore_unresolved_threads(self):
        """Should ignore unresolved threads."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="This should be 33 characters",
                recent_comments=[{"body": "Working on it\n\n-- Claude Code"}],
                is_resolved=False,
            )
        ]

        with patch.object(
            fix_verification_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_numeric_claims_verified("123")

        assert len(result) == 0

    def test_japanese_verification_accepted(self):
        """Should accept Japanese verification phrases."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="32文字になっています",
                recent_comments=[{"body": "確認済み: 32文字で正しいです\n\n-- Claude Code"}],
                is_resolved=True,
            )
        ]

        with patch.object(
            fix_verification_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_numeric_claims_verified("123")

        assert len(result) == 0

    def test_codex_reviewer_detected(self):
        """Should detect numeric claims from Codex reviewer."""
        threads = [
            self._make_thread(
                first_author="codex-cloud[bot]",
                first_body="Array should have 5 items",
                recent_comments=[{"body": "Fixed\n\n-- Claude Code"}],
                is_resolved=True,
            )
        ]

        with patch.object(
            fix_verification_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_numeric_claims_verified("123")

        assert len(result) == 1

    def test_verification_in_separate_comment(self):
        """Should accept verification in a separate comment."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Value should be 100 bytes",
                recent_comments=[
                    {"body": "修正しました\n\n-- Claude Code"},
                    {"body": "Verified: counted 100 bytes, correct\n\n-- Claude Code"},
                ],
                is_resolved=True,
            )
        ]

        with patch.object(
            fix_verification_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_numeric_claims_verified("123")

        assert len(result) == 0

    def test_verification_without_claude_code_signature_not_counted(self):
        """Should not count verification without Claude Code signature."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Should be 50 lines",
                recent_comments=[
                    {"body": "修正しました\n\n-- Claude Code"},
                    {"body": "Verified: checked the lines"},  # No signature
                ],
                is_resolved=True,
            )
        ]

        with patch.object(
            fix_verification_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_numeric_claims_verified("123")

        assert len(result) == 1
