"""Tests for secret-deploy-check.py hook."""

import importlib.util
import json
import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add hooks directory to path for common module
hooks_dir = Path(__file__).parent.parent
sys.path.insert(0, str(hooks_dir))

# Load module from file path (handles hyphenated filenames)
script_path = hooks_dir / "secret-deploy-check.py"
spec = importlib.util.spec_from_file_location("secret_deploy_check", script_path)
secret_deploy_check = importlib.util.module_from_spec(spec)
sys.modules["secret_deploy_check"] = secret_deploy_check
spec.loader.exec_module(secret_deploy_check)


class TestLoadTrackingData:
    """Tests for load_tracking_data function."""

    @patch.object(secret_deploy_check, "TRACKING_FILE")
    def test_returns_empty_when_file_not_exists(self, mock_tracking_file):
        """Returns empty data when tracking file doesn't exist."""
        mock_tracking_file.exists.return_value = False
        result = secret_deploy_check.load_tracking_data()
        assert result == {"secrets": [], "updated_at": None}

    @patch.object(secret_deploy_check, "TRACKING_FILE")
    def test_loads_existing_data(self, mock_tracking_file):
        """Loads data from existing tracking file."""
        mock_tracking_file.exists.return_value = True
        mock_tracking_file.read_text.return_value = json.dumps(
            {"secrets": ["VITE_TEST"], "updated_at": "2025-12-17T10:00:00Z"}
        )
        result = secret_deploy_check.load_tracking_data()
        assert result["secrets"] == ["VITE_TEST"]
        assert result["updated_at"] == "2025-12-17T10:00:00Z"

    @patch.object(secret_deploy_check, "TRACKING_FILE")
    def test_returns_empty_on_invalid_json(self, mock_tracking_file):
        """Returns empty data when JSON is invalid."""
        mock_tracking_file.exists.return_value = True
        mock_tracking_file.read_text.return_value = "invalid json"
        result = secret_deploy_check.load_tracking_data()
        assert result == {"secrets": [], "updated_at": None}


class TestClearTrackingData:
    """Tests for clear_tracking_data function."""

    @patch.object(secret_deploy_check, "TRACKING_FILE")
    def test_clears_existing_file(self, mock_tracking_file):
        """Removes tracking file when it exists."""
        mock_tracking_file.exists.return_value = True
        secret_deploy_check.clear_tracking_data()
        mock_tracking_file.unlink.assert_called_once()

    @patch.object(secret_deploy_check, "TRACKING_FILE")
    def test_no_error_when_file_not_exists(self, mock_tracking_file):
        """No error when tracking file doesn't exist."""
        mock_tracking_file.exists.return_value = False
        # Should not raise
        secret_deploy_check.clear_tracking_data()
        mock_tracking_file.unlink.assert_not_called()


