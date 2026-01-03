"""Tests for record-review-response.py script."""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# record-review-response.py has a hyphen in the name, so we need to import it dynamically
SCRIPT_PATH = Path(__file__).parent.parent / "record-review-response.py"
_spec = importlib.util.spec_from_file_location("record_review_response", SCRIPT_PATH)
record_review_response = importlib.util.module_from_spec(_spec)
sys.modules["record_review_response"] = record_review_response
_spec.loader.exec_module(record_review_response)

# Import symbols from the dynamically loaded module
record_response = record_review_response.record_response
infer_validity = record_review_response.infer_validity
main = record_review_response.main


class TestRecordReviewResponse:
    """Tests for record_response function."""

    @pytest.fixture
    def temp_log_file(self, tmp_path):
        """Create a temporary log file."""
        log_file = tmp_path / "review-quality.jsonl"
        with patch("record_review_response.REVIEW_QUALITY_LOG", log_file):
            yield log_file

    def test_record_accepted_response(self, temp_log_file):
        """Should record accepted resolution correctly."""
        with patch("record_review_response.REVIEW_QUALITY_LOG", temp_log_file):
            with patch(
                "record_review_response._get_session_id_fallback", return_value="test-session"
            ):
                from record_review_response import record_response

                record_response(
                    pr_number="123",
                    comment_id="456",
                    resolution="accepted",
                    validity=None,
                    category=None,
                    issue_created=None,
                    reason=None,
                )

        # Verify file contents
        content = temp_log_file.read_text().strip()
        record = json.loads(content)
        assert record["pr_number"] == 123
        assert record["comment_id"] == 456
        assert record["resolution"] == "accepted"
        assert record["validity"] == "valid"  # Inferred from accepted
        assert record["record_type"] == "response"

    def test_record_rejected_response(self, temp_log_file):
        """Should record rejected resolution with reason."""
        with patch("record_review_response.REVIEW_QUALITY_LOG", temp_log_file):
            with patch(
                "record_review_response._get_session_id_fallback", return_value="test-session"
            ):
                from record_review_response import record_response

                record_response(
                    pr_number="123",
                    comment_id="456",
                    resolution="rejected",
                    validity="invalid",
                    category="style",
                    issue_created=None,
                    reason="False positive",
                )

        content = temp_log_file.read_text().strip()
        record = json.loads(content)
        assert record["resolution"] == "rejected"
        assert record["validity"] == "invalid"
        assert record["category"] == "style"
        assert record["reason"] == "False positive"

    def test_record_issue_created_response(self, temp_log_file):
        """Should record issue_created resolution with issue number."""
        with patch("record_review_response.REVIEW_QUALITY_LOG", temp_log_file):
            with patch(
                "record_review_response._get_session_id_fallback", return_value="test-session"
            ):
                from record_review_response import record_response

                record_response(
                    pr_number="123",
                    comment_id="456",
                    resolution="issue_created",
                    validity=None,
                    category=None,
                    issue_created="789",
                    reason=None,
                )

        content = temp_log_file.read_text().strip()
        record = json.loads(content)
        assert record["resolution"] == "issue_created"
        assert record["validity"] == "valid"  # Issue creation implies valid
        assert record["issue_created"] == 789

    def test_infer_validity_from_resolution(self, temp_log_file):
        """Should infer validity when not explicitly provided."""
        with patch("record_review_response.REVIEW_QUALITY_LOG", temp_log_file):
            with patch(
                "record_review_response._get_session_id_fallback", return_value="test-session"
            ):
                from record_review_response import infer_validity

                assert infer_validity("accepted") == "valid"
                assert infer_validity("rejected") == "invalid"
                assert infer_validity("issue_created") == "valid"

    def test_invalid_pr_number_raises_valueerror(self, temp_log_file):
        """Should raise ValueError when pr_number is not numeric."""
        with patch("record_review_response.REVIEW_QUALITY_LOG", temp_log_file):
            with patch(
                "record_review_response._get_session_id_fallback", return_value="test-session"
            ):
                with pytest.raises(ValueError, match="Invalid numeric ID"):
                    record_response(
                        pr_number="abc",
                        comment_id="456",
                        resolution="accepted",
                        validity=None,
                        category=None,
                        issue_created=None,
                        reason=None,
                    )

    def test_invalid_comment_id_raises_valueerror(self, temp_log_file):
        """Should raise ValueError when comment_id is not numeric."""
        with patch("record_review_response.REVIEW_QUALITY_LOG", temp_log_file):
            with patch(
                "record_review_response._get_session_id_fallback", return_value="test-session"
            ):
                with pytest.raises(ValueError, match="Invalid numeric ID"):
                    record_response(
                        pr_number="123",
                        comment_id="xyz",
                        resolution="accepted",
                        validity=None,
                        category=None,
                        issue_created=None,
                        reason=None,
                    )

    def test_invalid_issue_created_skips_field(self, temp_log_file, capsys):
        """Should skip issue_created field and warn when value is not numeric."""
        with patch("record_review_response.REVIEW_QUALITY_LOG", temp_log_file):
            with patch(
                "record_review_response._get_session_id_fallback", return_value="test-session"
            ):
                record_response(
                    pr_number="123",
                    comment_id="456",
                    resolution="issue_created",
                    validity=None,
                    category=None,
                    issue_created="abc",  # Invalid numeric value
                    reason=None,
                )

        # Check warning was printed
        captured = capsys.readouterr()
        assert "Warning: issue_created must be numeric" in captured.err
        assert "abc" in captured.err
        assert "Skipping field" in captured.err

        # Check record was written but without issue_created field
        content = temp_log_file.read_text().strip()
        record = json.loads(content)
        assert "issue_created" not in record
        assert record["pr_number"] == 123
        assert record["comment_id"] == 456


class TestMainFunction:
    """Tests for main function."""

    def test_requires_issue_for_issue_created(self, capsys):
        """Should fail when resolution=issue_created without --issue."""
        test_args = [
            "record-review-response.py",
            "--pr",
            "123",
            "--comment-id",
            "456",
            "--resolution",
            "issue_created",
        ]
        with patch("sys.argv", test_args):
            from record_review_response import main

            result = main()

        assert result == 1
        captured = capsys.readouterr()
        assert "--issue is required" in captured.err

    def test_accepts_valid_arguments(self, tmp_path, capsys):
        """Should accept valid command line arguments."""
        log_file = tmp_path / "review-quality.jsonl"
        test_args = [
            "record-review-response.py",
            "--pr",
            "123",
            "--comment-id",
            "456",
            "--resolution",
            "accepted",
        ]
        with patch("sys.argv", test_args):
            with patch("record_review_response.REVIEW_QUALITY_LOG", log_file):
                with patch("record_review_response._get_session_id_fallback", return_value="test"):
                    from record_review_response import main

                    result = main()

        assert result == 0
        captured = capsys.readouterr()
        assert "Recorded response" in captured.out
