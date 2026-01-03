#!/usr/bin/env python3
"""Tests for session-log-compressor.py hook."""

import gzip
import json
import subprocess
import tempfile
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "session-log-compressor.py"


def run_hook(input_data: dict) -> dict:
    """Run the hook with given input and return the result."""
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestSessionLogCompressor:
    """Tests for session-log-compressor hook."""

    def test_approve_with_empty_input(self):
        """Should approve with empty input."""
        result = run_hook({})
        assert result["decision"] == "approve"

    def test_approve_when_stop_hook_active(self):
        """Should approve immediately when stop_hook_active is True."""
        result = run_hook({"stop_hook_active": True})
        assert result["decision"] == "approve"

    def test_approve_with_transcript_path(self):
        """Should approve with transcript_path provided."""
        result = run_hook({"transcript_path": "/some/path/transcript.json"})
        assert result["decision"] == "approve"


class TestCompressRotatedLogs:
    """Tests for compress_rotated_logs function from common.py."""

    def test_compress_rotated_log_files(self):
        """Should compress rotated log files and remove originals."""
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent))
        from lib.execution import compress_rotated_logs

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)

            # Create rotated log files
            log1 = log_dir / "test.log.1"
            log2 = log_dir / "test.log.2"
            log1.write_text("Log content 1")
            log2.write_text("Log content 2")

            # Run compression
            compressed_count = compress_rotated_logs(log_dir)

            # Verify results
            assert compressed_count == 2
            assert not log1.exists()
            assert not log2.exists()
            assert (log_dir / "test.log.1.gz").exists()
            assert (log_dir / "test.log.2.gz").exists()

            # Verify compressed content
            with gzip.open(log_dir / "test.log.1.gz", "rt") as f:
                assert f.read() == "Log content 1"

    def test_compress_multi_digit_rotation(self):
        """Should compress logs with multi-digit rotation numbers (e.g., .log.10)."""
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent))
        from lib.execution import compress_rotated_logs

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)

            # Create rotated log files with multi-digit suffixes
            log1 = log_dir / "test.log.1"
            log10 = log_dir / "test.log.10"
            log99 = log_dir / "test.log.99"
            log1.write_text("Log content 1")
            log10.write_text("Log content 10")
            log99.write_text("Log content 99")

            # Run compression
            compressed_count = compress_rotated_logs(log_dir)

            # Verify all were compressed
            assert compressed_count == 3
            assert not log1.exists()
            assert not log10.exists()
            assert not log99.exists()
            assert (log_dir / "test.log.1.gz").exists()
            assert (log_dir / "test.log.10.gz").exists()
            assert (log_dir / "test.log.99.gz").exists()

    def test_skip_already_compressed_files(self):
        """Should skip files that are already compressed."""
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent))
        from lib.execution import compress_rotated_logs

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)

            # Create rotated log file and its gz version
            log1 = log_dir / "test.log.1"
            log1.write_text("Original content")
            gz1 = log_dir / "test.log.1.gz"
            with gzip.open(gz1, "wt") as f:
                f.write("Already compressed")

            # Run compression
            compressed_count = compress_rotated_logs(log_dir)

            # Should not compress (gz already exists)
            assert compressed_count == 0
            # Original should still exist (not deleted)
            assert log1.exists()

    def test_nonexistent_directory(self):
        """Should return 0 for non-existent directory."""
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent))
        from lib.execution import compress_rotated_logs

        result = compress_rotated_logs(Path("/nonexistent/directory"))
        assert result == 0

    def test_empty_directory(self):
        """Should return 0 for empty directory."""
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent))
        from lib.execution import compress_rotated_logs

        with tempfile.TemporaryDirectory() as tmpdir:
            result = compress_rotated_logs(Path(tmpdir))
            assert result == 0

    def test_only_current_log_no_rotation(self):
        """Should not compress current log file (no rotation suffix)."""
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent))
        from lib.execution import compress_rotated_logs

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)

            # Create current log (no rotation suffix)
            current_log = log_dir / "test.log"
            current_log.write_text("Current log content")

            # Run compression
            compressed_count = compress_rotated_logs(log_dir)

            # Should not compress current log
            assert compressed_count == 0
            assert current_log.exists()
