#!/usr/bin/env python3
"""Tests for collect-pr-metrics.py script."""

import importlib.util
import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for imports
scripts_dir = Path(__file__).parent.parent
sys.path.insert(0, str(scripts_dir))

# Load module from file path (handles hyphenated filenames)
script_path = scripts_dir / "collect_pr_metrics.py"
spec = importlib.util.spec_from_file_location("collect_pr_metrics", script_path)
collect_pr_metrics = importlib.util.module_from_spec(spec)
sys.modules["collect_pr_metrics"] = collect_pr_metrics
spec.loader.exec_module(collect_pr_metrics)


class TestFetchPrDetails:
    """Tests for fetch_pr_details function."""

    @patch("subprocess.run")
    def test_successful_fetch(self, mock_run):
        """Successfully fetches PR details."""
        pr_data = {
            "number": 123,
            "title": "Test PR",
            "state": "MERGED",
            "createdAt": "2025-01-01T00:00:00Z",
            "mergedAt": "2025-01-01T01:00:00Z",
            "author": {"login": "testuser"},
            "reviews": [],
            "comments": [],
            "commits": [{"sha": "abc123"}],
            "additions": 10,
            "deletions": 5,
            "changedFiles": 2,
            "labels": [],
            "reviewDecision": "APPROVED",
        }
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(pr_data))

        result = collect_pr_metrics.fetch_pr_details(123)

        assert result is not None
        assert result["number"] == 123
        assert result["title"] == "Test PR"

    @patch("subprocess.run")
    def test_fetch_failure(self, mock_run):
        """Returns None on fetch failure."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = collect_pr_metrics.fetch_pr_details(123)

        assert result is None

    @patch("subprocess.run")
    def test_fetch_timeout(self, mock_run):
        """Handles timeout gracefully."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired("gh", 30)

        result = collect_pr_metrics.fetch_pr_details(123)

        assert result is None


