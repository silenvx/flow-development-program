"""Tests for analyze-review-quality.py script."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestLoadRecords:
    """Tests for load_records function."""

    def test_load_empty_file(self, tmp_path):
        """Should return empty list for empty file."""
        log_file = tmp_path / "review-quality.jsonl"
        log_file.write_text("")

        with patch("analyze_review_quality.REVIEW_QUALITY_LOG", log_file):
            from analyze_review_quality import load_records

            records = load_records()
            assert records == []

    def test_load_valid_records(self, tmp_path):
        """Should load valid JSONL records."""
        log_file = tmp_path / "review-quality.jsonl"
        records_data = [
            {"pr_number": "123", "comment_id": "1", "reviewer": "copilot"},
            {"pr_number": "123", "comment_id": "2", "reviewer": "codex_cli"},
        ]
        log_file.write_text("\n".join(json.dumps(r) for r in records_data))

        with patch("analyze_review_quality.REVIEW_QUALITY_LOG", log_file):
            from analyze_review_quality import load_records

            records = load_records()
            assert len(records) == 2
            assert records[0]["reviewer"] == "copilot"

    def test_load_skips_invalid_json(self, tmp_path):
        """Should skip invalid JSON lines."""
        log_file = tmp_path / "review-quality.jsonl"
        log_file.write_text('{"pr_number": "123"}\ninvalid json\n{"pr_number": "456"}\n')

        with patch("analyze_review_quality.REVIEW_QUALITY_LOG", log_file):
            from analyze_review_quality import load_records

            records = load_records()
            assert len(records) == 2

    def test_file_not_exists(self, tmp_path):
        """Should return empty list when file does not exist."""
        log_file = tmp_path / "nonexistent.jsonl"

        with patch("analyze_review_quality.REVIEW_QUALITY_LOG", log_file):
            from analyze_review_quality import load_records

            records = load_records()
            assert records == []


class TestMergeRecords:
    """Tests for merge_records function."""

    def test_merge_unique_comments(self):
        """Should keep all unique comments."""
        from analyze_review_quality import merge_records

        records = [
            {"pr_number": "123", "comment_id": "1", "reviewer": "copilot"},
            {"pr_number": "123", "comment_id": "2", "reviewer": "codex_cli"},
        ]
        merged = merge_records(records)
        assert len(merged) == 2

    def test_merge_response_overrides_initial(self):
        """Should let response records override initial records."""
        from analyze_review_quality import merge_records

        records = [
            {"pr_number": "123", "comment_id": "1", "reviewer": "copilot"},
            {
                "pr_number": "123",
                "comment_id": "1",
                "resolution": "accepted",
                "record_type": "response",
            },
        ]
        merged = merge_records(records)
        assert len(merged) == 1
        assert merged["123:1"]["resolution"] == "accepted"

    def test_merge_keeps_latest_response(self):
        """Should keep the latest response record."""
        from analyze_review_quality import merge_records

        records = [
            {
                "pr_number": "123",
                "comment_id": "1",
                "resolution": "rejected",
                "record_type": "response",
            },
            {
                "pr_number": "123",
                "comment_id": "1",
                "resolution": "accepted",
                "record_type": "response",
            },
        ]
        merged = merge_records(records)
        # Both are responses, last one wins
        assert merged["123:1"]["resolution"] == "accepted"


class TestFilterRecords:
    """Tests for filter_records function."""

    def test_filter_by_since(self):
        """Should filter records by since date."""
        from analyze_review_quality import filter_records

        records = [
            {"timestamp": "2025-12-01T10:00:00+00:00", "comment_id": "1"},
            {"timestamp": "2025-12-15T10:00:00+00:00", "comment_id": "2"},
            {"timestamp": "2025-12-20T10:00:00+00:00", "comment_id": "3"},
        ]
        filtered = filter_records(records, since="2025-12-10", until=None)
        assert len(filtered) == 2
        assert filtered[0]["comment_id"] == "2"

    def test_filter_by_until(self):
        """Should filter records by until date."""
        from analyze_review_quality import filter_records

        records = [
            {"timestamp": "2025-12-01T10:00:00+00:00", "comment_id": "1"},
            {"timestamp": "2025-12-15T10:00:00+00:00", "comment_id": "2"},
            {"timestamp": "2025-12-20T10:00:00+00:00", "comment_id": "3"},
        ]
        filtered = filter_records(records, since=None, until="2025-12-15")
        assert len(filtered) == 2
        assert filtered[1]["comment_id"] == "2"

    def test_filter_by_range(self):
        """Should filter records by date range."""
        from analyze_review_quality import filter_records

        records = [
            {"timestamp": "2025-12-01T10:00:00+00:00", "comment_id": "1"},
            {"timestamp": "2025-12-15T10:00:00+00:00", "comment_id": "2"},
            {"timestamp": "2025-12-20T10:00:00+00:00", "comment_id": "3"},
        ]
        filtered = filter_records(records, since="2025-12-10", until="2025-12-18")
        assert len(filtered) == 1
        assert filtered[0]["comment_id"] == "2"


class TestCalculateStats:
    """Tests for calculate_stats function."""

    def test_calculate_overall_stats(self):
        """Should calculate overall statistics."""
        from analyze_review_quality import calculate_stats

        merged = {
            "123:1": {"resolution": "accepted", "validity": "valid"},
            "123:2": {"resolution": "rejected", "validity": "invalid"},
            "123:3": {},  # No resolution yet
        }
        stats = calculate_stats(merged)
        assert stats["total_comments"] == 3
        assert stats["with_resolution"] == 2
        assert stats["pending_resolution"] == 1
        assert stats["resolution_breakdown"]["accepted"] == 1
        assert stats["resolution_breakdown"]["rejected"] == 1
        assert stats["validity_breakdown"]["valid"] == 1
        assert stats["validity_breakdown"]["invalid"] == 1


class TestCalculateByReviewer:
    """Tests for calculate_by_reviewer function."""

    def test_group_by_reviewer(self):
        """Should group statistics by reviewer."""
        from analyze_review_quality import calculate_by_reviewer

        merged = {
            "123:1": {"reviewer": "copilot", "resolution": "accepted", "validity": "valid"},
            "123:2": {"reviewer": "copilot", "resolution": "rejected", "validity": "invalid"},
            "123:3": {"reviewer": "codex_cli", "resolution": "accepted", "validity": "valid"},
        }
        stats = calculate_by_reviewer(merged)
        assert stats["copilot"]["total"] == 2
        assert stats["copilot"]["accepted"] == 1
        assert stats["copilot"]["acceptance_rate"] == 50.0
        assert stats["codex_cli"]["total"] == 1
        assert stats["codex_cli"]["acceptance_rate"] == 100.0


class TestCalculateByCategory:
    """Tests for calculate_by_category function."""

    def test_group_by_category(self):
        """Should group statistics by category."""
        from analyze_review_quality import calculate_by_category

        merged = {
            "123:1": {"category": "style", "resolution": "accepted", "validity": "valid"},
            "123:2": {"category": "style", "resolution": "rejected", "validity": "invalid"},
            "123:3": {"category": "bug", "resolution": "accepted", "validity": "valid"},
        }
        stats = calculate_by_category(merged)
        assert stats["style"]["total"] == 2
        assert stats["style"]["validity_rate"] == 50.0
        assert stats["bug"]["total"] == 1
        assert stats["bug"]["validity_rate"] == 100.0


class TestMainFunction:
    """Tests for main function."""

    def test_no_records_message(self, tmp_path, capsys):
        """Should show message when no records found."""
        log_file = tmp_path / "review-quality.jsonl"

        test_args = ["analyze_review_quality.py"]
        with patch("sys.argv", test_args):
            with patch("analyze_review_quality.REVIEW_QUALITY_LOG", log_file):
                from analyze_review_quality import main

                result = main()

        assert result == 0
        captured = capsys.readouterr()
        assert "見つかりません" in captured.out

    def test_json_output(self, tmp_path, capsys):
        """Should output JSON when --json flag is used."""
        log_file = tmp_path / "review-quality.jsonl"
        log_file.write_text(
            '{"pr_number": "123", "comment_id": "1", "reviewer": "copilot", "timestamp": "2025-12-20T10:00:00+00:00"}\n'
        )

        test_args = ["analyze_review_quality.py", "--json"]
        with patch("sys.argv", test_args):
            with patch("analyze_review_quality.REVIEW_QUALITY_LOG", log_file):
                from analyze_review_quality import main

                result = main()

        assert result == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "overall" in output
        assert "by_reviewer" in output
        assert "by_category" in output
