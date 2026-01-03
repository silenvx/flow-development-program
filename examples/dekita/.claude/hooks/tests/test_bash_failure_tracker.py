#!/usr/bin/env python3
"""Unit tests for bash-failure-tracker.py"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# bash-failure-tracker.py has hyphens, so we need dynamic import
HOOK_PATH = Path(__file__).parent.parent / "bash-failure-tracker.py"
_spec = importlib.util.spec_from_file_location("bash_failure_tracker", HOOK_PATH)
bash_failure_tracker = importlib.util.module_from_spec(_spec)
sys.modules["bash_failure_tracker"] = bash_failure_tracker
_spec.loader.exec_module(bash_failure_tracker)

is_shell_corruption_error = bash_failure_tracker.is_shell_corruption_error
load_tracking_data = bash_failure_tracker.load_tracking_data
save_tracking_data = bash_failure_tracker.save_tracking_data
FAILURE_THRESHOLD = bash_failure_tracker.FAILURE_THRESHOLD


class TestIsShellCorruptionError:
    """Tests for is_shell_corruption_error function."""

    def test_detects_no_such_file(self):
        """Should detect 'No such file or directory' error."""
        assert is_shell_corruption_error("bash: cd: /foo: No such file or directory")

    def test_detects_unable_to_read_cwd(self):
        """Should detect 'Unable to read current working directory' error."""
        assert is_shell_corruption_error("fatal: Unable to read current working directory")

    def test_detects_cannot_access(self):
        """Should detect 'cannot access' error."""
        assert is_shell_corruption_error("ls: cannot access '/foo': No such file")

    def test_detects_fatal_unable_to_read(self):
        """Should detect 'fatal: Unable to read' error."""
        assert is_shell_corruption_error("fatal: Unable to read")

    def test_case_insensitive(self):
        """Should match case-insensitively."""
        assert is_shell_corruption_error("NO SUCH FILE OR DIRECTORY")

    def test_ignores_normal_output(self):
        """Should not flag normal output."""
        assert not is_shell_corruption_error("Build succeeded")
        assert not is_shell_corruption_error("Test passed")

    def test_ignores_empty_output(self):
        """Should not flag empty output."""
        assert not is_shell_corruption_error("")


class TestTrackingDataPersistence:
    """Tests for tracking data load/save functions."""

    def test_load_returns_default_for_missing_file(self):
        """Should return default data when file doesn't exist."""
        with patch.object(bash_failure_tracker, "TRACKING_FILE", Path("/nonexistent/file.json")):
            data = load_tracking_data()
            assert data["consecutive_failures"] == 0
            assert data["last_errors"] == []
            assert data["updated_at"] is None

    def test_save_and_load_roundtrip(self):
        """Should save and load data correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = Path(tmpdir) / "claude-hooks"
            test_file = test_dir / "bash-failures.json"

            with patch.object(bash_failure_tracker, "TRACKING_DIR", test_dir):
                with patch.object(bash_failure_tracker, "TRACKING_FILE", test_file):
                    test_data = {
                        "consecutive_failures": 3,
                        "last_errors": ["error1", "error2"],
                        "updated_at": "2025-12-15T00:00:00Z",
                    }
                    save_tracking_data(test_data)
                    loaded_data = load_tracking_data()

                    assert loaded_data["consecutive_failures"] == 3
                    assert loaded_data["last_errors"] == ["error1", "error2"]


class TestFailureThreshold:
    """Tests for failure threshold constant."""

    def test_threshold_is_reasonable(self):
        """Threshold should be reasonable (not too low, not too high)."""
        assert FAILURE_THRESHOLD >= 2
        assert FAILURE_THRESHOLD <= 10


class TestConsecutiveFailureTracking:
    """Tests for consecutive failure tracking behavior."""

    def setup_method(self):
        """Set up temporary tracking directory for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name) / "claude-hooks"
        self.test_file = self.test_dir / "bash-failures.json"
        # Patch the tracking file location
        self.dir_patch = patch.object(bash_failure_tracker, "TRACKING_DIR", self.test_dir)
        self.file_patch = patch.object(bash_failure_tracker, "TRACKING_FILE", self.test_file)
        self.dir_patch.start()
        self.file_patch.start()

    def teardown_method(self):
        """Clean up patches and temp directory."""
        self.dir_patch.stop()
        self.file_patch.stop()
        self.temp_dir.cleanup()

    def test_failure_increments_counter(self):
        """Consecutive failures should increment the counter."""
        # Start fresh
        data = load_tracking_data()
        assert data["consecutive_failures"] == 0

        # Simulate first failure
        data["consecutive_failures"] += 1
        data["last_errors"].append("error1")
        save_tracking_data(data)

        # Verify
        loaded = load_tracking_data()
        assert loaded["consecutive_failures"] == 1

        # Simulate second failure
        loaded["consecutive_failures"] += 1
        loaded["last_errors"].append("error2")
        save_tracking_data(loaded)

        # Verify
        loaded = load_tracking_data()
        assert loaded["consecutive_failures"] == 2

    def test_success_resets_counter(self):
        """Successful command should reset the failure counter."""
        # Set up some failures
        data = {
            "consecutive_failures": 3,
            "last_errors": ["err1", "err2", "err3"],
            "updated_at": "2025-12-15T00:00:00Z",
        }
        save_tracking_data(data)

        # Simulate success (reset)
        data["consecutive_failures"] = 0
        data["last_errors"] = []
        save_tracking_data(data)

        loaded = load_tracking_data()
        assert loaded["consecutive_failures"] == 0
        assert loaded["last_errors"] == []

    def test_last_errors_limited_to_five(self):
        """Should only keep the last 5 errors."""
        data = {
            "consecutive_failures": 6,
            "last_errors": ["e1", "e2", "e3", "e4", "e5", "e6", "e7"],
            "updated_at": None,
        }
        # Apply the limit as the hook does
        data["last_errors"] = data["last_errors"][-5:]
        save_tracking_data(data)

        loaded = load_tracking_data()
        assert len(loaded["last_errors"]) == 5
        assert loaded["last_errors"] == ["e3", "e4", "e5", "e6", "e7"]


