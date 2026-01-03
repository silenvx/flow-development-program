"""Tests for ci_monitor.rate_limit module."""

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import ci_monitor.rate_limit as rl
from ci_monitor.rate_limit import (
    _read_rate_limit_file_cache,
    _write_rate_limit_file_cache,
    check_rate_limit,
    format_reset_time,
    get_adjusted_interval,
    get_rate_limit_reset_time,
    log_rate_limit_warning_to_console,
    print_rate_limit_warning,
    should_prefer_rest_api,
)


class TestFormatResetTime:
    """Tests for format_reset_time function."""

    def test_zero_timestamp(self):
        """Test with zero timestamp."""
        seconds, human = format_reset_time(0)
        assert seconds == 0
        assert human == "不明"

    def test_past_timestamp(self):
        """Test with past timestamp."""
        past = int(time.time()) - 100
        seconds, human = format_reset_time(past)
        assert seconds == 0
        assert human == "まもなく"

    def test_future_timestamp_seconds(self):
        """Test with future timestamp within 60 seconds."""
        future = int(time.time()) + 30
        seconds, human = format_reset_time(future)
        assert 25 <= seconds <= 35  # Allow some tolerance
        assert "秒" in human

    def test_future_timestamp_minutes(self):
        """Test with future timestamp over 60 seconds."""
        future = int(time.time()) + 180
        seconds, human = format_reset_time(future)
        assert 175 <= seconds <= 185  # Allow some tolerance
        assert "分" in human


