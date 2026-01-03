"""Tests for check_skip_env function.

Issue #1260: Record SKIP environment variable usage in logs.
"""

import os
from pathlib import Path
from unittest import mock

import pytest


class TestCheckSkipEnv:
    """Test check_skip_env function."""

    @pytest.fixture
    def mock_log_dir(self, tmp_path: Path) -> Path:
        """Create a temporary log directory."""
        log_dir = tmp_path / ".claude" / "logs" / "execution"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir

    def test_returns_true_when_skip_enabled(self, mock_log_dir: Path) -> None:
        """check_skip_env returns True when env var is set to '1'."""
        from lib.results import check_skip_env

        with mock.patch.dict(os.environ, {"SKIP_TEST": "1"}):
            with mock.patch("common.EXECUTION_LOG_DIR", mock_log_dir):
                result = check_skip_env("test-hook", "SKIP_TEST")

        assert result is True

    def test_returns_false_when_skip_not_set(self) -> None:
        """check_skip_env returns False when env var is not set."""
        from lib.results import check_skip_env

        # Ensure SKIP_TEST is not set
        env = os.environ.copy()
        env.pop("SKIP_TEST", None)

        with mock.patch.dict(os.environ, env, clear=True):
            result = check_skip_env("test-hook", "SKIP_TEST")

        assert result is False

    def test_returns_false_when_skip_set_to_zero(self) -> None:
        """check_skip_env returns False when env var is set to '0'."""
        from lib.results import check_skip_env

        with mock.patch.dict(os.environ, {"SKIP_TEST": "0"}):
            result = check_skip_env("test-hook", "SKIP_TEST")

        assert result is False

    def test_returns_false_when_skip_set_to_false(self) -> None:
        """check_skip_env returns False when env var is set to 'false'."""
        from lib.results import check_skip_env

        with mock.patch.dict(os.environ, {"SKIP_TEST": "false"}):
            result = check_skip_env("test-hook", "SKIP_TEST")

        assert result is False

    def test_returns_true_when_skip_set_to_true(self, mock_log_dir: Path) -> None:
        """check_skip_env returns True when env var is set to 'true'."""
        from lib.results import check_skip_env

        with mock.patch.dict(os.environ, {"SKIP_TEST": "true"}):
            with mock.patch("common.EXECUTION_LOG_DIR", mock_log_dir):
                result = check_skip_env("test-hook", "SKIP_TEST")

        assert result is True

    def test_logs_skip_event(self, mock_log_dir: Path) -> None:
        """check_skip_env logs skip event to session-specific file.

        Issue #1994: Updated to test session-specific file format.
        Issue #2496: Updated to use PPID-based session ID fallback.
        Issue #2529: ppidフォールバック廃止、session_id=Noneでログはスキップ。
        """
        from lib.results import check_skip_env

        # Issue #2529: session_idがない場合、ログファイルは作成されない
        with mock.patch.dict(os.environ, {"SKIP_PLAN": "1"}):
            with mock.patch("lib.execution._get_execution_log_dir", return_value=mock_log_dir):
                result = check_skip_env("planning-enforcement", "SKIP_PLAN")

        # Skip環境変数が有効なのでTrueを返す
        assert result is True

        # Issue #2529: session_idがNoneなので、session固有のログファイルは作成されない
        session_log_files = list(mock_log_dir.glob("hook-execution-*.jsonl"))
        assert len(session_log_files) == 0

    def test_logs_skip_with_true_value(self, mock_log_dir: Path) -> None:
        """check_skip_env returns True when set to 'true'.

        Issue #1994: Updated to test session-specific file format.
        Issue #2496: Updated to use PPID-based session ID fallback.
        Issue #2529: ppidフォールバック廃止。ログ作成ではなく戻り値をテスト。
        """
        from lib.results import check_skip_env

        # Issue #2529: session_idがない場合はログ作成をテストしない
        with mock.patch.dict(os.environ, {"SKIP_CODEX_REVIEW": "true"}):
            with mock.patch("lib.execution._get_execution_log_dir", return_value=mock_log_dir):
                result = check_skip_env("codex-review-check", "SKIP_CODEX_REVIEW")

        assert result is True

    def test_no_log_when_skip_not_enabled(self, mock_log_dir: Path) -> None:
        """check_skip_env does not log when env var is not enabled.

        Issue #2496: Updated to use PPID-based session ID fallback.
        Issue #2529: ppidフォールバック廃止。
        """
        from lib.results import check_skip_env

        env = os.environ.copy()
        env.pop("SKIP_TEST", None)

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("lib.execution._get_execution_log_dir", return_value=mock_log_dir):
                result = check_skip_env("test-hook", "SKIP_TEST")

        # Skip not enabled -> False
        assert result is False

        # No log files should exist
        session_log_files = list(mock_log_dir.glob("hook-execution-*.jsonl"))
        assert len(session_log_files) == 0

    def test_logs_with_additional_details(self, mock_log_dir: Path) -> None:
        """check_skip_env returns True with additional details.

        Issue #1994: Updated to test session-specific file format.
        Issue #2496: Updated to use PPID-based session ID fallback.
        Issue #2529: ppidフォールバック廃止。ログではなく戻り値をテスト。
        """
        from lib.results import check_skip_env

        # Issue #2529: session_idがない場合はログ作成をテストしない
        with mock.patch.dict(os.environ, {"SKIP_TEST": "1"}):
            with mock.patch("lib.execution._get_execution_log_dir", return_value=mock_log_dir):
                result = check_skip_env(
                    "test-hook",
                    "SKIP_TEST",
                    {"command": "git worktree add", "tool_name": "Bash"},
                )

        assert result is True
