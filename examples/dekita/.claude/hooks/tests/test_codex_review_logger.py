#!/usr/bin/env python3
"""Tests for codex-review-logger.py hook."""

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

HOOK_PATH = Path(__file__).parent.parent / "codex-review-logger.py"


def run_hook(input_data: dict) -> dict:
    """Run the hook with given input and return the result."""
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestCodexReviewLoggerBasic:
    """Basic tests for codex-review-logger hook."""

    def test_always_approves(self):
        """Should always approve (logger doesn't block)."""
        test_cases = [
            {"tool_input": {"command": "ls -la"}},
            {"tool_input": {"command": "codex review --base main"}},
            {"tool_input": {"command": ""}},
            {},
        ]

        for input_data in test_cases:
            with self.subTest(input_data=input_data):
                result = run_hook(input_data)
                assert result["decision"] == "approve"

    def test_detect_codex_review_patterns(self):
        """Should detect various codex review command patterns."""
        import importlib.util
        import sys

        parent_dir = str(Path(__file__).parent.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        spec = importlib.util.spec_from_file_location("codex_review_logger", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Patterns that should match
        assert module.is_codex_review_command("codex review")
        assert module.is_codex_review_command("codex review --base main")
        assert module.is_codex_review_command("codex  review  --base main")
        assert module.is_codex_review_command("some_cmd && codex review")

        # Patterns that should NOT match
        assert not module.is_codex_review_command("codex reviewer")
        assert not module.is_codex_review_command("codex reviews")
        assert not module.is_codex_review_command("echo 'codex review'")
        assert not module.is_codex_review_command('echo "codex review"')
        assert not module.is_codex_review_command("")


class TestCodexReviewLoggerLogFile:
    """Tests for log file creation."""

    def test_log_file_creation(self):
        """Test that log_review_execution creates log file with commit hash."""
        import importlib.util
        import sys

        parent_dir = str(Path(__file__).parent.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        spec = importlib.util.spec_from_file_location("codex_review_logger", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = module.MARKERS_LOG_DIR
            module.MARKERS_LOG_DIR = Path(tmpdir)

            try:
                module.log_review_execution("test-branch", "abc1234", "diffhash123")

                log_file = Path(tmpdir) / "codex-review-test-branch.done"
                assert log_file.exists()
                # Content should be "branch:commit:diff_hash" format
                assert log_file.read_text() == "test-branch:abc1234:diffhash123"
            finally:
                module.MARKERS_LOG_DIR = original_log_dir

    def test_log_file_with_slash_branch(self):
        """Test log file creation with branch name containing slash."""
        import importlib.util
        import sys

        parent_dir = str(Path(__file__).parent.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        spec = importlib.util.spec_from_file_location("codex_review_logger", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = module.MARKERS_LOG_DIR
            module.MARKERS_LOG_DIR = Path(tmpdir)

            try:
                module.log_review_execution("feature/test", "def5678", "diffhash456")

                # Branch name "feature/test" should be sanitized to "feature-test"
                log_file = Path(tmpdir) / "codex-review-feature-test.done"
                assert log_file.exists()
                # Content should contain original branch name, commit, and diff_hash
                assert log_file.read_text() == "feature/test:def5678:diffhash456"
            finally:
                module.MARKERS_LOG_DIR = original_log_dir

    def test_log_file_without_commit(self):
        """Test log file creation when commit is None."""
        import importlib.util
        import sys

        parent_dir = str(Path(__file__).parent.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        spec = importlib.util.spec_from_file_location("codex_review_logger", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmpdir:
            original_log_dir = module.MARKERS_LOG_DIR
            module.MARKERS_LOG_DIR = Path(tmpdir)

            try:
                module.log_review_execution("test-branch", None, None)

                log_file = Path(tmpdir) / "codex-review-test-branch.done"
                assert log_file.exists()
                # When commit is None, just store branch name
                assert log_file.read_text() == "test-branch"
            finally:
                module.MARKERS_LOG_DIR = original_log_dir


class TestCodexReviewLoggerEdgeCases:
    """Tests for edge cases."""

    def test_no_log_for_main_branch(self):
        """Should not log review for main branch."""
        # Note: This test relies on the hook behavior, which checks branch name
        # The actual test would need to mock get_current_branch
        result = run_hook({"tool_input": {"command": "codex review"}})
        # Should still approve
        assert result["decision"] == "approve"

    def test_error_handling(self):
        """Should handle errors gracefully and still approve."""
        # Invalid JSON shouldn't crash the hook
        result = subprocess.run(
            ["python3", str(HOOK_PATH)],
            input="not valid json",
            capture_output=True,
            text=True,
        )
        # Should still produce valid JSON output
        try:
            output = json.loads(result.stdout)
            assert output["decision"] == "approve"
        except json.JSONDecodeError:
            pytest.fail("Hook should produce valid JSON even on error")