class TestFetchPrChecks:
    """Tests for fetch_pr_checks function."""

    @patch("subprocess.run")
    def test_all_checks_passed(self, mock_run):
        """Correctly counts passed checks."""
        checks = [
            {"name": "build", "state": "SUCCESS", "conclusion": "SUCCESS"},
            {"name": "test", "state": "SUCCESS", "conclusion": "SUCCESS"},
        ]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(checks))

        result = collect_pr_metrics.fetch_pr_checks(123)

        assert result["total_checks"] == 2
        assert result["passed"] == 2
        assert result["failed"] == 0

    @patch("subprocess.run")
    def test_mixed_check_results(self, mock_run):
        """Correctly counts mixed check results."""
        checks = [
            {"name": "build", "state": "SUCCESS", "conclusion": "SUCCESS"},
            {"name": "test", "state": "FAILURE", "conclusion": "FAILURE"},
            {"name": "lint", "state": "PENDING", "conclusion": ""},
        ]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(checks))

        result = collect_pr_metrics.fetch_pr_checks(123)

        assert result["total_checks"] == 3
        assert result["passed"] == 1
        assert result["failed"] == 1
        assert result["pending"] == 1

    @patch("subprocess.run")
    def test_fetch_failure_returns_defaults(self, mock_run):
        """Returns default values on fetch failure."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = collect_pr_metrics.fetch_pr_checks(123)

        assert result["total_checks"] == 0
        assert result["passed"] == 0
        assert result["failed"] == 0
        assert result["pending"] == 0


class TestFetchReviewDetails:
    """Tests for fetch_review_details function."""

    @patch("subprocess.run")
    def test_counts_review_states(self, mock_run):
        """Correctly counts different review states."""
        reviews = [
            {"state": "APPROVED", "user": {"login": "reviewer1", "type": "User"}},
            {
                "state": "CHANGES_REQUESTED",
                "user": {"login": "reviewer2", "type": "User"},
            },
            {"state": "COMMENTED", "user": {"login": "reviewer3", "type": "User"}},
        ]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(reviews))

        result = collect_pr_metrics.fetch_review_details(123)

        assert result["total_reviews"] == 3
        assert result["approved"] == 1
        assert result["changes_requested"] == 1
        assert result["commented"] == 1
        assert result["human_reviews"] == 3

    @patch("subprocess.run")
    def test_identifies_ai_reviewers(self, mock_run):
        """Correctly identifies AI reviewers."""
        reviews = [
            {
                "state": "COMMENTED",
                "user": {"login": "copilot-pull-request-reviewer", "type": "Bot"},
            },
            {"state": "APPROVED", "user": {"login": "testuser", "type": "User"}},
        ]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(reviews))

        result = collect_pr_metrics.fetch_review_details(123)

        assert result["ai_reviews"] == 1
        assert result["human_reviews"] == 1

    @patch("subprocess.run")
    def test_identifies_all_ai_reviewer_patterns(self, mock_run):
        """Identifies all configured AI reviewer patterns."""
        reviews = [
            {"state": "COMMENTED", "user": {"login": "github-actions[bot]", "type": "Bot"}},
            {"state": "COMMENTED", "user": {"login": "codex-reviewer", "type": "Bot"}},
            {"state": "COMMENTED", "user": {"login": "openai-bot", "type": "Bot"}},
            {"state": "APPROVED", "user": {"login": "humanuser", "type": "User"}},
        ]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(reviews))

        result = collect_pr_metrics.fetch_review_details(123)

        assert result["ai_reviews"] == 3
        assert result["human_reviews"] == 1

    @patch("subprocess.run")
    def test_deduplicates_reviewers(self, mock_run):
        """Deduplicates multiple reviews from same user."""
        reviews = [
            {"state": "COMMENTED", "user": {"login": "reviewer1", "type": "User"}},
            {"state": "APPROVED", "user": {"login": "reviewer1", "type": "User"}},
        ]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(reviews))

        result = collect_pr_metrics.fetch_review_details(123)

        assert result["total_reviews"] == 2
        assert result["human_reviews"] == 1  # Same user counted once
        assert len(result["reviewers"]) == 1


class TestCalculateMetrics:
    """Tests for calculate_metrics function."""

    def test_calculates_cycle_time(self):
        """Correctly calculates cycle time in hours."""
        pr_data = {
            "number": 123,
            "title": "Test PR",
            "state": "MERGED",
            "createdAt": "2025-01-01T00:00:00Z",
            "mergedAt": "2025-01-01T02:30:00Z",
            "author": {"login": "testuser"},
            "additions": 10,
            "deletions": 5,
            "changedFiles": 2,
            "commits": [{}],
            "comments": [],
            "labels": [],
            "reviewDecision": "APPROVED",
        }
        checks = {"total_checks": 1, "passed": 1, "failed": 0, "pending": 0}
        reviews = {
            "total_reviews": 1,
            "approved": 1,
            "changes_requested": 0,
            "commented": 0,
            "ai_reviews": 0,
            "human_reviews": 1,
            "reviewers": ["reviewer1"],
        }

        result = collect_pr_metrics.calculate_metrics(pr_data, checks, reviews)

        assert result["cycle_time_hours"] == 2.5

    def test_handles_missing_merge_time(self):
        """Handles missing mergedAt (open PR)."""
        pr_data = {
            "number": 123,
            "title": "Test PR",
            "state": "OPEN",
            "createdAt": "2025-01-01T00:00:00Z",
            "mergedAt": None,
            "author": {"login": "testuser"},
            "additions": 10,
            "deletions": 5,
            "changedFiles": 2,
            "commits": [],
            "comments": [],
            "labels": [],
        }
        checks = {"total_checks": 0, "passed": 0, "failed": 0, "pending": 0}
        reviews = {
            "total_reviews": 0,
            "approved": 0,
            "changes_requested": 0,
            "commented": 0,
            "ai_reviews": 0,
            "human_reviews": 0,
            "reviewers": [],
        }

        result = collect_pr_metrics.calculate_metrics(pr_data, checks, reviews)

        assert result["cycle_time_hours"] is None

    def test_truncates_long_title(self):
        """Truncates titles longer than 100 characters."""
        long_title = "A" * 150
        pr_data = {
            "number": 123,
            "title": long_title,
            "state": "MERGED",
            "createdAt": "2025-01-01T00:00:00Z",
            "mergedAt": "2025-01-01T01:00:00Z",
            "author": {"login": "testuser"},
            "additions": 0,
            "deletions": 0,
            "changedFiles": 0,
            "commits": [],
            "comments": [],
            "labels": [],
        }
        checks = {"total_checks": 0, "passed": 0, "failed": 0, "pending": 0}
        reviews = {
            "total_reviews": 0,
            "approved": 0,
            "changes_requested": 0,
            "commented": 0,
            "ai_reviews": 0,
            "human_reviews": 0,
            "reviewers": [],
        }

        result = collect_pr_metrics.calculate_metrics(pr_data, checks, reviews)

        assert len(result["title"]) == 100


class TestRecordMetrics:
    """Tests for record_metrics function."""

    def test_writes_metrics_to_file(self):
        """Successfully writes metrics to log file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "pr-metrics.log"

            # Patch the log file path
            with (
                patch.object(collect_pr_metrics, "PR_METRICS_LOG", log_file),
                patch.object(collect_pr_metrics, "LOGS_DIR", Path(tmpdir)),
            ):
                metrics = {"pr_number": 123, "title": "Test"}
                collect_pr_metrics.record_metrics(metrics)

            assert log_file.exists()
            content = log_file.read_text()
            data = json.loads(content.strip())
            assert data["pr_number"] == 123