class TestHookIntegration:
    """Integration tests for the full hook behavior."""

    def setup_method(self):
        """Set up temporary tracking directory for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name) / "claude-hooks"
        self.test_file = self.test_dir / "bash-failures.json"

    def teardown_method(self):
        """Clean up temp directory."""
        self.temp_dir.cleanup()

    def run_hook(self, input_data: dict) -> dict:
        """Run the hook with given input and return parsed output."""
        env = os.environ.copy()
        env["TMPDIR"] = self.temp_dir.name
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
            env=env,
        )
        if result.stdout:
            return json.loads(result.stdout)
        return {}

    def test_successful_command_returns_continue(self):
        """Successful Bash command should return continue: true."""
        input_data = {"tool_result": {"exit_code": 0, "stdout": "success", "stderr": ""}}
        output = self.run_hook(input_data)
        assert output.get("continue", False)

    def test_failed_command_returns_continue(self):
        """Failed Bash command should still return continue: true."""
        input_data = {"tool_result": {"exit_code": 1, "stdout": "", "stderr": "command failed"}}
        output = self.run_hook(input_data)
        assert output.get("continue", False)

    def test_threshold_exceeded_with_corruption_shows_warning(self):
        """Should show warning when threshold exceeded with shell corruption."""
        # Pre-populate with failures at threshold - 1
        self.test_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "consecutive_failures": FAILURE_THRESHOLD - 1,
            "last_errors": ["err"] * (FAILURE_THRESHOLD - 1),
            "updated_at": "2025-12-15T00:00:00Z",
        }
        self.test_file.write_text(json.dumps(data))

        # Trigger one more failure with shell corruption pattern
        input_data = {
            "tool_result": {
                "exit_code": 1,
                "stdout": "",
                "stderr": "bash: cd: /deleted: No such file or directory",
            }
        }
        output = self.run_hook(input_data)

        assert output.get("continue", False)
        assert "systemMessage" in output
        assert "シェル破損" in output["systemMessage"]

    def test_threshold_exceeded_without_corruption_shows_generic_warning(self):
        """Should show generic warning when threshold exceeded without corruption pattern."""
        # Pre-populate with failures at threshold - 1
        self.test_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "consecutive_failures": FAILURE_THRESHOLD - 1,
            "last_errors": ["err"] * (FAILURE_THRESHOLD - 1),
            "updated_at": "2025-12-15T00:00:00Z",
        }
        self.test_file.write_text(json.dumps(data))

        # Trigger one more failure without shell corruption pattern
        input_data = {"tool_result": {"exit_code": 1, "stdout": "", "stderr": "some other error"}}
        output = self.run_hook(input_data)

        assert output.get("continue", False)
        assert "systemMessage" in output
        assert "Bash失敗" in output["systemMessage"]

    def test_below_threshold_no_warning(self):
        """Should not show warning when below threshold."""
        input_data = {
            "tool_result": {"exit_code": 1, "stdout": "", "stderr": "No such file or directory"}
        }
        output = self.run_hook(input_data)

        assert output.get("continue", False)
        # First failure - no warning yet
        assert "systemMessage" not in output
