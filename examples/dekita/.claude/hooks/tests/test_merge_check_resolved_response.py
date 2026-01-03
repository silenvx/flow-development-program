#!/usr/bin/env python3
"""Tests for merge-check.py - resolved response and verification module.

Covers:
- check_resolved_without_response function
- check_resolved_without_verification function
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

HOOK_PATH = Path(__file__).parent.parent / "merge-check.py"

# Import submodules for correct mock targets after modularization (Issue #1756)
# These imports enable tests to mock functions at their actual definition locations
import fix_verification_checker
import review_checker


def run_hook(input_data: dict) -> dict | None:
    """Run the hook with given input and return the result.

    Returns None if no output (silent approval per design principle).
    """
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    if not result.stdout.strip():
        return None  # Silent approval
    return json.loads(result.stdout)


class TestCheckResolvedWithoutResponse:
    """Tests for check_resolved_without_response function.

    This function checks if resolved threads have Claude Code responses.
    Uses two GraphQL query aliases:
    - firstComment: Gets the original AI review comment for author identification
    - recentComments: Gets the latest 20 comments to find Claude Code responses
    """

    def setup_method(self):
        """Load the module."""
        import importlib.util

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
        """Create a mock thread object with the new structure.

        Args:
            first_author: Login of the first comment author
            first_body: Body of the first comment
            recent_comments: List of recent comment dicts with 'body' key
            is_resolved: Whether the thread is resolved
            thread_id: ID of the thread
        """
        return {
            "id": thread_id,
            "isResolved": is_resolved,
            "firstComment": {"nodes": [{"body": first_body, "author": {"login": first_author}}]},
            "recentComments": {"nodes": recent_comments},
        }

    def test_detect_resolved_without_response(self):
        """Should detect resolved thread without Claude Code response."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[{"body": "Some comment without signature"}],
                is_resolved=True,
            )
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_resolved_without_response("123")

        assert len(result) == 1
        assert result[0]["author"] == "copilot[bot]"

    def test_ignore_resolved_with_response(self):
        """Should ignore resolved thread that has Claude Code response."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[{"body": "Fixed!\n\n-- Claude Code"}],
                is_resolved=True,
            )
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_resolved_without_response("123")

        assert len(result) == 0

    def test_ignore_unresolved_threads(self):
        """Should ignore unresolved threads."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[],
                is_resolved=False,
            )
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_resolved_without_response("123")

        assert len(result) == 0

    def test_ignore_non_ai_threads(self):
        """Should ignore threads started by non-AI users."""
        threads = [
            self._make_thread(
                first_author="human-user",
                first_body="My comment",
                recent_comments=[],
                is_resolved=True,
            )
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_resolved_without_response("123")

        assert len(result) == 0

    def test_find_response_in_recent_comments(self):
        """Should find Claude Code response in recent comments (not just first)."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    {"body": "Comment 1"},
                    {"body": "Comment 2"},
                    {"body": "Fixed!\n\n-- Claude Code"},  # Response in 3rd comment
                ],
                is_resolved=True,
            )
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_resolved_without_response("123")

        assert len(result) == 0

    def test_first_comment_author_used_for_identification(self):
        """Should use firstComment for author identification (issue #152 fix)."""
        # This test simulates a thread with >20 comments where the first comment
        # author (AI reviewer) would not be in `comments(last: 20)`
        threads = [
            self._make_thread(
                first_author="copilot[bot]",  # AI reviewer
                first_body="Original AI review comment",
                # Recent comments don't include the original first comment
                recent_comments=[
                    {"body": "Reply 1 from human"},
                    {"body": "Reply 2 from human"},
                ],
                is_resolved=True,
            )
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_resolved_without_response("123")

        # Should detect this as resolved without response
        # because the AI reviewer's thread has no Claude Code response
        assert len(result) == 1
        assert result[0]["author"] == "copilot[bot]"

    def test_codex_author_detected(self):
        """Should detect codex-bot as AI reviewer."""
        threads = [
            self._make_thread(
                first_author="codex-bot",
                first_body="Review comment",
                recent_comments=[],
                is_resolved=True,
            )
        ]

        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout=self._make_graphql_response(threads)
                )
                result = self.module.check_resolved_without_response("123")

        assert len(result) == 1
        assert result[0]["author"] == "codex-bot"

    def test_fail_open_on_api_error(self):
        """Should return empty list on API errors (fail open)."""
        with patch.object(
            review_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stdout="")
                result = self.module.check_resolved_without_response("123")

        assert result == []


class TestCheckResolvedWithoutVerification:
    """Tests for check_resolved_without_verification function.

    This function checks if resolved threads have fix claims without verification.
    - Only checks comments with Claude Code signature for fix claims
    - Uses regex to avoid false positives from "Not verified:" or "Unverified:"
    """

    def setup_method(self):
        """Load the module."""
        import importlib.util

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

    def test_detect_fix_claim_without_verification(self):
        """Should detect fix claim without verification."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[{"body": "Fixed: Updated the code.\n\n-- Claude Code"}],
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

        assert len(result) == 1
        # Uses display_name from FixClaimKeyword (Issue #462)
        assert result[0]["fix_claim"] == "Fixed"

    def test_ignore_fix_claim_with_verification(self):
        """Should ignore fix claim that has verification."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    {"body": "Fixed: Updated the code.\n\n-- Claude Code"},
                    {"body": "Verified: Confirmed the fix is in place.\n\n-- Claude Code"},
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

        assert len(result) == 0

    def test_not_match_not_verified_as_verification(self):
        """Should NOT treat 'Not verified:' as verification (false positive prevention)."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    {"body": "Fixed: Updated the code.\n\n-- Claude Code"},
                    {"body": "Not verified: I couldn't confirm this.\n\n-- Claude Code"},
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

        # Should still detect as unverified because "Not verified:" is not a verification
        assert len(result) == 1

    def test_not_match_unverified_as_verification(self):
        """Should NOT treat 'Unverified:' as verification (false positive prevention)."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    {"body": "Fixed: Updated the code.\n\n-- Claude Code"},
                    {"body": "Unverified: This needs more testing.\n\n-- Claude Code"},
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

        # Should still detect as unverified because "Unverified:" is not a verification
        assert len(result) == 1

    def test_ignore_non_claude_code_comments(self):
        """Should ignore comments without Claude Code signature."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    {"body": "Fixed: Updated the code."},  # No signature
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

        # Should not detect because the comment doesn't have Claude Code signature
        assert len(result) == 0

    def test_ignore_non_ai_reviewer_threads(self):
        """Should ignore threads not started by AI reviewers."""
        threads = [
            self._make_thread(
                first_author="human-user",  # Not an AI reviewer
                first_body="Please fix this bug",
                recent_comments=[{"body": "Fixed: Updated the code.\n\n-- Claude Code"}],
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

        assert len(result) == 0

    def test_fail_open_on_api_error(self):
        """Should return empty list on API errors (fail open)."""
        with patch.object(
            fix_verification_checker, "get_repo_owner_and_name", return_value=("owner", "repo")
        ):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stdout="")
                result = self.module.check_resolved_without_verification("123")

        assert result == []

    def test_not_match_never_verified_as_verification(self):
        """Should NOT treat 'never verified:' as verification."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    {"body": "Fixed: Updated the code.\n\n-- Claude Code"},
                    {"body": "never verified: skipped testing.\n\n-- Claude Code"},
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

        # Should still detect as unverified
        assert len(result) == 1

    def test_detect_codex_bot_author(self):
        """Should detect threads started by codex-bot variant."""
        threads = [
            self._make_thread(
                first_author="codex-bot",  # Codex variant
                first_body="Please fix this bug",
                recent_comments=[{"body": "Fixed: Updated the code.\n\n-- Claude Code"}],
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

        assert len(result) == 1

    def test_detect_japanese_fix_claim(self):
        """Should detect Japanese fix claim keywords."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[{"body": "修正済み\n\n-- Claude Code"}],
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

        assert len(result) == 1
        assert result[0]["fix_claim"] == "修正済み"

    def test_fix_claim_and_verification_in_same_comment(self):
        """Should recognize verification in same comment as fix claim."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    {
                        "body": "Fixed: Updated the code.\n\nVerified: Confirmed working.\n\n-- Claude Code"
                    }
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

        # Should NOT detect because verification is present
        assert len(result) == 0

    def test_verification_without_signature_not_counted(self):
        """Verification without Claude Code signature should NOT count."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    {"body": "Fixed: Updated the code.\n\n-- Claude Code"},
                    {"body": "Verified: Looks good."},  # No signature
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

        # Should detect as unverified because verification lacks signature
        assert len(result) == 1

    def test_not_match_havent_verified_as_verification(self):
        """Should NOT treat "haven't verified:" as verification (Issue #462)."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    {"body": "Fixed: Updated the code.\n\n-- Claude Code"},
                    {"body": "I haven't verified: this needs more testing.\n\n-- Claude Code"},
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

        # Should still detect as unverified
        assert len(result) == 1

    def test_not_match_could_not_verified_as_verification(self):
        """Should NOT treat "could not verified:" as verification (Issue #462)."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    {"body": "Fixed: Updated the code.\n\n-- Claude Code"},
                    {"body": "could not verified: environment issue.\n\n-- Claude Code"},
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

        # Should still detect as unverified
        assert len(result) == 1

    def test_not_match_couldnt_verified_as_verification(self):
        """Should NOT treat "couldn't verified:" as verification (Issue #462)."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    {"body": "Fixed: Updated the code.\n\n-- Claude Code"},
                    {"body": "couldn't verified: no access.\n\n-- Claude Code"},
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

        # Should still detect as unverified
        assert len(result) == 1

    def test_not_match_did_not_verified_as_verification(self):
        """Should NOT treat "did not verified:" as verification (Issue #462)."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    {"body": "Fixed: Updated the code.\n\n-- Claude Code"},
                    {"body": "did not verified: skipped.\n\n-- Claude Code"},
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

        # Should still detect as unverified
        assert len(result) == 1

    def test_not_match_cannot_verified_as_verification(self):
        """Should NOT treat "cannot verified:" as verification (Issue #462)."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    {"body": "Fixed: Updated the code.\n\n-- Claude Code"},
                    {"body": "cannot verified: blocked.\n\n-- Claude Code"},
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

        # Should still detect as unverified
        assert len(result) == 1

    def test_mixed_negated_and_valid_verification_counts(self):
        """Comment with both negated and valid verification should count as verified.

        This is the key fix from Codex review (Issue #462): a comment like
        "Previously unverified: pending. Verified: confirmed." should count
        as verified because there's at least one non-negated "verified:".
        """
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    {"body": "Fixed: Updated the code.\n\n-- Claude Code"},
                    {
                        "body": "Previously unverified: pending. Verified: confirmed.\n\n-- Claude Code"
                    },
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

        # Should NOT detect as unverified because there's a valid verification
        assert len(result) == 0

    def test_specific_fix_with_file_path_reference(self):
        """Should NOT require explicit Verified when fix has file:line reference (Issue #856)."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    {"body": "Fixed: Updated the code in merge-check.py:50\n\n-- Claude Code"},
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

        # Should NOT detect as unverified because file path reference is self-verifying
        assert len(result) == 0

    def test_specific_fix_with_full_path_reference(self):
        """Should NOT require explicit Verified when fix has full file path (Issue #856)."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    {"body": "Fixed: Updated src/utils/helpers.ts:25-30\n\n-- Claude Code"},
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

        # Should NOT detect as unverified because file path reference is self-verifying
        assert len(result) == 0

    def test_specific_fix_with_commit_hash_reference(self):
        """Should NOT require explicit Verified when fix has commit hash (Issue #856)."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    {"body": "Fixed in abc1234: Updated the validation logic.\n\n-- Claude Code"},
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

        # Should NOT detect as unverified because commit hash reference is self-verifying
        assert len(result) == 0

    def test_generic_fix_without_specific_reference(self):
        """Should still require Verified for generic fix claims (Issue #856)."""
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    {"body": "Fixed: Updated the code.\n\n-- Claude Code"},
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

        # Should detect as unverified because generic fix claim needs explicit verification
        assert len(result) == 1

    def test_generic_fix_followed_by_unrelated_file_reference(self):
        """Should NOT mark as verified when later comment has file ref but isn't fix (Issue #856).

        Regression test: ensures self-verification only applies to the comment
        containing the fix claim, not to unrelated later comments.
        """
        threads = [
            self._make_thread(
                first_author="copilot[bot]",
                first_body="Please fix this bug",
                recent_comments=[
                    # Generic fix claim (no specific reference)
                    {"body": "Fixed: Updated the code.\n\n-- Claude Code"},
                    # Later comment with file reference but NOT a fix claim
                    {"body": "Note: see config.json for details.\n\n-- Claude Code"},
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

        # Should STILL detect as unverified because the fix claim itself
        # doesn't have specific evidence (the file ref is in a later comment)
        assert len(result) == 1