class TestCollectRecentPrs:
    """Tests for collect_recent_prs function."""

    @patch("subprocess.run")
    def test_filters_by_date(self, mock_run):
        """Filters PRs by merge date."""
        now = datetime.now(UTC)
        recent = (now - timedelta(days=1)).isoformat()
        old = (now - timedelta(days=10)).isoformat()

        prs = [
            {"number": 1, "mergedAt": recent},
            {"number": 2, "mergedAt": old},
        ]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(prs))

        result = collect_pr_metrics.collect_recent_prs(days=7)

        assert result == [1]

    @patch("subprocess.run")
    def test_returns_empty_on_failure(self, mock_run):
        """Returns empty list on API failure."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = collect_pr_metrics.collect_recent_prs()

        assert result == []


class TestIsAlreadyRecorded:
    """Tests for is_already_recorded function."""

    def test_returns_false_for_nonexistent_file(self):
        """Returns False when log file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "nonexistent.log"

            with patch.object(collect_pr_metrics, "PR_METRICS_LOG", log_file):
                result = collect_pr_metrics.is_already_recorded(123)

            assert not result

    def test_finds_recorded_pr(self):
        """Returns True when PR is already recorded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "pr-metrics.log"
            log_file.write_text('{"pr_number": 123, "title": "Test"}\n')

            with patch.object(collect_pr_metrics, "PR_METRICS_LOG", log_file):
                result = collect_pr_metrics.is_already_recorded(123)

            assert result

    def test_returns_false_for_unrecorded_pr(self):
        """Returns False when PR is not recorded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "pr-metrics.log"
            log_file.write_text('{"pr_number": 456, "title": "Other"}\n')

            with patch.object(collect_pr_metrics, "PR_METRICS_LOG", log_file):
                result = collect_pr_metrics.is_already_recorded(123)

            assert not result

    def test_handles_invalid_json_lines(self):
        """Handles invalid JSON lines gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "pr-metrics.log"
            log_file.write_text('invalid json\n{"pr_number": 123}\n')

            with patch.object(collect_pr_metrics, "PR_METRICS_LOG", log_file):
                result = collect_pr_metrics.is_already_recorded(123)

            assert result


class TestMain:
    """Tests for main function."""

    @patch.object(collect_pr_metrics, "fetch_pr_details")
    @patch.object(collect_pr_metrics, "fetch_pr_checks")
    @patch.object(collect_pr_metrics, "fetch_review_details")
    @patch.object(collect_pr_metrics, "record_metrics")
    @patch.object(collect_pr_metrics, "is_already_recorded")
    def test_collects_single_pr(
        self, mock_recorded, mock_record, mock_reviews, mock_checks, mock_details
    ):
        """Collects metrics for a single PR."""
        mock_recorded.return_value = False
        mock_details.return_value = {
            "number": 123,
            "title": "Test",
            "state": "MERGED",
            "createdAt": "2025-01-01T00:00:00Z",
            "mergedAt": "2025-01-01T01:00:00Z",
            "author": {"login": "test"},
            "additions": 0,
            "deletions": 0,
            "changedFiles": 0,
            "commits": [],
            "comments": [],
            "labels": [],
        }
        mock_checks.return_value = {
            "total_checks": 0,
            "passed": 0,
            "failed": 0,
            "pending": 0,
        }
        mock_reviews.return_value = {
            "total_reviews": 0,
            "approved": 0,
            "changes_requested": 0,
            "commented": 0,
            "ai_reviews": 0,
            "human_reviews": 0,
            "reviewers": [],
        }

        with patch.object(sys, "argv", ["script", "123"]):
            captured_output = StringIO()
            sys.stdout = captured_output
            try:
                collect_pr_metrics.main()
            finally:
                sys.stdout = sys.__stdout__

        mock_record.assert_called_once()
        assert "Collected PR #123" in captured_output.getvalue()

    @patch.object(collect_pr_metrics, "is_already_recorded")
    def test_skips_already_recorded_pr(self, mock_recorded):
        """Skips PRs that are already recorded."""
        mock_recorded.return_value = True

        with patch.object(sys, "argv", ["script", "123"]):
            captured_output = StringIO()
            sys.stdout = captured_output
            try:
                collect_pr_metrics.main()
            finally:
                sys.stdout = sys.__stdout__

        assert "1 skipped" in captured_output.getvalue()

    @patch.object(collect_pr_metrics, "fetch_pr_details")
    @patch.object(collect_pr_metrics, "record_metrics")
    @patch.object(collect_pr_metrics, "is_already_recorded")
    def test_handles_fetch_failure(self, mock_recorded, mock_record, mock_details):
        """Handles fetch_pr_details returning None."""
        mock_recorded.return_value = False
        mock_details.return_value = None

        with patch.object(sys, "argv", ["script", "123"]):
            captured_output = StringIO()
            sys.stdout = captured_output
            try:
                collect_pr_metrics.main()
            finally:
                sys.stdout = sys.__stdout__

        mock_record.assert_not_called()
        assert "Could not fetch PR #123" in captured_output.getvalue()

    @patch.object(collect_pr_metrics, "collect_recent_prs")
    @patch.object(collect_pr_metrics, "fetch_pr_details")
    @patch.object(collect_pr_metrics, "fetch_pr_checks")
    @patch.object(collect_pr_metrics, "fetch_review_details")
    @patch.object(collect_pr_metrics, "record_metrics")
    @patch.object(collect_pr_metrics, "is_already_recorded")
    def test_recent_flag_collects_multiple_prs(
        self, mock_recorded, mock_record, mock_reviews, mock_checks, mock_details, mock_recent
    ):
        """Collects multiple PRs when --recent flag is used."""
        mock_recent.return_value = [100, 101, 102]
        mock_recorded.return_value = False
        mock_details.return_value = {
            "number": 100,
            "title": "Test",
            "state": "MERGED",
            "createdAt": "2025-01-01T00:00:00Z",
            "mergedAt": "2025-01-01T01:00:00Z",
            "author": {"login": "test"},
            "additions": 0,
            "deletions": 0,
            "changedFiles": 0,
            "commits": [],
            "comments": [],
            "labels": [],
        }
        mock_checks.return_value = {
            "total_checks": 0,
            "passed": 0,
            "failed": 0,
            "pending": 0,
        }
        mock_reviews.return_value = {
            "total_reviews": 0,
            "approved": 0,
            "changes_requested": 0,
            "commented": 0,
            "ai_reviews": 0,
            "human_reviews": 0,
            "reviewers": [],
        }

        with patch.object(sys, "argv", ["script", "--recent"]):
            captured_output = StringIO()
            sys.stdout = captured_output
            try:
                collect_pr_metrics.main()
            finally:
                sys.stdout = sys.__stdout__

        mock_recent.assert_called_once()
        assert mock_record.call_count == 3
        assert "3 collected" in captured_output.getvalue()
