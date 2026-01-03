#!/usr/bin/env python3
"""Tests for update_secret.py script."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import update_secret


class TestRunCommand:
    """Tests for run_command function."""

    def test_successful_command(self):
        """Test running a successful command."""
        success, output = update_secret.run_command(["echo", "hello"])
        assert success is True
        assert output == "hello"

    def test_failed_command(self):
        """Test running a failed command."""
        success, output = update_secret.run_command(["false"])
        assert success is False

    def test_timeout(self):
        """Test command timeout."""
        success, output = update_secret.run_command(["sleep", "10"], timeout=1)
        assert success is False
        assert "timed out" in output.lower()


class TestUpdateSecret:
    """Tests for update_secret function."""

    @patch("subprocess.run")
    def test_update_secret_success(self, mock_run):
        """Test successful secret update."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = update_secret.update_secret("TEST_SECRET", "test_value")

        assert result is True
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == ["gh", "secret", "set", "TEST_SECRET"]
        assert call_args[1]["input"] == "test_value"

    @patch("subprocess.run")
    def test_update_secret_failure(self, mock_run):
        """Test failed secret update."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Permission denied")

        result = update_secret.update_secret("TEST_SECRET", "test_value")

        assert result is False


class TestTriggerDeploy:
    """Tests for trigger_deploy function."""

    @patch("update_secret.run_command")
    @patch("time.sleep")
    def test_trigger_deploy_success(self, mock_sleep, mock_run):
        """Test successful deploy trigger."""
        # First call: trigger workflow
        # Second call: get run ID
        mock_run.side_effect = [
            (True, ""),
            (True, "12345678"),
        ]

        result = update_secret.trigger_deploy()

        assert result == "12345678"
        assert mock_run.call_count == 2

    @patch("update_secret.run_command")
    def test_trigger_deploy_failure(self, mock_run):
        """Test failed deploy trigger."""
        mock_run.return_value = (False, "Error")

        result = update_secret.trigger_deploy()

        assert result is None


class TestWaitForDeploy:
    """Tests for wait_for_deploy function."""

    @patch("update_secret.run_command")
    @patch("time.sleep")
    def test_wait_for_deploy_success(self, mock_sleep, mock_run):
        """Test successful deploy wait."""
        mock_run.return_value = (True, "completed|success")

        result = update_secret.wait_for_deploy("12345678")

        assert result is True

    @patch("update_secret.run_command")
    @patch("time.sleep")
    def test_wait_for_deploy_failure(self, mock_sleep, mock_run):
        """Test failed deploy."""
        mock_run.return_value = (True, "completed|failure")

        result = update_secret.wait_for_deploy("12345678")

        assert result is False

    @patch("update_secret.run_command")
    @patch("time.sleep")
    @patch("time.time")
    def test_wait_for_deploy_timeout(self, mock_time, mock_sleep, mock_run):
        """Test deploy timeout."""
        # Simulate timeout
        mock_time.side_effect = [0, 0, update_secret.DEPLOY_TIMEOUT_MINUTES * 60 + 1]
        mock_run.return_value = (True, "in_progress|")

        result = update_secret.wait_for_deploy("12345678")

        assert result is False


class TestVerifyProduction:
    """Tests for verify_production function."""

    @patch("update_secret.run_command")
    def test_verify_admax_found(self, mock_run):
        """Test AdMax verification when script URL is found."""
        expected_id = "ba1f662d620fa8d93a043dcda91d2e9c"
        mock_run.return_value = (
            True,
            f'<script src="https://adm.shinobi.jp/o/{expected_id}"></script>',
        )

        result = update_secret.verify_production("VITE_ADMAX_ID", expected_id)

        assert result is True

    @patch("update_secret.run_command")
    def test_verify_admax_page_loads(self, mock_run):
        """Test AdMax verification when page loads but script not in HTML."""
        mock_run.return_value = (True, "<html><title>dekita!</title></html>")

        result = update_secret.verify_production(
            "VITE_ADMAX_ID", "ba1f662d620fa8d93a043dcda91d2e9c"
        )

        # Should return True because page loads (script loaded dynamically)
        assert result is True

    @patch("update_secret.run_command")
    def test_verify_other_secret(self, mock_run):
        """Test verification for non-AdMax secrets."""
        mock_run.return_value = (True, "200")

        result = update_secret.verify_production("OTHER_SECRET", "value")

        assert result is True


class TestUpdateResult:
    """Tests for UpdateResult dataclass."""

    def test_default_values(self):
        """Test default values."""
        result = update_secret.UpdateResult(success=False, message="test")

        assert result.success is False
        assert result.message == "test"
        assert result.secret_updated is False
        assert result.deploy_triggered is False
        assert result.deploy_completed is False
        assert result.verified is False
        assert result.run_id is None


class TestProductionUrl:
    """Tests for production URL configuration."""

    def test_production_url_is_dekita_app(self):
        """Ensure production URL is dekita.app, not dekita.pages.dev."""
        assert update_secret.PRODUCTION_URL == "https://dekita.app"
        assert "pages.dev" not in update_secret.PRODUCTION_URL
