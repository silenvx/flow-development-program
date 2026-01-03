#!/usr/bin/env python3
"""Tests for check-sentry-usage.py."""

import sys
import tempfile
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import after path modification
import importlib.util

spec = importlib.util.spec_from_file_location(
    "check_sentry_usage",
    Path(__file__).parent.parent / "check-sentry-usage.py",
)
check_sentry_usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_sentry_usage)


class TestIsInComment:
    """Tests for is_in_comment function."""

    def test_not_in_comment(self):
        """Pattern before any comment should not be detected as in comment."""
        line = 'Sentry.setTag("key", "value");'
        assert not check_sentry_usage.is_in_comment(line, 0)

    def test_in_comment(self):
        """Pattern after // should be detected as in comment."""
        line = '// Sentry.setTag("key", "value");'
        assert not check_sentry_usage.is_in_comment(line, 0)  # Before //
        assert check_sentry_usage.is_in_comment(line, 10)  # After //

    def test_comment_after_code(self):
        """Code before comment, comment after."""
        line = "const x = 1; // Sentry.setTag()"
        assert not check_sentry_usage.is_in_comment(line, 0)  # Code part
        assert check_sentry_usage.is_in_comment(line, 20)  # Comment part

    def test_no_comment(self):
        """Line without comment."""
        line = 'const x = Sentry.setTag("key", "value");'
        assert not check_sentry_usage.is_in_comment(line, 15)


class TestCheckFile:
    """Tests for check_file function."""

    def test_no_violations(self):
        """File with correct Sentry usage should have no violations."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ts", delete=False) as f:
            f.write("""
import * as Sentry from "@sentry/cloudflare";

Sentry.withScope((scope) => {
  scope.setTag("key", "value");
  Sentry.captureException(err);
});
""")
            f.flush()
            violations = check_sentry_usage.check_file(Path(f.name))
            assert violations == []

    def test_direct_setTag_violation(self):
        """Direct Sentry.setTag() should be detected."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ts", delete=False) as f:
            f.write('Sentry.setTag("key", "value");\n')
            f.flush()
            violations = check_sentry_usage.check_file(Path(f.name))
            assert len(violations) == 1
            assert violations[0][2] == "Sentry.setTag()"

    def test_direct_setContext_violation(self):
        """Direct Sentry.setContext() should be detected."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ts", delete=False) as f:
            f.write('Sentry.setContext("request", { path: "/" });\n')
            f.flush()
            violations = check_sentry_usage.check_file(Path(f.name))
            assert len(violations) == 1
            assert violations[0][2] == "Sentry.setContext()"

    def test_commented_out_pattern_ignored(self):
        """Patterns in // comments should be ignored."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ts", delete=False) as f:
            f.write("// Don't use Sentry.setTag() directly\n")
            f.flush()
            violations = check_sentry_usage.check_file(Path(f.name))
            assert violations == []

    def test_nonexistent_file(self):
        """Non-existent file should return empty list."""
        violations = check_sentry_usage.check_file(Path("/nonexistent/file.ts"))
        assert violations == []
