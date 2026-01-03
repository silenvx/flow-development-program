#!/usr/bin/env python3
"""Tests for merge-check.py - specific fix claim and self-verification module.

Covers:
- is_specific_fix_claim function
- EXPLICIT_NOT_VERIFIED_PATTERN
- Self-verification with negation
"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

HOOK_PATH = Path(__file__).parent.parent / "merge-check.py"

# Import submodules for correct mock targets after modularization (Issue #1756)
import fix_verification_checker


class TestIsSpecificFixClaim:
    """Tests for is_specific_fix_claim function (Issue #856).

    This function detects if a fix claim contains specific evidence like:
    - File path references: file.py:10, src/utils.ts:25
    - Commit hash references: in abc1234, commit abc1234def (requires prefix)
    """

    def setup_method(self):
        """Load the module."""

        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_detect_file_with_line_number(self):
        """Should detect file.py:10 pattern."""
        assert self.module.is_specific_fix_claim("Fixed: Updated merge-check.py:50")

    def test_detect_file_with_line_range(self):
        """Should detect file.py:10-20 pattern."""
        assert self.module.is_specific_fix_claim("Fixed: Updated merge-check.py:50-60")

    def test_detect_path_with_line_number(self):
        """Should detect path/to/file.ts:25 pattern."""
        assert self.module.is_specific_fix_claim("Fixed: Updated src/utils/helpers.ts:25")

    def test_detect_relative_path(self):
        """Should detect ./path/to/file.tsx:10 pattern."""
        assert self.module.is_specific_fix_claim("Fixed: Updated ./components/Button.tsx:10")

    def test_detect_absolute_path(self):
        """Should detect /absolute/path/file.py:10 pattern."""
        assert self.module.is_specific_fix_claim("Fixed: Updated /app/main.py:10")
        assert self.module.is_specific_fix_claim("Fixed: Updated /usr/local/src/utils.ts:25")

    def test_detect_bare_filename(self):
        """Should detect bare filename with known extension."""
        assert self.module.is_specific_fix_claim("Fixed: Updated common.py")

    def test_detect_common_extensions(self):
        """Should detect various common file extensions."""
        extensions = [
            "py",
            "ts",
            "tsx",
            "js",
            "jsx",
            "yml",
            "yaml",
            "json",
            "md",
            "sh",
            # Build / package management
            "gradle",
            "gemspec",
            "bazel",
            "lock",
        ]
        for ext in extensions:
            assert self.module.is_specific_fix_claim(f"Fixed: Updated file.{ext}")

    def test_detect_short_commit_hash(self):
        """Should detect 7-character commit hash."""
        assert self.module.is_specific_fix_claim("Fixed in abc1234: Updated the code")

    def test_detect_full_commit_hash(self):
        """Should detect full 40-character commit hash."""
        assert self.module.is_specific_fix_claim(
            "Fixed in abc1234567890def1234567890abc1234567890a: Updated the code"
        )

    def test_detect_commit_prefix(self):
        """Should detect 'commit abc1234' pattern."""
        assert self.module.is_specific_fix_claim("Fixed in commit abc1234: Updated the code")

    def test_not_detect_generic_fix(self):
        """Should NOT detect generic fix without specific references."""
        assert not self.module.is_specific_fix_claim("Fixed: Updated the code.")

    def test_not_detect_short_hex(self):
        """Should NOT detect hex strings shorter than 7 characters."""
        assert not self.module.is_specific_fix_claim("Fixed: Error code abc12")

    def test_not_detect_common_words(self):
        """Should NOT detect common words that look like filenames."""
        # "the" is too short, "Updated" doesn't have valid extension
        assert not self.module.is_specific_fix_claim("Fixed: Updated the validation")

    def test_not_detect_bare_hex_without_prefix(self):
        """Should NOT detect bare hex string without 'in ' or 'commit ' prefix.

        This prevents false positives like "Fixed error 1234567" being treated
        as containing a commit hash reference.
        """
        assert not self.module.is_specific_fix_claim("Fixed error 1234567: Updated the code")
        assert not self.module.is_specific_fix_claim("Fixed abc1234def: Updated the code")

    def test_detect_commit_hash_requires_prefix(self):
        """Should require 'in ' or 'commit ' prefix for commit hash detection."""
        # With prefix - should detect
        assert self.module.is_specific_fix_claim("Fixed in abc1234: Updated")
        assert self.module.is_specific_fix_claim("commit abc1234: Fixed")
        # Without prefix - should NOT detect (unless there's a file reference)
        assert not self.module.is_specific_fix_claim("Fixed abc1234: Updated")

    def test_not_detect_resubmit_as_commit(self):
        """Should NOT detect 'resubmit abc1234' as commit hash (word boundary check).

        Words like "resubmit", "recommit", "precommit" should not trigger commit
        hash detection because the "in" is part of another word.
        """
        assert not self.module.is_specific_fix_claim("resubmit abc1234: Updated")
        assert not self.module.is_specific_fix_claim("recommit abc1234: Updated")
        assert not self.module.is_specific_fix_claim("precommit abc1234: Updated")

    def test_not_detect_url_as_file_reference(self):
        """Should NOT detect URLs as file references (Issue #887).

        URLs should not match, including those with source file extensions.
        The pattern uses negative lookbehinds to exclude URL paths.
        """
        # Standard URLs without source extensions
        assert not self.module.is_specific_fix_claim("http://example.com:8080")
        assert not self.module.is_specific_fix_claim("https://api.example.com:443/path")
        assert not self.module.is_specific_fix_claim("Running on localhost:3000")
        # URLs with source file extensions (should NOT match)
        assert not self.module.is_specific_fix_claim("http://example.py:8080")
        assert not self.module.is_specific_fix_claim("https://example.ts:3000")
        # GitHub-style URLs with file paths (should NOT match)
        assert not self.module.is_specific_fix_claim(
            "https://github.com/user/repo/blob/main/src/file.py:10"
        )

    def test_not_detect_ip_address_as_file_reference(self):
        """Should NOT detect IP addresses as file references (Issue #887).

        IP addresses like 192.168.1.1:8080 should not match.
        """
        assert not self.module.is_specific_fix_claim("192.168.1.1:8080")
        assert not self.module.is_specific_fix_claim("10.0.0.1:3000")
        assert not self.module.is_specific_fix_claim("Server at 127.0.0.1:8000")

    def test_not_detect_version_as_file_reference(self):
        """Should NOT detect version numbers as file references (Issue #887).

        Version strings like v1.2.3:4567 should not match.
        """
        assert not self.module.is_specific_fix_claim("v1.2.3:4567")
        assert not self.module.is_specific_fix_claim("Version 2.0.1:5000")

    def test_detect_special_build_files(self):
        """Should detect special build files like Makefile, Dockerfile.

        These files don't have extensions, so they need special handling.
        """
        assert self.module.is_specific_fix_claim("Fixed: Updated Makefile:10")
        assert self.module.is_specific_fix_claim("Fixed: Updated Dockerfile:5")
        assert self.module.is_specific_fix_claim("Fixed: Updated Jenkinsfile:20")
        assert self.module.is_specific_fix_claim("Fixed: Updated Makefile")
        assert self.module.is_specific_fix_claim("Fixed: Updated Dockerfile")
        # Path-based build files should also work
        assert self.module.is_specific_fix_claim("Fixed: Updated src/Makefile:10")
        assert self.module.is_specific_fix_claim("Fixed: Updated docker/Dockerfile:5")

    def test_detect_uppercase_extensions(self):
        """Should detect uppercase file extensions (case-insensitive matching).

        Windows paths and git diffs often show uppercase extensions.
        """
        assert self.module.is_specific_fix_claim("Fixed: FILE.PY:10")
        assert self.module.is_specific_fix_claim("Fixed: SRC/UTILS.TS:25")
        assert self.module.is_specific_fix_claim("Fixed: Config.JSON")
        assert self.module.is_specific_fix_claim("Fixed: README.MD")


class TestExplicitNotVerifiedPattern:
    """Tests for EXPLICIT_NOT_VERIFIED_PATTERN (Issue #856).

    This pattern detects explicit "not verified" statements (without colon)
    to override self-verification from specific fix claims.
    """

    def setup_method(self):
        """Load the module."""

        spec = importlib.util.spec_from_file_location("merge_check", HOOK_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_matches_not_verified(self):
        """Should match 'not verified'."""
        pat = self.module.EXPLICIT_NOT_VERIFIED_PATTERN
        assert pat.search("not verified yet") is not None
        assert pat.search("Not verified.") is not None

    def test_matches_unverified(self):
        """Should match 'unverified'."""
        pat = self.module.EXPLICIT_NOT_VERIFIED_PATTERN
        assert pat.search("still unverified") is not None
        assert pat.search("Unverified.") is not None

    def test_matches_never_verified(self):
        """Should match 'never verified'."""
        pat = self.module.EXPLICIT_NOT_VERIFIED_PATTERN
        assert pat.search("never verified this") is not None

    def test_matches_havent_verified(self):
        """Should match \"haven't verified\"."""
        pat = self.module.EXPLICIT_NOT_VERIFIED_PATTERN
        assert pat.search("haven't verified it") is not None

    def test_matches_couldnt_verify(self):
        """Should match \"couldn't verify\"."""
        pat = self.module.EXPLICIT_NOT_VERIFIED_PATTERN
        assert pat.search("couldn't verify due to error") is not None

    def test_matches_not_yet_verified(self):
        """Should match 'not yet verified' with intermediate word."""
        pat = self.module.EXPLICIT_NOT_VERIFIED_PATTERN
        assert pat.search("not yet verified") is not None
        assert pat.search("Fixed: merge-check.py:50. Not yet verified.") is not None

    def test_matches_havent_fully_verified(self):
        """Should match \"haven't fully verified\" with intermediate word."""
        pat = self.module.EXPLICIT_NOT_VERIFIED_PATTERN
        assert pat.search("haven't fully verified this") is not None
        assert pat.search("I haven't actually verified the fix yet") is not None

    def test_matches_up_to_two_intermediate_words(self):
        """Should match negation with up to 2 intermediate words before 'verified'."""
        pat = self.module.EXPLICIT_NOT_VERIFIED_PATTERN
        # 2 intermediate words - should match
        assert pat.search("not yet actually verified") is not None
        assert (
            pat.search("haven't really fully verified it") is not None
        )  # 2 words between haven't and verified

    def test_does_not_match_long_sentences(self):
        """Should NOT match long sentences where negation is far from 'verified'."""
        pat = self.module.EXPLICIT_NOT_VERIFIED_PATTERN
        # This sentence has many words between "haven't" and "verified"
        assert pat.search("I haven't reviewed the code changes and verified") is None

    def test_does_not_match_plain_verified(self):
        """Should NOT match plain 'verified' without negation."""
        pat = self.module.EXPLICIT_NOT_VERIFIED_PATTERN
        assert pat.search("verified: confirmed") is None
        assert pat.search("I verified the fix") is None

    def test_case_insensitive(self):
        """Should be case-insensitive."""
        pat = self.module.EXPLICIT_NOT_VERIFIED_PATTERN
        assert pat.search("NOT VERIFIED") is not None
        assert pat.search("Not Verified") is not None
        assert pat.search("UNVERIFIED") is not None

    def test_matches_verify_forms(self):
        """Should match 'verify' forms (not just 'verified')."""
        pat = self.module.EXPLICIT_NOT_VERIFIED_PATTERN
        assert pat.search("couldn't verify") is not None
        assert pat.search("didn't verify locally") is not None
        assert pat.search("could not verify the fix") is not None
        # Note: "haven't been able to verify" has 3 intermediate words (been, able, to)
        # which exceeds our 2-word limit, so it won't match. This is expected behavior.

    def test_matches_verify_with_intermediate_words(self):
        """Should match 'verify' with intermediate words."""
        pat = self.module.EXPLICIT_NOT_VERIFIED_PATTERN
        assert pat.search("couldn't fully verify") is not None
        assert pat.search("Fixed: merge-check.py:50. Couldn't verify locally.") is not None

    def test_does_not_match_plain_verify(self):
        """Should NOT match plain 'verify' without negation."""
        pat = self.module.EXPLICIT_NOT_VERIFIED_PATTERN
        assert pat.search("I will verify this later") is None
        assert pat.search("Please verify the fix") is None


class TestSelfVerificationWithNegation:
    """Integration tests for self-verification with explicit negation (Issue #856).

    When a fix claim contains specific evidence (file path/commit hash) AND
    explicitly says "not verified", it should NOT be treated as self-verified.
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

    def test_explicit_negation_overrides_self_verification(self):
        """Fix claim with file path BUT 'Not verified yet' should NOT self-verify."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    {"body": "Fixed: merge-check.py:50. Not verified yet.\n\n-- Claude Code"}
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
                result = self.module.check_resolved_without_verification("123")

        # SHOULD detect as unverified - explicit negation overrides self-verification
        assert len(result) == 1

    def test_unverified_keyword_overrides_self_verification(self):
        """Fix claim with commit hash BUT 'Unverified' should NOT self-verify."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    # Note: "Fixed:" is the fix claim, "in abc1234" triggers commit detection
                    {"body": "Fixed: Updated in abc1234. Unverified.\n\n-- Claude Code"}
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
                result = self.module.check_resolved_without_verification("123")

        # SHOULD detect as unverified - "unverified" overrides self-verification
        assert len(result) == 1

    def test_file_path_without_negation_self_verifies(self):
        """Fix claim with file path and NO negation should self-verify."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[{"body": "Fixed: merge-check.py:50\n\n-- Claude Code"}],
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
                result = self.module.check_resolved_without_verification("123")

        # Should NOT detect - file path makes it self-verified
        assert len(result) == 0