class TestFileCacheFunctions:
    """Tests for file cache read/write functions."""

    def test_read_nonexistent_cache(self):
        """Test reading from non-existent cache file."""
        with patch.object(rl, "RATE_LIMIT_FILE_CACHE_PATH", Path("/nonexistent/path")):
            result = _read_rate_limit_file_cache()
            assert result is None

    def test_write_and_read_cache(self):
        """Test writing and reading cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "rate-limit-cache.json"
            with patch.object(rl, "RATE_LIMIT_FILE_CACHE_PATH", cache_path):
                # Write cache
                _write_rate_limit_file_cache(100, 5000, int(time.time()) + 3600)

                # Read cache
                result = _read_rate_limit_file_cache()
                assert result is not None
                remaining, limit, reset, cached_at = result
                assert remaining == 100
                assert limit == 5000

    def test_read_corrupted_cache(self):
        """Test reading corrupted cache file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "rate-limit-cache.json"
            cache_path.write_text("not valid json")
            with patch.object(rl, "RATE_LIMIT_FILE_CACHE_PATH", cache_path):
                result = _read_rate_limit_file_cache()
                assert result is None

    def test_read_stale_cache(self):
        """Test reading stale cache (older than TTL)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "rate-limit-cache.json"
            # Write cache with old timestamp
            cache_data = {
                "timestamp": time.time() - 120,  # 2 minutes ago (TTL is 60s)
                "remaining": 100,
                "limit": 5000,
                "reset": int(time.time()) + 3600,
            }
            cache_path.write_text(json.dumps(cache_data))
            with patch.object(rl, "RATE_LIMIT_FILE_CACHE_PATH", cache_path):
                result = _read_rate_limit_file_cache()
                assert result is None

    def test_read_cache_with_string_timestamp(self):
        """Test reading cache with non-numeric timestamp (string)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "rate-limit-cache.json"
            cache_data = {
                "timestamp": "not a number",
                "remaining": 100,
                "limit": 5000,
                "reset": int(time.time()) + 3600,
            }
            cache_path.write_text(json.dumps(cache_data))
            with patch.object(rl, "RATE_LIMIT_FILE_CACHE_PATH", cache_path):
                result = _read_rate_limit_file_cache()
                assert result is None

    def test_read_cache_with_null_timestamp(self):
        """Test reading cache with null timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "rate-limit-cache.json"
            cache_data = {
                "timestamp": None,
                "remaining": 100,
                "limit": 5000,
                "reset": int(time.time()) + 3600,
            }
            cache_path.write_text(json.dumps(cache_data))
            with patch.object(rl, "RATE_LIMIT_FILE_CACHE_PATH", cache_path):
                result = _read_rate_limit_file_cache()
                assert result is None

    def test_read_cache_with_negative_timestamp(self):
        """Test reading cache with negative timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "rate-limit-cache.json"
            cache_data = {
                "timestamp": -100,
                "remaining": 100,
                "limit": 5000,
                "reset": int(time.time()) + 3600,
            }
            cache_path.write_text(json.dumps(cache_data))
            with patch.object(rl, "RATE_LIMIT_FILE_CACHE_PATH", cache_path):
                result = _read_rate_limit_file_cache()
                assert result is None

    def test_read_cache_with_future_timestamp(self):
        """Test reading cache with future timestamp (clock skew)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "rate-limit-cache.json"
            cache_data = {
                "timestamp": time.time() + 3600,  # 1 hour in the future
                "remaining": 100,
                "limit": 5000,
                "reset": int(time.time()) + 3600,
            }
            cache_path.write_text(json.dumps(cache_data))
            with patch.object(rl, "RATE_LIMIT_FILE_CACHE_PATH", cache_path):
                result = _read_rate_limit_file_cache()
                assert result is None


class TestCheckRateLimit:
    """Tests for check_rate_limit function."""

    @patch.object(rl, "run_gh_command")
    def test_successful_api_call(self, mock_run):
        """Test successful rate limit API call."""
        # Reset cache
        rl._rate_limit_cache = None

        mock_run.return_value = (True, "4500\t5000\t1704067200")

        with patch.object(rl, "_read_rate_limit_file_cache", return_value=None):
            remaining, limit, reset = check_rate_limit(use_cache=False)
            assert remaining == 4500
            assert limit == 5000
            assert reset == 1704067200

    @patch.object(rl, "run_gh_command")
    def test_failed_api_call(self, mock_run):
        """Test failed rate limit API call."""
        rl._rate_limit_cache = None

        mock_run.return_value = (False, "error")

        with patch.object(rl, "_read_rate_limit_file_cache", return_value=None):
            remaining, limit, reset = check_rate_limit(use_cache=False)
            assert remaining == 0
            assert limit == 0
            assert reset == 0

    @patch.object(rl, "run_gh_command")
    def test_incomplete_tsv_output(self, mock_run):
        """Test API response with less than 3 parts."""
        rl._rate_limit_cache = None

        mock_run.return_value = (True, "100\t5000")  # Only 2 parts instead of 3

        with patch.object(rl, "_read_rate_limit_file_cache", return_value=None):
            remaining, limit, reset = check_rate_limit(use_cache=False)
            assert remaining == 0
            assert limit == 0
            assert reset == 0

    def test_uses_memory_cache(self):
        """Test that memory cache is used when available."""
        # Set up fresh cache
        rl._rate_limit_cache = (4000, 5000, 1704067200, time.time())

        remaining, limit, reset = check_rate_limit(use_cache=True)
        assert remaining == 4000
        assert limit == 5000

    @patch.object(rl, "run_gh_command")
    def test_file_cache_integration(self, mock_run):
        """Test file cache is used when memory cache is empty."""
        # Clear memory cache
        rl._rate_limit_cache = None

        # Mock file cache to return valid data
        file_cache_data = (3000, 5000, 1704067200, time.time())
        with patch.object(rl, "_read_rate_limit_file_cache", return_value=file_cache_data):
            remaining, limit, reset = check_rate_limit(use_cache=True)
            # Should use file cache, not call API
            mock_run.assert_not_called()
            assert remaining == 3000
            assert limit == 5000


class TestGetRateLimitResetTime:
    """Tests for get_rate_limit_reset_time function."""

    @patch.object(rl, "check_rate_limit")
    def test_returns_formatted_time(self, mock_check):
        """Test that reset time is properly formatted."""
        future_time = int(time.time()) + 300  # 5 minutes from now
        mock_check.return_value = (100, 5000, future_time)

        seconds, human = get_rate_limit_reset_time()
        assert 295 <= seconds <= 305
        assert "分" in human


class TestGetAdjustedInterval:
    """Tests for get_adjusted_interval function."""

    def test_critical_threshold(self):
        """Test interval adjustment at critical threshold."""
        # Below critical (50)
        interval = get_adjusted_interval(30, 40)
        assert interval == 180  # 30 * 6

    def test_warning_threshold(self):
        """Test interval adjustment at warning threshold."""
        # Below warning (100) but above critical
        interval = get_adjusted_interval(30, 80)
        assert interval == 120  # 30 * 4

    def test_adjust_threshold(self):
        """Test interval adjustment at adjust threshold."""
        # Below adjust (500) but above warning
        interval = get_adjusted_interval(30, 200)
        assert interval == 60  # 30 * 2

    def test_normal_threshold(self):
        """Test interval with normal remaining count."""
        # Above all thresholds
        interval = get_adjusted_interval(30, 1000)
        assert interval == 30  # No adjustment


class TestShouldPreferRestApi:
    """Tests for should_prefer_rest_api function."""

    @patch.object(rl, "check_rate_limit")
    def test_returns_true_below_threshold(self, mock_check):
        """Test returns True when below REST priority threshold."""
        rl._rest_priority_mode_active = False

        mock_check.return_value = (150, 5000, 1704067200)  # Below 200 threshold

        result = should_prefer_rest_api(log_transition=False)
        assert result is True

    @patch.object(rl, "check_rate_limit")
    def test_returns_false_above_threshold(self, mock_check):
        """Test returns False when above REST priority threshold."""
        mock_check.return_value = (300, 5000, 1704067200)  # Above 200 threshold

        result = should_prefer_rest_api(log_transition=False)
        assert result is False

    @patch.object(rl, "check_rate_limit")
    def test_returns_false_on_api_failure(self, mock_check):
        """Test returns False when API check fails."""
        mock_check.return_value = (0, 0, 0)  # API failure

        result = should_prefer_rest_api(log_transition=False)
        assert result is False

    @patch.object(rl, "check_rate_limit")
    def test_log_transition_entering_rest_mode(self, mock_check, capsys):
        """Test logging when entering REST priority mode."""
        rl._rest_priority_mode_active = False
        mock_check.return_value = (150, 5000, 1704067200)  # Below 200 threshold

        log_event_fn = MagicMock()
        result = should_prefer_rest_api(log_transition=True, log_event_fn=log_event_fn)

        assert result is True
        captured = capsys.readouterr()
        assert "REST優先モード" in captured.err
        log_event_fn.assert_called_once()

    @patch.object(rl, "check_rate_limit")
    def test_log_transition_exiting_rest_mode(self, mock_check, capsys):
        """Test logging when exiting REST priority mode."""
        rl._rest_priority_mode_active = True
        mock_check.return_value = (300, 5000, 1704067200)  # Above 200 threshold

        log_event_fn = MagicMock()
        result = should_prefer_rest_api(log_transition=True, log_event_fn=log_event_fn)

        assert result is False
        captured = capsys.readouterr()
        assert "GraphQLモードに復帰" in captured.err
        log_event_fn.assert_called_once()


class TestPrintRateLimitWarning:
    """Tests for print_rate_limit_warning function."""

    @patch.object(rl, "check_rate_limit")
    def test_prints_warning(self, mock_check, capsys):
        """Test that warning is printed."""
        future_time = int(time.time()) + 300
        mock_check.return_value = (0, 5000, future_time)

        print_rate_limit_warning()

        captured = capsys.readouterr()
        assert "GraphQL API" in captured.err
        assert "レート制限" in captured.err

    @patch.object(rl, "check_rate_limit")
    def test_calls_log_event_fn(self, mock_check):
        """Test that log_event_fn is called."""
        future_time = int(time.time()) + 300
        mock_check.return_value = (0, 5000, future_time)

        log_event_fn = MagicMock()
        print_rate_limit_warning(log_event_fn=log_event_fn)

        log_event_fn.assert_called_once()


class TestLogRateLimitWarningToConsole:
    """Tests for log_rate_limit_warning_to_console function."""

    def test_critical_warning(self, capsys):
        """Test critical warning output."""
        log_rate_limit_warning_to_console(
            remaining=30,  # Below critical (50)
            limit=5000,
            reset_timestamp=int(time.time()) + 300,
        )

        captured = capsys.readouterr()
        assert "CRITICAL" in captured.out
        assert "pausing" in captured.out.lower()

    def test_warning_level(self, capsys):
        """Test warning level output."""
        log_rate_limit_warning_to_console(
            remaining=80,  # Below warning (100) but above critical
            limit=5000,
            reset_timestamp=int(time.time()) + 300,
        )

        captured = capsys.readouterr()
        assert "low" in captured.out.lower()

    def test_calls_log_event_fn(self):
        """Test that log_event_fn is called for critical."""
        log_event_fn = MagicMock()
        log_rate_limit_warning_to_console(
            remaining=30,
            limit=5000,
            reset_timestamp=int(time.time()) + 300,
            log_event_fn=log_event_fn,
        )

        log_event_fn.assert_called_once()

    def test_json_mode_critical(self):
        """Test JSON mode output for critical level."""
        log_fn = MagicMock()
        log_rate_limit_warning_to_console(
            remaining=30,
            limit=5000,
            reset_timestamp=int(time.time()) + 300,
            json_mode=True,
            log_fn=log_fn,
        )

        log_fn.assert_called_once()
        args = log_fn.call_args[0]
        assert "CRITICAL" in args[0]
        assert args[1] is True  # json_mode
        assert args[2]["level"] == "critical"

    def test_json_mode_warning(self):
        """Test JSON mode output for warning level."""
        log_fn = MagicMock()
        log_rate_limit_warning_to_console(
            remaining=80,
            limit=5000,
            reset_timestamp=int(time.time()) + 300,
            json_mode=True,
            log_fn=log_fn,
        )

        log_fn.assert_called_once()
        args = log_fn.call_args[0]
        assert args[2]["level"] == "warning"

    def test_no_output_above_warning_threshold(self, capsys):
        """Test no output when remaining is above warning threshold."""
        log_event_fn = MagicMock()
        log_rate_limit_warning_to_console(
            remaining=500,  # Above warning threshold (100)
            limit=5000,
            reset_timestamp=int(time.time()) + 300,
            log_event_fn=log_event_fn,
        )

        captured = capsys.readouterr()
        assert captured.out == ""
        log_event_fn.assert_not_called()
