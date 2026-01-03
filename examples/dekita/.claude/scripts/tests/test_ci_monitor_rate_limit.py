#!/usr/bin/env python3
"""Unit tests for ci_monitor.rate_limit module."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

# Add scripts directory to path so we can import ci_monitor package
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the ci_monitor package for attribute access (Issue #2624)
import ci_monitor

# Import directly from ci_monitor package (Issue #2624)
from ci_monitor import load_monitor_state, save_monitor_state


class TestCheckRateLimit:
    """Tests for check_rate_limit function (Issue #896, #1347)."""

    def setup_method(self):
        """Clear cache before each test."""
        ci_monitor.rate_limit._rate_limit_cache = None

    def test_returns_rate_limit_info(self, tmp_path):
        """Test successful rate limit check."""
        nonexistent_cache = tmp_path / "nonexistent" / "cache.json"
        with (
            patch.object(ci_monitor, "RATE_LIMIT_FILE_CACHE_PATH", nonexistent_cache),
            patch.object(ci_monitor, "run_gh_command") as mock_gh,
        ):
            mock_gh.return_value = (True, "4500\t5000\t1703318400")
            remaining, limit, reset_ts = ci_monitor.check_rate_limit()
            assert remaining == 4500
            assert limit == 5000
            assert reset_ts == 1703318400

    def test_cache_returns_cached_value(self, tmp_path):
        """Test cache returns cached value within TTL (Issue #1347)."""
        nonexistent_cache = tmp_path / "nonexistent" / "cache.json"
        with (
            patch.object(ci_monitor, "RATE_LIMIT_FILE_CACHE_PATH", nonexistent_cache),
            patch.object(ci_monitor, "run_gh_command") as mock_gh,
        ):
            mock_gh.return_value = (True, "4500\t5000\t1703318400")
            # First call - should hit API
            ci_monitor.check_rate_limit()
            assert mock_gh.call_count == 1

            # Second call - should return cached value
            remaining, limit, reset_ts = ci_monitor.check_rate_limit()
            assert mock_gh.call_count == 1  # No additional API call
            assert remaining == 4500
            assert limit == 5000

    def test_use_cache_false_bypasses_cache(self, tmp_path):
        """Test use_cache=False forces fresh API call (Issue #1347)."""
        nonexistent_cache = tmp_path / "nonexistent" / "cache.json"
        with (
            patch.object(ci_monitor, "RATE_LIMIT_FILE_CACHE_PATH", nonexistent_cache),
            patch.object(ci_monitor, "run_gh_command") as mock_gh,
        ):
            mock_gh.return_value = (True, "4500\t5000\t1703318400")
            # First call
            ci_monitor.check_rate_limit()
            assert mock_gh.call_count == 1

            # Second call with use_cache=False
            ci_monitor.check_rate_limit(use_cache=False)
            assert mock_gh.call_count == 2  # Additional API call

    def test_cache_expires_after_ttl(self, tmp_path):
        """Test cache expires after TTL and triggers new API call (Issue #1347)."""
        import time as time_module

        base_time = time_module.time()
        nonexistent_cache = tmp_path / "nonexistent" / "cache.json"

        with (
            patch.object(ci_monitor, "RATE_LIMIT_FILE_CACHE_PATH", nonexistent_cache),
            patch.object(ci_monitor, "run_gh_command") as mock_gh,
        ):
            mock_gh.return_value = (True, "4500\t5000\t1703318400")

            # Patch time.time in ci_monitor module
            with patch.object(ci_monitor.main_loop.time, "time") as mock_time:
                # First call at base_time - caches the result
                mock_time.return_value = base_time
                ci_monitor.check_rate_limit()
                assert mock_gh.call_count == 1

                # Second call still within TTL - should use cache
                mock_time.return_value = base_time + 30
                ci_monitor.check_rate_limit()
                assert mock_gh.call_count == 1  # No new API call

                # Third call after TTL (61 seconds) - should trigger new API call
                mock_time.return_value = base_time + 61
                ci_monitor.check_rate_limit()
                assert mock_gh.call_count == 2  # New API call after TTL

    def test_returns_zeros_on_failure(self, tmp_path):
        """Test rate limit check returns zeros on failure."""
        nonexistent_cache = tmp_path / "nonexistent" / "cache.json"
        with (
            patch.object(ci_monitor, "RATE_LIMIT_FILE_CACHE_PATH", nonexistent_cache),
            patch.object(ci_monitor, "run_gh_command") as mock_gh,
        ):
            mock_gh.return_value = (False, "Error")
            remaining, limit, reset_ts = ci_monitor.check_rate_limit()
            assert remaining == 0
            assert limit == 0
            assert reset_ts == 0

    def test_returns_zeros_on_invalid_output(self, tmp_path):
        """Test rate limit check handles invalid output."""
        nonexistent_cache = tmp_path / "nonexistent" / "cache.json"
        with (
            patch.object(ci_monitor, "RATE_LIMIT_FILE_CACHE_PATH", nonexistent_cache),
            patch.object(ci_monitor, "run_gh_command") as mock_gh,
        ):
            mock_gh.return_value = (True, "invalid")
            remaining, limit, reset_ts = ci_monitor.check_rate_limit()
            assert remaining == 0
            assert limit == 0
            assert reset_ts == 0

    def test_file_cache_is_read_when_memory_cache_empty(self, tmp_path):
        """Test file cache is used when in-memory cache is empty (Issue #1291)."""
        import time as time_module

        cache_file = tmp_path / "rate-limit-cache.json"
        cache_data = {
            "timestamp": time_module.time(),
            "remaining": 3000,
            "limit": 5000,
            "reset": 1703318400,
        }
        cache_file.write_text(json.dumps(cache_data))

        # Clear in-memory cache to ensure file cache is tested
        ci_monitor.rate_limit._rate_limit_cache = None

        with (
            patch.object(ci_monitor, "RATE_LIMIT_FILE_CACHE_PATH", cache_file),
            patch.object(ci_monitor, "run_gh_command") as mock_gh,
        ):
            remaining, limit, reset_ts = ci_monitor.check_rate_limit()
            # Should use file cache, no API call
            assert mock_gh.call_count == 0
            assert remaining == 3000
            assert limit == 5000
            assert reset_ts == 1703318400

    def test_file_cache_is_written_after_api_call(self, tmp_path):
        """Test file cache is updated after API call (Issue #1291)."""
        cache_file = tmp_path / "rate-limit-cache.json"

        # Clear in-memory cache to ensure API is called
        ci_monitor.rate_limit._rate_limit_cache = None

        with (
            patch.object(ci_monitor, "RATE_LIMIT_FILE_CACHE_PATH", cache_file),
            patch.object(ci_monitor, "run_gh_command") as mock_gh,
        ):
            mock_gh.return_value = (True, "4500\t5000\t1703318400")
            ci_monitor.check_rate_limit()

            # Verify file cache was written
            assert cache_file.exists()
            data = json.loads(cache_file.read_text())
            assert data["remaining"] == 4500
            assert data["limit"] == 5000
            assert data["reset"] == 1703318400
            assert "timestamp" in data

    def test_file_cache_stale_triggers_api_call(self, tmp_path):
        """Test stale file cache triggers API call (Issue #1291)."""
        import time as time_module

        cache_file = tmp_path / "rate-limit-cache.json"
        # Create stale cache (older than TTL)
        cache_data = {
            "timestamp": time_module.time() - 120,  # 2 minutes ago
            "remaining": 3000,
            "limit": 5000,
            "reset": 1703318400,
        }
        cache_file.write_text(json.dumps(cache_data))

        # Clear in-memory cache
        ci_monitor.rate_limit._rate_limit_cache = None

        with (
            patch.object(ci_monitor, "RATE_LIMIT_FILE_CACHE_PATH", cache_file),
            patch.object(ci_monitor, "run_gh_command") as mock_gh,
        ):
            mock_gh.return_value = (True, "4000\t5000\t1703318500")
            remaining, limit, reset_ts = ci_monitor.check_rate_limit()

            # Should call API because file cache is stale
            assert mock_gh.call_count == 1
            assert remaining == 4000

    def test_file_cache_corrupted_triggers_api_call(self, tmp_path):
        """Test corrupted file cache triggers API fallback (Issue #1291)."""
        cache_file = tmp_path / "rate-limit-cache.json"
        cache_file.write_text("invalid json {{{")

        # Clear in-memory cache
        ci_monitor.rate_limit._rate_limit_cache = None

        with (
            patch.object(ci_monitor, "RATE_LIMIT_FILE_CACHE_PATH", cache_file),
            patch.object(ci_monitor, "run_gh_command") as mock_gh,
        ):
            mock_gh.return_value = (True, "4500\t5000\t1703318400")
            remaining, limit, reset_ts = ci_monitor.check_rate_limit()

            # Should fallback to API
            assert mock_gh.call_count == 1
            assert remaining == 4500

    def test_file_cache_future_timestamp_triggers_api_call(self, tmp_path):
        """Test future timestamp in file cache triggers API fallback (Issue #1291)."""
        import time as time_module

        cache_file = tmp_path / "rate-limit-cache.json"
        # Create cache with future timestamp
        cache_data = {
            "timestamp": time_module.time() + 3600,  # 1 hour in future
            "remaining": 3000,
            "limit": 5000,
            "reset": 1703318400,
        }
        cache_file.write_text(json.dumps(cache_data))

        # Clear in-memory cache
        ci_monitor.rate_limit._rate_limit_cache = None

        with (
            patch.object(ci_monitor, "RATE_LIMIT_FILE_CACHE_PATH", cache_file),
            patch.object(ci_monitor, "run_gh_command") as mock_gh,
        ):
            mock_gh.return_value = (True, "4000\t5000\t1703318500")
            remaining, limit, reset_ts = ci_monitor.check_rate_limit()

            # Should call API because future timestamp is invalid
            assert mock_gh.call_count == 1
            assert remaining == 4000

    def test_file_cache_negative_timestamp_triggers_api_call(self, tmp_path):
        """Test negative timestamp in file cache triggers API fallback (Issue #1291)."""
        cache_file = tmp_path / "rate-limit-cache.json"
        # Create cache with negative timestamp
        cache_data = {
            "timestamp": -100,
            "remaining": 3000,
            "limit": 5000,
            "reset": 1703318400,
        }
        cache_file.write_text(json.dumps(cache_data))

        # Clear in-memory cache
        ci_monitor.rate_limit._rate_limit_cache = None

        with (
            patch.object(ci_monitor, "RATE_LIMIT_FILE_CACHE_PATH", cache_file),
            patch.object(ci_monitor, "run_gh_command") as mock_gh,
        ):
            mock_gh.return_value = (True, "4000\t5000\t1703318500")
            remaining, limit, reset_ts = ci_monitor.check_rate_limit()

            # Should call API because negative timestamp is invalid
            assert mock_gh.call_count == 1
            assert remaining == 4000

    def test_memory_cache_takes_precedence_over_file_cache(self, tmp_path):
        """Test in-memory cache is checked before file cache (Issue #1291)."""
        import time as time_module

        cache_file = tmp_path / "rate-limit-cache.json"
        # Create valid file cache with different values
        cache_data = {
            "timestamp": time_module.time() - 10,  # 10 seconds ago
            "remaining": 1000,  # File cache value
            "limit": 5000,
            "reset": 1703318400,
        }
        cache_file.write_text(json.dumps(cache_data))

        # Set in-memory cache with different values
        ci_monitor.rate_limit._rate_limit_cache = (2000, 5000, 1703318500, time_module.time() - 5)

        with (
            patch.object(ci_monitor, "RATE_LIMIT_FILE_CACHE_PATH", cache_file),
            patch.object(ci_monitor, "run_gh_command") as mock_gh,
        ):
            remaining, limit, reset_ts = ci_monitor.check_rate_limit()

            # Should return in-memory cache values, not file cache
            assert mock_gh.call_count == 0
            assert remaining == 2000  # From in-memory, not 1000 from file
            assert reset_ts == 1703318500  # From in-memory


class TestGetAdjustedInterval:
    """Tests for get_adjusted_interval function (Issue #896)."""

    def test_normal_interval_when_high_remaining(self):
        """Test no adjustment when rate limit is high."""
        result = ci_monitor.get_adjusted_interval(30, 4000)
        assert result == 30

    def test_doubled_interval_when_moderately_low(self):
        """Test interval doubled when remaining < 500."""
        result = ci_monitor.get_adjusted_interval(30, 400)
        assert result == 60

    def test_quadrupled_interval_when_low(self):
        """Test interval quadrupled when remaining < 100."""
        result = ci_monitor.get_adjusted_interval(30, 80)
        assert result == 120

    def test_sixfold_interval_when_critical(self):
        """Test interval 6x when remaining < 50."""
        result = ci_monitor.get_adjusted_interval(30, 40)
        assert result == 180

    def test_sixfold_interval_when_zero_remaining(self):
        """Test interval 6x when remaining is 0 (rate limit exhausted)."""
        result = ci_monitor.get_adjusted_interval(30, 0)
        assert result == 180

    def test_boundary_at_critical_threshold(self):
        """Test boundary at RATE_LIMIT_CRITICAL_THRESHOLD (50).

        remaining=50 is NOT critical (condition is < 50), so it gets 4x.
        remaining=49 IS critical, so it gets 6x.
        """
        # At boundary: remaining=50 is NOT critical (< 50 is False)
        result = ci_monitor.get_adjusted_interval(30, 50)
        assert result == 120  # 4x (warning level, not critical)

        # Below boundary: remaining=49 IS critical
        result = ci_monitor.get_adjusted_interval(30, 49)
        assert result == 180  # 6x (critical level)

    def test_boundary_at_warning_threshold(self):
        """Test boundary at RATE_LIMIT_WARNING_THRESHOLD (100).

        remaining=100 is NOT low (condition is < 100), so it gets 2x.
        remaining=99 IS low, so it gets 4x.
        """
        # At boundary: remaining=100 is NOT low (< 100 is False)
        result = ci_monitor.get_adjusted_interval(30, 100)
        assert result == 60  # 2x (moderate level)

        # Below boundary: remaining=99 IS low
        result = ci_monitor.get_adjusted_interval(30, 99)
        assert result == 120  # 4x (low level)

    def test_boundary_at_adjust_threshold(self):
        """Test boundary at RATE_LIMIT_ADJUST_THRESHOLD (500).

        remaining=500 is normal (condition is < 500 is False).
        remaining=499 starts adjustment (2x).
        """
        # At boundary: remaining=500 is normal (< 500 is False)
        result = ci_monitor.get_adjusted_interval(30, 500)
        assert result == 30  # Normal interval

        # Below boundary: remaining=499 triggers adjustment
        result = ci_monitor.get_adjusted_interval(30, 499)
        assert result == 60  # 2x (moderate level)


class TestShouldPreferRestApi:
    """Tests for should_prefer_rest_api function (Issue #1360)."""

    def setup_method(self):
        """Reset REST priority mode state and cache before each test."""
        ci_monitor.rate_limit._rate_limit_cache = None
        # Thread-safe reset of REST priority mode state
        with ci_monitor.rate_limit._rest_priority_mode_lock:
            ci_monitor.rate_limit._rest_priority_mode_active = False

    def test_returns_true_when_below_threshold(self):
        """Test returns True when remaining < RATE_LIMIT_REST_PRIORITY_THRESHOLD."""
        with patch.object(ci_monitor, "check_rate_limit") as mock_check:
            mock_check.return_value = (150, 5000, 1703318400)
            result = ci_monitor.should_prefer_rest_api(log_transition=False)
            assert result is True

    def test_returns_false_when_above_threshold(self):
        """Test returns False when remaining >= RATE_LIMIT_REST_PRIORITY_THRESHOLD."""
        with patch.object(ci_monitor, "check_rate_limit") as mock_check:
            mock_check.return_value = (250, 5000, 1703318400)
            result = ci_monitor.should_prefer_rest_api(log_transition=False)
            assert result is False

    def test_returns_false_when_api_check_fails(self):
        """Test returns False when rate limit check fails (limit=0)."""
        with patch.object(ci_monitor, "check_rate_limit") as mock_check:
            mock_check.return_value = (0, 0, 0)
            result = ci_monitor.should_prefer_rest_api(log_transition=False)
            assert result is False

    def test_boundary_at_threshold(self):
        """Test boundary at RATE_LIMIT_REST_PRIORITY_THRESHOLD (200).

        remaining=200 is NOT below threshold (condition is < 200).
        remaining=199 IS below threshold.
        """
        with patch.object(ci_monitor, "check_rate_limit") as mock_check:
            # At boundary: remaining=200 is NOT below threshold
            mock_check.return_value = (200, 5000, 1703318400)
            assert ci_monitor.should_prefer_rest_api(log_transition=False) is False

            # Below boundary: remaining=199 IS below threshold
            mock_check.return_value = (199, 5000, 1703318400)
            assert ci_monitor.should_prefer_rest_api(log_transition=False) is True

    def test_logs_entering_rest_priority_mode(self, capsys):
        """Test logs when entering REST priority mode."""
        with patch.object(ci_monitor, "check_rate_limit") as mock_check:
            with patch.object(ci_monitor, "log_rate_limit_event") as mock_log:
                mock_check.return_value = (150, 5000, 1703318400)
                ci_monitor.should_prefer_rest_api(log_transition=True)

                captured = capsys.readouterr()
                assert "REST優先モードに切り替え" in captured.err
                mock_log.assert_called_once()
                assert (
                    mock_log.call_args[0][0] == ci_monitor.RateLimitEventType.REST_PRIORITY_ENTERED
                )

    def test_logs_exiting_rest_priority_mode(self, capsys):
        """Test logs when exiting REST priority mode."""
        # First, enter REST priority mode (thread-safe)
        with ci_monitor.rate_limit._rest_priority_mode_lock:
            ci_monitor.rate_limit._rest_priority_mode_active = True

        with patch.object(ci_monitor, "check_rate_limit") as mock_check:
            with patch.object(ci_monitor, "log_rate_limit_event") as mock_log:
                mock_check.return_value = (250, 5000, 1703318400)
                ci_monitor.should_prefer_rest_api(log_transition=True)

                captured = capsys.readouterr()
                assert "GraphQLモードに復帰" in captured.err
                mock_log.assert_called_once()
                assert (
                    mock_log.call_args[0][0] == ci_monitor.RateLimitEventType.REST_PRIORITY_EXITED
                )

    def test_does_not_log_when_no_transition(self, capsys):
        """Test does not log when state doesn't change."""
        with patch.object(ci_monitor, "check_rate_limit") as mock_check:
            with patch.object(ci_monitor, "log_rate_limit_event") as mock_log:
                # Both calls return above threshold (no transition)
                mock_check.return_value = (250, 5000, 1703318400)
                ci_monitor.should_prefer_rest_api(log_transition=True)
                ci_monitor.should_prefer_rest_api(log_transition=True)

                # No log event should be recorded
                mock_log.assert_not_called()

    def test_log_transition_false_disables_logging(self, capsys):
        """Test log_transition=False disables state transition logging."""
        with patch.object(ci_monitor, "check_rate_limit") as mock_check:
            with patch.object(ci_monitor, "log_rate_limit_event") as mock_log:
                mock_check.return_value = (150, 5000, 1703318400)
                ci_monitor.should_prefer_rest_api(log_transition=False)

                captured = capsys.readouterr()
                assert captured.err == ""
                mock_log.assert_not_called()


class TestLogRateLimitWarning:
    """Tests for log_rate_limit_warning function (Issue #896)."""

    def test_logs_critical_warning(self, capsys):
        """Test critical warning is logged when remaining < 50."""
        import time as time_module

        reset_ts = int(time_module.time()) + 600
        ci_monitor.log_rate_limit_warning(40, 5000, reset_ts)
        captured = capsys.readouterr()
        assert "[CRITICAL]" in captured.out
        assert "very low" in captured.out

    def test_logs_warning_when_low(self, capsys):
        """Test warning is logged when remaining < 100."""
        import time as time_module

        reset_ts = int(time_module.time()) + 600
        ci_monitor.log_rate_limit_warning(80, 5000, reset_ts)
        captured = capsys.readouterr()
        assert "rate limit low" in captured.out

    def test_no_warning_when_high(self, capsys):
        """Test no warning when remaining is high."""
        import time as time_module

        reset_ts = int(time_module.time()) + 600
        ci_monitor.log_rate_limit_warning(4000, 5000, reset_ts)
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_logs_critical_warning_when_zero_remaining(self, capsys):
        """Test critical warning when remaining is 0 (rate limit exhausted)."""
        import time as time_module

        reset_ts = int(time_module.time()) + 600
        ci_monitor.log_rate_limit_warning(0, 5000, reset_ts)
        captured = capsys.readouterr()
        assert "[CRITICAL]" in captured.out
        assert "very low" in captured.out

    def test_logs_json_in_json_mode(self, capsys):
        """Test JSON output in json_mode."""
        import time as time_module

        reset_ts = int(time_module.time()) + 600
        ci_monitor.log_rate_limit_warning(40, 5000, reset_ts, json_mode=True)
        captured = capsys.readouterr()
        # In json_mode, output should be valid JSON (written to stderr)
        parsed = json.loads(captured.err.strip())
        assert parsed["type"] == "log"
        assert "CRITICAL" in parsed["message"]


class TestFormatResetTime:
    """Tests for format_reset_time function (Issue #1244)."""

    def test_zero_timestamp_returns_unknown(self):
        """Test that zero timestamp returns '不明'."""
        seconds, human = ci_monitor.format_reset_time(0)
        assert seconds == 0
        assert human == "不明"

    def test_past_timestamp_returns_soon(self):
        """Test that past timestamp returns 'まもなく'."""
        import time as time_module

        past_ts = int(time_module.time()) - 60
        seconds, human = ci_monitor.format_reset_time(past_ts)
        assert seconds == 0
        assert human == "まもなく"

    def test_less_than_minute_returns_seconds(self):
        """Test that <60 seconds returns seconds."""
        import time as time_module

        future_ts = int(time_module.time()) + 30
        seconds, human = ci_monitor.format_reset_time(future_ts)
        assert 28 <= seconds <= 32  # Allow some tolerance
        assert "秒" in human

    def test_more_than_minute_returns_minutes(self):
        """Test that >=60 seconds returns minutes."""
        import time as time_module

        future_ts = int(time_module.time()) + 180  # 3 minutes
        seconds, human = ci_monitor.format_reset_time(future_ts)
        assert seconds >= 178
        assert "分" in human


class TestPrintRateLimitWarning:
    """Tests for print_rate_limit_warning function (Issue #1244)."""

    def test_prints_warning_message(self, capsys):
        """Test that warning message is printed to stderr."""
        with patch.object(ci_monitor, "check_rate_limit") as mock_check:
            with patch.object(ci_monitor, "log_rate_limit_event") as mock_log:
                import time as time_module

                reset_ts = int(time_module.time()) + 600
                mock_check.return_value = (0, 5000, reset_ts)
                ci_monitor.print_rate_limit_warning()
                captured = capsys.readouterr()
                assert "レート制限" in captured.err
                assert mock_log.called

    def test_calls_log_rate_limit_event_with_limit_reached(self):
        """Test that log_rate_limit_event is called with 'limit_reached' event type."""
        with patch.object(ci_monitor, "check_rate_limit") as mock_check:
            with patch.object(ci_monitor, "log_rate_limit_event") as mock_log:
                import time as time_module

                reset_ts = int(time_module.time()) + 600
                mock_check.return_value = (0, 5000, reset_ts)
                ci_monitor.print_rate_limit_warning()
                mock_log.assert_called_once_with("limit_reached", 0, 5000, reset_ts)

    def test_single_check_rate_limit_call(self):
        """Test that check_rate_limit is called only once (Issue #1244 optimization)."""
        with patch.object(ci_monitor, "check_rate_limit") as mock_check:
            with patch.object(ci_monitor, "log_rate_limit_event"):
                import time as time_module

                reset_ts = int(time_module.time()) + 600
                mock_check.return_value = (0, 5000, reset_ts)
                ci_monitor.print_rate_limit_warning()
                # Should only call check_rate_limit once, not twice
                assert mock_check.call_count == 1


class TestLogRateLimitEvent:
    """Tests for log_rate_limit_event function (Issue #1244)."""

    def test_basic_event_logging(self):
        """Test basic event logging with required parameters."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            ci_monitor.log_rate_limit_event("warning", 4500, 5000, 1735368000)
            mock_log.assert_called_once()
            call_args = mock_log.call_args
            assert call_args.kwargs["hook_name"] == "ci-monitor"
            assert call_args.kwargs["decision"] == "warning"
            assert call_args.kwargs["reason"] == "Rate limit: 4500/5000"
            details = call_args.kwargs["details"]
            assert details["remaining"] == 4500
            assert details["limit"] == 5000
            assert details["reset_timestamp"] == 1735368000
            assert details["usage_percent"] == 10.0

    def test_details_parameter_merged(self):
        """Test that details parameter is correctly merged."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            ci_monitor.log_rate_limit_event(
                "adjusted_interval",
                100,
                5000,
                1735368000,
                {"old_interval": 30, "new_interval": 60},
            )
            call_args = mock_log.call_args
            details = call_args.kwargs["details"]
            # Standard fields
            assert details["remaining"] == 100
            assert details["limit"] == 5000
            # Merged fields
            assert details["old_interval"] == 30
            assert details["new_interval"] == 60

    def test_usage_percent_calculation(self):
        """Test usage_percent is correctly calculated."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            # 50% usage
            ci_monitor.log_rate_limit_event("warning", 2500, 5000, 1735368000)
            details = mock_log.call_args.kwargs["details"]
            assert details["usage_percent"] == 50.0

            # 0% usage (full capacity)
            ci_monitor.log_rate_limit_event("warning", 5000, 5000, 1735368000)
            details = mock_log.call_args.kwargs["details"]
            assert details["usage_percent"] == 0.0

            # 100% usage (exhausted)
            ci_monitor.log_rate_limit_event("limit_reached", 0, 5000, 1735368000)
            details = mock_log.call_args.kwargs["details"]
            assert details["usage_percent"] == 100.0

    def test_zero_limit_handling(self):
        """Test that limit=0 doesn't cause division by zero."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            # Should not raise ZeroDivisionError
            ci_monitor.log_rate_limit_event("warning", 0, 0, 0)
            details = mock_log.call_args.kwargs["details"]
            assert details["usage_percent"] == 0

    def test_event_types(self):
        """Test different event types are logged correctly."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            for event_type in ["warning", "limit_reached", "adjusted_interval"]:
                ci_monitor.log_rate_limit_event(event_type, 100, 5000, 1735368000)
                assert mock_log.call_args.kwargs["decision"] == event_type

    def test_recovered_event_type(self):
        """Test recovered event type is logged correctly (Issue #1385)."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            ci_monitor.log_rate_limit_event(
                "recovered",
                4500,
                5000,
                1735368000,
                {"base_interval": 30, "previous_interval": 120},
            )
            mock_log.assert_called_once()
            call_args = mock_log.call_args
            assert call_args.kwargs["decision"] == "recovered"
            details = call_args.kwargs["details"]
            assert details["base_interval"] == 30
            assert details["previous_interval"] == 120
            assert details["remaining"] == 4500
            assert details["usage_percent"] == 10.0

    def test_adjusted_interval_direction_increase(self):
        """Test adjusted_interval with direction=increase (Issue #1385)."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            ci_monitor.log_rate_limit_event(
                "adjusted_interval",
                100,
                5000,
                1735368000,
                {"old_interval": 30, "new_interval": 60, "direction": "increase"},
            )
            mock_log.assert_called_once()
            call_args = mock_log.call_args
            details = call_args.kwargs["details"]
            assert details["direction"] == "increase"
            assert details["old_interval"] == 30
            assert details["new_interval"] == 60
            assert details["remaining"] == 100
            assert details["usage_percent"] == 98.0

    def test_adjusted_interval_direction_decrease(self):
        """Test adjusted_interval with direction=decrease (Issue #1385)."""
        with patch.object(ci_monitor, "log_hook_execution") as mock_log:
            ci_monitor.log_rate_limit_event(
                "adjusted_interval",
                4000,
                5000,
                1735368000,
                {"old_interval": 120, "new_interval": 60, "direction": "decrease"},
            )
            mock_log.assert_called_once()
            call_args = mock_log.call_args
            details = call_args.kwargs["details"]
            assert details["direction"] == "decrease"
            assert details["old_interval"] == 120
            assert details["new_interval"] == 60
            assert details["remaining"] == 4000
            assert details["usage_percent"] == 20.0


class TestMonitorStateWithRateLimit:
    """Tests for save_monitor_state and load_monitor_state with rate limit info (Issue #1373)."""

    def test_save_and_load_state_with_rate_limit(self, tmp_path: Path):
        """Test that rate limit info is correctly saved and loaded."""
        state_file = tmp_path / ".claude" / "state" / "ci-monitor-123.json"
        with patch.object(ci_monitor, "get_state_file_path", return_value=state_file):
            state = {
                "status": "monitoring",
                "rebase_count": 2,
                "elapsed_seconds": 120,
                "rate_limit": {
                    "remaining": 4500,
                    "limit": 5000,
                    "reset_at": 1735400000,
                },
            }
            result = save_monitor_state("123", state)
            assert result is True

            loaded = load_monitor_state("123")
            assert loaded is not None
            assert loaded["rate_limit"]["remaining"] == 4500
            assert loaded["rate_limit"]["limit"] == 5000
            assert loaded["rate_limit"]["reset_at"] == 1735400000

    def test_load_state_without_rate_limit(self, tmp_path: Path):
        """Test that loading state without rate_limit field works correctly."""
        state_file = tmp_path / ".claude" / "state" / "ci-monitor-456.json"
        with patch.object(ci_monitor, "get_state_file_path", return_value=state_file):
            state = {
                "status": "monitoring",
                "rebase_count": 1,
                "elapsed_seconds": 60,
            }
            save_monitor_state("456", state)

            loaded = load_monitor_state("456")
            assert loaded is not None
            assert "rate_limit" not in loaded
            assert loaded["status"] == "monitoring"
