#!/usr/bin/env python3
"""Tests for api-operation-timer.py hook."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "api-operation-timer.py"


def load_module():
    """Load the hook module for testing."""
    parent_dir = str(Path(__file__).parent.parent)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    spec = importlib.util.spec_from_file_location("api_operation_timer", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_hook(input_data: dict) -> dict:
    """Run the hook with given input and return the result."""
    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestApiOperationTimer:
    """Tests for API operation timer hook."""

    def test_approve_gh_pr_command(self):
        """Should approve gh pr commands and record timing."""
        input_data = {
            "session_id": "test-session-123",
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr create --title 'test'"},
        }
        result = run_hook(input_data)
        assert result.get("decision") == "approve"

    def test_approve_git_push_command(self):
        """Should approve git push commands."""
        input_data = {
            "session_id": "test-session-123",
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
        }
        result = run_hook(input_data)
        assert result.get("decision") == "approve"

    def test_approve_npm_run_command(self):
        """Should approve npm run commands."""
        input_data = {
            "session_id": "test-session-123",
            "tool_name": "Bash",
            "tool_input": {"command": "npm run build"},
        }
        result = run_hook(input_data)
        assert result.get("decision") == "approve"

    def test_approve_non_target_command(self):
        """Should approve non-target commands without recording."""
        input_data = {
            "session_id": "test-session-123",
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
        }
        result = run_hook(input_data)
        assert result.get("decision") == "approve"

    def test_approve_non_bash_tool(self):
        """Should approve non-Bash tools."""
        input_data = {
            "session_id": "test-session-123",
            "tool_name": "Read",
            "tool_input": {"file_path": "/some/path"},
        }
        result = run_hook(input_data)
        assert result.get("decision") == "approve"

    def test_approve_empty_input(self):
        """Should approve when input is empty."""
        result = run_hook({})
        assert result.get("decision") == "approve"


class TestTimingFileCreation:
    """Tests for timing file creation."""

    def setup_method(self):
        self.module = load_module()
        # Use a temporary directory for timing files
        self.temp_dir = tempfile.mkdtemp()
        self.module.TIMING_DIR = Path(self.temp_dir) / "api-timing"

    def teardown_method(self):
        # Cleanup
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_save_start_time_creates_file(self):
        """Should create timing file when saving start time."""
        self.module.save_start_time("test-session", "tool-123", "gh pr create")

        timing_files = list(self.module.TIMING_DIR.glob("*.json"))
        assert len(timing_files) == 1

        with open(timing_files[0]) as f:
            data = json.load(f)
        assert "start_time" in data
        assert data["session_id"] == "test-session"

    def test_save_start_time_with_no_tool_id(self):
        """Should create timing file with command hash when no tool_id."""
        self.module.save_start_time("test-session", None, "gh pr create")

        timing_files = list(self.module.TIMING_DIR.glob("*.json"))
        assert len(timing_files) == 1

        filename = timing_files[0].name
        assert "cmd-" in filename

    def test_cleanup_old_timing_files(self):
        """Should cleanup old timing files."""
        self.module.TIMING_DIR.mkdir(parents=True, exist_ok=True)

        # Create an old file
        old_file = self.module.TIMING_DIR / "old-file.json"
        old_file.write_text("{}")
        # Set modification time to 2 hours ago
        old_mtime = os.path.getmtime(old_file) - 7200
        os.utime(old_file, (old_mtime, old_mtime))

        # Create a new file
        new_file = self.module.TIMING_DIR / "new-file.json"
        new_file.write_text("{}")

        self.module.cleanup_old_timing_files()

        # Old file should be deleted
        assert not old_file.exists()
        # New file should remain
        assert new_file.exists()