class TestCheckDeployAfterUpdate:
    """Tests for check_deploy_after_update function."""

    def test_returns_false_when_no_updated_at(self):
        """Returns False when updated_at is None."""
        result = secret_deploy_check.check_deploy_after_update(None)
        assert not result

    @patch("subprocess.run")
    def test_returns_true_when_successful_run_after_update(self, mock_run):
        """Returns True when a successful CI run occurred after update."""
        # Update time: 10:00
        update_time = "2025-12-17T10:00:00Z"
        # CI run time: 11:00 (after update)
        run_time = "2025-12-17T11:00:00Z"

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([{"createdAt": run_time, "conclusion": "success"}]),
        )

        result = secret_deploy_check.check_deploy_after_update(update_time)
        assert result

    @patch("subprocess.run")
    def test_returns_false_when_run_before_update(self, mock_run):
        """Returns False when CI run occurred before update."""
        # Update time: 11:00
        update_time = "2025-12-17T11:00:00Z"
        # CI run time: 10:00 (before update)
        run_time = "2025-12-17T10:00:00Z"

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([{"createdAt": run_time, "conclusion": "success"}]),
        )

        result = secret_deploy_check.check_deploy_after_update(update_time)
        assert not result

    @patch("subprocess.run")
    def test_returns_false_when_run_failed(self, mock_run):
        """Returns False when CI run failed."""
        update_time = "2025-12-17T10:00:00Z"
        run_time = "2025-12-17T11:00:00Z"

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([{"createdAt": run_time, "conclusion": "failure"}]),
        )

        result = secret_deploy_check.check_deploy_after_update(update_time)
        assert not result

    @patch("subprocess.run")
    def test_handles_gh_command_failure(self, mock_run):
        """Returns False when gh command fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = secret_deploy_check.check_deploy_after_update("2025-12-17T10:00:00Z")
        assert not result

    @patch("subprocess.run")
    def test_handles_timeout(self, mock_run):
        """Returns False when gh command times out."""
        mock_run.side_effect = subprocess.TimeoutExpired("gh", 30)
        result = secret_deploy_check.check_deploy_after_update("2025-12-17T10:00:00Z")
        assert not result

    def test_returns_false_for_invalid_timestamp(self):
        """Returns False when given invalid timestamp format."""
        result = secret_deploy_check.check_deploy_after_update("invalid-timestamp")
        assert not result

    def test_returns_false_for_malformed_timestamp(self):
        """Returns False when given malformed timestamp like 2025-13-45T99:99:99Z."""
        result = secret_deploy_check.check_deploy_after_update("2025-13-45T99:99:99Z")
        assert not result


class TestMain:
    """Tests for main function."""

    @patch.object(secret_deploy_check, "load_tracking_data")
    @patch.object(secret_deploy_check, "log_hook_execution")
    def test_approves_when_no_secrets(self, mock_log, mock_load):
        """Approves when no secrets were updated."""
        mock_load.return_value = {"secrets": [], "updated_at": None}

        captured_output = StringIO()
        captured_input = StringIO(json.dumps({}))
        sys.stdout = captured_output
        sys.stdin = captured_input
        try:
            secret_deploy_check.main()
        finally:
            sys.stdout = sys.__stdout__
            sys.stdin = sys.__stdin__

        result = json.loads(captured_output.getvalue())
        assert result["decision"] == "approve"

    @patch.object(secret_deploy_check, "load_tracking_data")
    @patch.object(secret_deploy_check, "check_deploy_after_update")
    @patch.object(secret_deploy_check, "clear_tracking_data")
    @patch.object(secret_deploy_check, "log_hook_execution")
    def test_approves_and_clears_when_deployed(self, mock_log, mock_clear, mock_check, mock_load):
        """Approves and clears tracking when secrets were deployed."""
        mock_load.return_value = {
            "secrets": ["VITE_TEST"],
            "updated_at": "2025-12-17T10:00:00Z",
        }
        mock_check.return_value = True

        captured_output = StringIO()
        captured_input = StringIO(json.dumps({}))
        sys.stdout = captured_output
        sys.stdin = captured_input
        try:
            secret_deploy_check.main()
        finally:
            sys.stdout = sys.__stdout__
            sys.stdin = sys.__stdin__

        result = json.loads(captured_output.getvalue())
        assert result["decision"] == "approve"
        assert "デプロイされました" in result.get("systemMessage", "")
        mock_clear.assert_called_once()

    @patch.object(secret_deploy_check, "load_tracking_data")
    @patch.object(secret_deploy_check, "check_deploy_after_update")
    @patch.object(secret_deploy_check, "log_hook_execution")
    def test_blocks_when_not_deployed(self, mock_log, mock_check, mock_load):
        """Blocks when secrets were updated but not deployed."""
        mock_load.return_value = {
            "secrets": ["VITE_AD_ID"],
            "updated_at": "2025-12-17T10:00:00Z",
        }
        mock_check.return_value = False

        captured_output = StringIO()
        captured_input = StringIO(json.dumps({}))
        sys.stdout = captured_output
        sys.stdin = captured_input
        try:
            secret_deploy_check.main()
        finally:
            sys.stdout = sys.__stdout__
            sys.stdin = sys.__stdin__

        result = json.loads(captured_output.getvalue())
        assert result["decision"] == "block"
        assert "デプロイされていません" in result["reason"]
        assert "VITE_AD_ID" in result["reason"]

    @patch.object(secret_deploy_check, "load_tracking_data")
    @patch.object(secret_deploy_check, "log_hook_execution")
    def test_approves_when_stop_hook_active(self, mock_log, mock_load):
        """Approves when stop_hook_active to prevent infinite loops."""
        mock_load.return_value = {
            "secrets": ["VITE_TEST"],
            "updated_at": "2025-12-17T10:00:00Z",
        }

        captured_output = StringIO()
        captured_input = StringIO(json.dumps({"stop_hook_active": True}))
        sys.stdout = captured_output
        sys.stdin = captured_input
        try:
            secret_deploy_check.main()
        finally:
            sys.stdout = sys.__stdout__
            sys.stdin = sys.__stdin__

        result = json.loads(captured_output.getvalue())
        assert result["decision"] == "approve"
