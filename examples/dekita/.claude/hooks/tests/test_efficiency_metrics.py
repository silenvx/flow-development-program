#!/usr/bin/env python3
"""Tests for efficiency metrics hooks."""

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch


# Load modules with hyphenated filenames
def load_module(name: str, filename: str):
    """Load a Python module from a hyphenated filename."""
    spec = importlib.util.spec_from_file_location(
        name,
        Path(__file__).parent.parent / filename,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rework_tracker = load_module("rework_tracker", "rework-tracker.py")
ci_recovery_tracker = load_module("ci_recovery_tracker", "ci-recovery-tracker.py")
tool_efficiency_tracker = load_module("tool_efficiency_tracker", "tool-efficiency-tracker.py")


class TestReworkTracker:
    """Tests for rework-tracker.py."""

    def test_get_session_id_from_project_dir(self):
        """Test session ID generation from project directory.

        get_session_id() returns a 16-char hex hash of CLAUDE_PROJECT_DIR.
        """
        import hashlib

        test_dir = "/test/project/dir"
        expected = hashlib.sha256(test_dir.encode("utf-8")).hexdigest()[:16]
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": test_dir}):
            assert rework_tracker.get_session_id() == expected

    def test_get_session_id_length(self):
        """Test session ID is always 16 characters."""
        result = rework_tracker.get_session_id()
        assert len(result) == 16


class TestCIRecoveryTracker:
    """Tests for ci-recovery-tracker.py."""

    def test_is_ci_check_command(self):
        """Test CI check command detection."""
        assert ci_recovery_tracker.is_ci_check_command("gh pr checks 123")
        assert ci_recovery_tracker.is_ci_check_command("gh run view 456")
        assert ci_recovery_tracker.is_ci_check_command("gh run watch 789")
        assert not ci_recovery_tracker.is_ci_check_command("gh pr create")
        assert not ci_recovery_tracker.is_ci_check_command("npm test")

    def test_extract_ci_target_number(self):
        """Test PR number/run ID extraction from CI commands."""
        assert ci_recovery_tracker.extract_ci_target_number("gh pr checks 123") == "123"
        assert ci_recovery_tracker.extract_ci_target_number("gh run view 456") == "456"
        assert ci_recovery_tracker.extract_ci_target_number("gh pr list") is None

    def test_detect_ci_status_failure(self):
        """Test CI failure detection from output."""
        assert ci_recovery_tracker.detect_ci_status("X CI / test") == "failure"
        assert ci_recovery_tracker.detect_ci_status("FAILURE: Build failed") == "failure"
        assert ci_recovery_tracker.detect_ci_status("❌ Tests failed") == "failure"

    def test_detect_ci_status_success(self):
        """Test CI success detection from output."""
        assert ci_recovery_tracker.detect_ci_status("✓ CI / test") == "success"
        assert ci_recovery_tracker.detect_ci_status("All checks have passed") == "success"
        assert ci_recovery_tracker.detect_ci_status("✅ Build succeeded") == "success"

    def test_detect_ci_status_unknown(self):
        """Test CI status unknown case."""
        assert ci_recovery_tracker.detect_ci_status("pending") is None
        assert ci_recovery_tracker.detect_ci_status("in_progress") is None


class TestToolEfficiencyTracker:
    """Tests for tool-efficiency-tracker.py."""

    def test_extract_target_read(self):
        """Test target extraction for Read tool."""
        tool_input = {"file_path": "/path/to/file.py"}
        assert tool_efficiency_tracker.extract_target("Read", tool_input) == "/path/to/file.py"

    def test_extract_target_edit(self):
        """Test target extraction for Edit tool."""
        tool_input = {"file_path": "/path/to/file.py", "old_string": "foo", "new_string": "bar"}
        assert tool_efficiency_tracker.extract_target("Edit", tool_input) == "/path/to/file.py"

    def test_extract_target_glob(self):
        """Test target extraction for Glob tool."""
        tool_input = {"pattern": "**/*.py"}
        assert tool_efficiency_tracker.extract_target("Glob", tool_input) == "**/*.py"

    def test_extract_target_grep(self):
        """Test target extraction for Grep tool."""
        tool_input = {"pattern": "TODO"}
        assert tool_efficiency_tracker.extract_target("Grep", tool_input) == "TODO"

    def test_extract_target_bash(self):
        """Test target extraction for Bash tool."""
        tool_input = {"command": "npm run test"}
        assert tool_efficiency_tracker.extract_target("Bash", tool_input) == "npm run test"

    def test_detect_read_edit_loop_no_pattern(self):
        """Test read-edit loop detection with insufficient data."""
        calls = [
            {"tool": "Read", "target": "/file.py", "timestamp": datetime.now(UTC).isoformat()},
            {"tool": "Edit", "target": "/file.py", "timestamp": datetime.now(UTC).isoformat()},
        ]
        assert tool_efficiency_tracker.detect_read_edit_loop(calls) is None

    def test_detect_read_edit_loop_with_pattern(self):
        """Test read-edit loop detection with pattern."""
        now = datetime.now(UTC)
        calls = [
            {"tool": "Read", "target": "/file.py", "timestamp": now.isoformat()},
            {"tool": "Edit", "target": "/file.py", "timestamp": now.isoformat()},
            {"tool": "Read", "target": "/file.py", "timestamp": now.isoformat()},
            {"tool": "Edit", "target": "/file.py", "timestamp": now.isoformat()},
            {"tool": "Read", "target": "/file.py", "timestamp": now.isoformat()},
            {"tool": "Edit", "target": "/file.py", "timestamp": now.isoformat()},
        ]
        result = tool_efficiency_tracker.detect_read_edit_loop(calls)
        assert result is not None
        assert result["pattern"] == "read_edit_loop"
        assert result["file"] == "/file.py"

    def test_detect_repeated_search_no_pattern(self):
        """Test repeated search detection with insufficient data."""
        calls = [
            {"tool": "Grep", "target": "TODO", "timestamp": datetime.now(UTC).isoformat()},
            {"tool": "Grep", "target": "FIXME", "timestamp": datetime.now(UTC).isoformat()},
        ]
        assert tool_efficiency_tracker.detect_repeated_search(calls) is None

    def test_detect_repeated_search_with_pattern(self):
        """Test repeated search detection with pattern."""
        now = datetime.now(UTC)
        calls = [
            {"tool": "Grep", "target": "TODO", "timestamp": now.isoformat()},
            {"tool": "Grep", "target": "todo", "timestamp": now.isoformat()},
            {"tool": "Grep", "target": "TODO", "timestamp": now.isoformat()},
        ]
        result = tool_efficiency_tracker.detect_repeated_search(calls)
        assert result is not None
        assert result["pattern"] == "repeated_search"
        assert result["count"] >= 3

    def test_detect_bash_retry_no_pattern(self):
        """Test Bash retry detection with insufficient failures."""
        calls = [
            {
                "tool": "Bash",
                "target": "npm test",
                "success": True,
                "timestamp": datetime.now(UTC).isoformat(),
            },
            {
                "tool": "Bash",
                "target": "npm test",
                "success": False,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        ]
        assert tool_efficiency_tracker.detect_bash_retry(calls) is None

    def test_detect_bash_retry_with_pattern(self):
        """Test Bash retry detection with pattern."""
        now = datetime.now(UTC)
        calls = [
            {"tool": "Bash", "target": "npm test", "success": False, "timestamp": now.isoformat()},
            {"tool": "Bash", "target": "npm test", "success": False, "timestamp": now.isoformat()},
            {"tool": "Bash", "target": "npm test", "success": False, "timestamp": now.isoformat()},
        ]
        result = tool_efficiency_tracker.detect_bash_retry(calls)
        assert result is not None
        assert result["pattern"] == "bash_retry"
        assert result["failure_count"] >= 3


def run_tests():
    """Run all tests."""
    import traceback

    test_classes = [
        TestReworkTracker,
        TestCIRecoveryTracker,
        TestToolEfficiencyTracker,
    ]

    passed = 0
    failed = 0

    for test_class in test_classes:
        instance = test_class()
        for method_name in dir(instance):
            if method_name.startswith("test_"):
                try:
                    getattr(instance, method_name)()
                    print(f"  ✓ {test_class.__name__}.{method_name}")
                    passed += 1
                except Exception as e:
                    print(f"  ✗ {test_class.__name__}.{method_name}: {e}")
                    traceback.print_exc()
                    failed += 1

    print(f"\n{passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
