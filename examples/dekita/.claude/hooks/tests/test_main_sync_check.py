#!/usr/bin/env python3
"""Tests for main_sync_check.py hook."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestMainSyncCheck:
    """Tests for main_sync_check hook functions."""

    @patch("subprocess.run")
    def test_fetch_remote_success(self, mock_run):
        """Should return True when git fetch succeeds."""
        mock_run.return_value = MagicMock(returncode=0)

        import importlib

        import main_sync_check

        importlib.reload(main_sync_check)

        result = main_sync_check.fetch_remote()
        assert result is True

    @patch("subprocess.run")
    def test_fetch_remote_failure(self, mock_run):
        """Should return False when git fetch fails."""
        mock_run.return_value = MagicMock(returncode=1)

        import importlib

        import main_sync_check

        importlib.reload(main_sync_check)

        result = main_sync_check.fetch_remote()
        assert result is False

    @patch("subprocess.run")
    def test_get_main_divergence_no_local_main(self, mock_run):
        """Should return (0, 0) when local main doesn't exist."""
        mock_run.return_value = MagicMock(returncode=1)

        import importlib

        import main_sync_check

        importlib.reload(main_sync_check)

        behind, ahead = main_sync_check.get_main_divergence()
        assert behind == 0
        assert ahead == 0

    @patch("subprocess.run")
    def test_get_main_divergence_with_divergence(self, mock_run):
        """Should return correct counts when branches diverge."""

        def mock_subprocess(*args, **kwargs):
            cmd = args[0]
            result = MagicMock()

            if "rev-parse" in cmd:
                result.returncode = 0
                result.stdout = "abc123"
            elif "main..origin/main" in cmd:
                result.returncode = 0
                result.stdout = "5"
            elif "origin/main..main" in cmd:
                result.returncode = 0
                result.stdout = "2"
            else:
                result.returncode = 0
                result.stdout = ""

            return result

        mock_run.side_effect = mock_subprocess

        import importlib

        import main_sync_check

        importlib.reload(main_sync_check)

        behind, ahead = main_sync_check.get_main_divergence()
        assert behind == 5
        assert ahead == 2

    @patch("subprocess.run")
    def test_check_suspicious_commits_no_repeats(self, mock_run):
        """Should return False when no suspicious patterns."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="feat: add feature\nfix: bug fix\ndocs: update readme\n",
        )

        import importlib

        import main_sync_check

        importlib.reload(main_sync_check)

        has_suspicious, count, msg = main_sync_check.check_suspicious_commits()
        assert has_suspicious is False
        assert count == 0
        assert msg is None

    @patch("subprocess.run")
    def test_check_suspicious_commits_with_repeats(self, mock_run):
        """Should detect repeated commit messages."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="init\ninit\ninit\ninit\nfeat: something\n",
        )

        import importlib

        import main_sync_check

        importlib.reload(main_sync_check)

        has_suspicious, count, msg = main_sync_check.check_suspicious_commits()
        assert has_suspicious is True
        assert count == 4
        assert msg == "init"

    @patch("subprocess.run")
    def test_check_suspicious_commits_threshold(self, mock_run):
        """Should not flag 2 consecutive (only 3+ is suspicious)."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="init\ninit\nfeat: something\n",
        )

        import importlib

        import main_sync_check

        importlib.reload(main_sync_check)

        has_suspicious, count, msg = main_sync_check.check_suspicious_commits()
        assert has_suspicious is False


class TestMainSyncCheckIntegration:
    """Integration tests for main-sync-check hook."""

    def test_main_no_issues(self, capsys):
        """Should not output when no issues detected."""
        import main_sync_check

        with patch.object(main_sync_check, "check_and_update_session_marker", return_value=True):
            with patch.object(main_sync_check, "fetch_remote", return_value=True):
                with patch.object(main_sync_check, "get_main_divergence", return_value=(0, 0)):
                    with patch.object(
                        main_sync_check,
                        "check_suspicious_commits",
                        return_value=(False, 0, None),
                    ):
                        main_sync_check.main()

        captured = capsys.readouterr()
        assert captured.out == ""

    def test_main_with_behind(self, capsys):
        """Should warn when local main is behind."""
        import main_sync_check

        with patch.object(main_sync_check, "check_and_update_session_marker", return_value=True):
            with patch.object(main_sync_check, "fetch_remote", return_value=True):
                with patch.object(main_sync_check, "get_main_divergence", return_value=(10, 0)):
                    with patch.object(
                        main_sync_check,
                        "check_suspicious_commits",
                        return_value=(False, 0, None),
                    ):
                        main_sync_check.main()

        captured = capsys.readouterr()
        assert "10" in captured.out
        assert "git pull" in captured.out

    @patch("main_sync_check.check_and_update_session_marker")
    def test_main_skips_when_not_new_session(self, mock_session, capsys):
        """Should not run checks when not a new session."""
        mock_session.return_value = False

        import importlib

        import main_sync_check

        importlib.reload(main_sync_check)
        main_sync_check.main()

        captured = capsys.readouterr()
        assert captured.out == ""
