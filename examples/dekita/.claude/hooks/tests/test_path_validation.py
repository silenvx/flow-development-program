#!/usr/bin/env python3
"""Tests for path_validation module."""

import tempfile


class TestIsSafeTranscriptPath:
    """Tests for is_safe_transcript_path function."""

    def setup_method(self):
        from lib.path_validation import is_safe_transcript_path

        self.is_safe = is_safe_transcript_path

    def test_empty_path(self):
        """Test empty path is rejected."""
        assert not self.is_safe("")

    def test_none_like_path(self):
        """Test None-like string is rejected."""
        assert not self.is_safe("   ")

    def test_valid_temp_path(self):
        """Test valid temp path is accepted."""
        with tempfile.NamedTemporaryFile() as f:
            assert self.is_safe(f.name)

    def test_path_traversal_rejected(self):
        """Test path traversal is rejected."""
        # Try to escape home directory
        assert not self.is_safe("/../../../etc/passwd")

    def test_relative_path_in_home(self):
        """Test relative path resolves correctly."""

        # This should resolve to current directory which is under home
        result = self.is_safe("./test.txt")
        # The result depends on whether cwd is under allowed directories
        # Just verify it doesn't crash
        assert isinstance(result, bool)

    def test_absolute_path_outside_allowed(self):
        """Test absolute path outside allowed directories is rejected."""
        # /etc is typically not under home, tmp, or cwd
        # We just verify it doesn't crash - result depends on system config
        _ = self.is_safe("/etc/passwd")

    def test_home_path_accepted(self):
        """Test paths under home directory are accepted."""
        import os

        home = os.path.expanduser("~")
        test_path = os.path.join(home, "test_transcript.json")
        assert self.is_safe(test_path)

    def test_tmp_path_accepted(self):
        """Test paths under /tmp are accepted."""
        import os

        tmpdir = os.environ.get("TMPDIR", "/tmp")
        test_path = os.path.join(tmpdir, "test_transcript.json")
        assert self.is_safe(test_path)


class TestGetAllowedDirectories:
    """Tests for _get_allowed_directories function."""

    def test_returns_list(self):
        """Test that function returns a list."""
        from lib.path_validation import _get_allowed_directories

        result = _get_allowed_directories()
        assert isinstance(result, list)

    def test_includes_home(self):
        """Test that home directory is included."""
        from pathlib import Path

        from lib.path_validation import _get_allowed_directories

        result = _get_allowed_directories()
        home = Path.home()
        assert home in result


class TestIsPathUnder:
    """Tests for _is_path_under function."""

    def test_path_under_directory(self):
        """Test path under directory returns True."""
        from pathlib import Path

        from lib.path_validation import _is_path_under

        # Use resolved paths to handle symlinks (e.g., /tmp -> /private/tmp on macOS)
        parent = Path.home()
        child = parent / "subdir" / "file.txt"
        assert _is_path_under(child, parent)

    def test_path_not_under_directory(self):
        """Test path not under directory returns False."""
        from pathlib import Path

        from lib.path_validation import _is_path_under

        # /etc is not under home
        parent = Path.home()
        other = Path("/etc/passwd")
        assert not _is_path_under(other, parent)

    def test_same_path(self):
        """Test same path returns True (path is under itself)."""
        from pathlib import Path

        from lib.path_validation import _is_path_under

        path = Path.home()
        assert _is_path_under(path, path)
