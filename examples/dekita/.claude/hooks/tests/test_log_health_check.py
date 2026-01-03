"""Tests for log_health_check.py - Log health validation hook."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add hooks directory to path
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from log_health_check import (
    THRESHOLD_LOG_FRESHNESS_MINUTES,
    THRESHOLD_MAX_HOOK_EXECUTIONS,
    THRESHOLD_METRICS_LOG_DISCREPANCY_RATIO,
    THRESHOLD_MIN_DISK_SPACE_MB,
    THRESHOLD_MIN_HOOK_EXECUTIONS,
    check_disk_space,
    check_log_freshness,
    check_log_health,
    check_log_writable,
    count_hook_executions_in_log,
    format_health_report,
    get_session_metrics,
    main,
)


class TestGetSessionMetrics:
    """Tests for get_session_metrics function."""

    def test_returns_none_when_file_not_exists(self, tmp_path: Path) -> None:
        """ファイルが存在しない場合はNoneを返す."""
        with patch("log_health_check.METRICS_LOG_DIR", tmp_path):
            result = get_session_metrics("test-session-id")
            assert result is None

    def test_returns_metrics_for_matching_session(self, tmp_path: Path) -> None:
        """一致するセッションIDのメトリクスを返す."""
        metrics_file = tmp_path / "session-metrics.log"
        session_id = "test-session-123"
        metrics_data = {
            "session_id": session_id,
            "hook_executions": 100,
            "blocks": 5,
            "approves": 95,
        }
        metrics_file.write_text(json.dumps(metrics_data) + "\n")

        with patch("log_health_check.METRICS_LOG_DIR", tmp_path):
            result = get_session_metrics(session_id)
            assert result is not None
            assert result["session_id"] == session_id
            assert result["hook_executions"] == 100

    def test_returns_latest_entry_for_session(self, tmp_path: Path) -> None:
        """同じセッションIDの複数エントリがある場合、最新を返す."""
        metrics_file = tmp_path / "session-metrics.log"
        session_id = "test-session-123"
        old_data = {"session_id": session_id, "hook_executions": 50}
        new_data = {"session_id": session_id, "hook_executions": 100}
        metrics_file.write_text(json.dumps(old_data) + "\n" + json.dumps(new_data) + "\n")

        with patch("log_health_check.METRICS_LOG_DIR", tmp_path):
            result = get_session_metrics(session_id)
            assert result is not None
            assert result["hook_executions"] == 100

    def test_returns_none_for_non_matching_session(self, tmp_path: Path) -> None:
        """一致するセッションIDがない場合はNoneを返す."""
        metrics_file = tmp_path / "session-metrics.log"
        metrics_data = {"session_id": "other-session", "hook_executions": 100}
        metrics_file.write_text(json.dumps(metrics_data) + "\n")

        with patch("log_health_check.METRICS_LOG_DIR", tmp_path):
            result = get_session_metrics("test-session-123")
            assert result is None


class TestCountHookExecutionsInLog:
    """Tests for count_hook_executions_in_log function.

    Issue #2068: セッション毎ファイル形式に対応。
    """

    def test_returns_zero_when_file_not_exists(self, tmp_path: Path) -> None:
        """ファイルが存在しない場合は0を返す."""
        with patch("log_health_check.EXECUTION_LOG_DIR", tmp_path):
            result = count_hook_executions_in_log("nonexistent-session")
            assert result == 0

    def test_returns_zero_when_session_id_empty(self, tmp_path: Path) -> None:
        """session_idが空の場合は0を返す."""
        with patch("log_health_check.EXECUTION_LOG_DIR", tmp_path):
            result = count_hook_executions_in_log("")
            assert result == 0

    def test_counts_entries_in_session_file(self, tmp_path: Path) -> None:
        """セッション固有ファイル内のエントリ数をカウント."""
        session_id = "test-session-123"
        # セッション固有ファイル形式: hook-execution-{session_id}.jsonl
        log_file = tmp_path / f"hook-execution-{session_id}.jsonl"
        entries = [
            {"hook": "hook1", "timestamp": "2025-01-01T00:00:00"},
            {"hook": "hook2", "timestamp": "2025-01-01T00:00:01"},
            {"hook": "hook3", "timestamp": "2025-01-01T00:00:02"},
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        with patch("log_health_check.EXECUTION_LOG_DIR", tmp_path):
            result = count_hook_executions_in_log(session_id)
            assert result == 3


class TestCheckLogFreshness:
    """Tests for check_log_freshness function (Issue #1455)."""

    def test_returns_false_none_when_file_not_exists(self, tmp_path: Path) -> None:
        """ファイルが存在しない場合は(False, None)を返す."""
        non_existent = tmp_path / "non_existent.log"
        is_fresh, age = check_log_freshness(non_existent)
        assert is_fresh is False
        assert age is None

    def test_returns_true_for_fresh_file(self, tmp_path: Path) -> None:
        """新しいファイルは(True, age)を返す."""
        log_file = tmp_path / "test.log"
        log_file.write_text("test content")
        is_fresh, age = check_log_freshness(log_file)
        assert is_fresh is True
        assert age is not None
        assert age < 1  # 1分未満

    def test_returns_false_for_old_file(self, tmp_path: Path) -> None:
        """古いファイルは(False, age)を返す."""
        import os
        import time

        log_file = tmp_path / "old.log"
        log_file.write_text("old content")
        # ファイルの更新日時を15分前に設定
        old_time = time.time() - (15 * 60)
        os.utime(log_file, (old_time, old_time))

        is_fresh, age = check_log_freshness(log_file, threshold_minutes=10)
        assert is_fresh is False
        assert age is not None
        assert age >= 14  # 約15分

    def test_threshold_is_reasonable(self) -> None:
        """閾値が妥当な範囲内."""
        assert THRESHOLD_LOG_FRESHNESS_MINUTES >= 5
        assert THRESHOLD_LOG_FRESHNESS_MINUTES <= 30


class TestCheckLogWritable:
    """Tests for check_log_writable function (Issue #1456)."""

    def test_writable_directory(self, tmp_path: Path) -> None:
        """書き込み可能なディレクトリでTrueを返す."""
        is_writable, error = check_log_writable(tmp_path)
        assert is_writable is True
        assert error is None

    def test_writable_file(self, tmp_path: Path) -> None:
        """書き込み可能なファイルでTrueを返す."""
        test_file = tmp_path / "test.log"
        test_file.write_text("test")
        is_writable, error = check_log_writable(test_file)
        assert is_writable is True
        assert error is None

    def test_nonexistent_file_with_writable_parent(self, tmp_path: Path) -> None:
        """存在しないファイルだが親ディレクトリが書き込み可能な場合Trueを返す."""
        test_file = tmp_path / "nonexistent.log"
        is_writable, error = check_log_writable(test_file)
        assert is_writable is True
        assert error is None

    def test_nonexistent_parent_directory(self, tmp_path: Path) -> None:
        """親ディレクトリが存在しない場合Falseを返す."""
        test_file = tmp_path / "nonexistent_dir" / "test.log"
        is_writable, error = check_log_writable(test_file)
        assert is_writable is False
        assert error is not None
        assert "存在しません" in error

    def test_readonly_file(self, tmp_path: Path) -> None:
        """読み取り専用ファイルでFalseを返す."""
        test_file = tmp_path / "readonly.log"
        test_file.write_text("test")
        test_file.chmod(0o444)  # Read-only
        try:
            is_writable, error = check_log_writable(test_file)
            assert is_writable is False
            assert error is not None
            assert "書き込み権限" in error
        finally:
            test_file.chmod(0o644)  # Restore permissions for cleanup

    def test_readonly_directory(self, tmp_path: Path) -> None:
        """読み取り専用ディレクトリでFalseを返す（レビュー指摘対応）."""
        readonly_dir = tmp_path / "readonly_dir"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o555)  # Read + execute only, no write
        try:
            is_writable, error = check_log_writable(readonly_dir)
            assert is_writable is False
            assert error is not None
            assert "書き込み" in error
        finally:
            readonly_dir.chmod(0o755)  # Restore permissions for cleanup


class TestCheckDiskSpace:
    """Tests for check_disk_space function (Issue #1456)."""

    def test_sufficient_disk_space(self, tmp_path: Path) -> None:
        """十分なディスク容量がある場合Trueを返す."""
        is_sufficient, error, free_mb = check_disk_space(tmp_path)
        assert is_sufficient is True
        assert error is None
        assert free_mb > 0

    def test_returns_free_mb(self, tmp_path: Path) -> None:
        """空き容量をMBで返す."""
        _, _, free_mb = check_disk_space(tmp_path)
        assert isinstance(free_mb, int)
        assert free_mb >= 0

    def test_file_path_checks_parent_directory(self, tmp_path: Path) -> None:
        """ファイルパスが渡された場合、親ディレクトリをチェックする."""
        test_file = tmp_path / "test.log"
        is_sufficient, _, free_mb = check_disk_space(test_file)
        # ファイルが存在しなくても親ディレクトリでチェックされる
        assert is_sufficient is True
        assert free_mb > 0

    def test_threshold_value_is_reasonable(self) -> None:
        """ディスク容量閾値が妥当な値である."""
        assert THRESHOLD_MIN_DISK_SPACE_MB == 100
        assert THRESHOLD_MIN_DISK_SPACE_MB > 0


class TestCheckLogHealth:
    """Tests for check_log_health function.

    Issue #2068: セッション毎ファイル形式に対応。
    """

    def _create_session_log(self, log_dir: Path, session_id: str, entry_count: int) -> Path:
        """セッション固有ログファイルを作成するヘルパー."""
        log_file = log_dir / f"hook-execution-{session_id}.jsonl"
        entries = [{"hook": f"hook{i}"} for i in range(entry_count)]
        log_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        return log_file

    def test_no_issues_for_healthy_session(self, tmp_path: Path) -> None:
        """正常なセッションでは問題なし."""
        session_id = "healthy-session"
        metrics_file = tmp_path / "session-metrics.log"
        metrics_data = {
            "session_id": session_id,
            "hook_executions": 100,
            "blocks": 5,
            "approves": 95,
        }
        metrics_file.write_text(json.dumps(metrics_data) + "\n")
        # log_entry_countとの乖離がないようにエントリを作成
        self._create_session_log(tmp_path, session_id, 100)

        with (
            patch("log_health_check.METRICS_LOG_DIR", tmp_path),
            patch("log_health_check.EXECUTION_LOG_DIR", tmp_path),
        ):
            issues = check_log_health(session_id)
            # 正常なセッションでは一切の問題が検出されないことを確認
            assert not issues, f"Healthy session should have no issues, but got: {issues}"

    def test_error_for_all_zero_metrics(self, tmp_path: Path) -> None:
        """メトリクス全ゼロでERROR."""
        session_id = "zero-session"
        metrics_file = tmp_path / "session-metrics.log"
        metrics_data = {
            "session_id": session_id,
            "hook_executions": 0,
            "blocks": 0,
            "approves": 0,
        }
        metrics_file.write_text(json.dumps(metrics_data) + "\n")
        # セッションログなし

        with (
            patch("log_health_check.METRICS_LOG_DIR", tmp_path),
            patch("log_health_check.EXECUTION_LOG_DIR", tmp_path),
        ):
            issues = check_log_health(session_id)
            assert len(issues) == 1
            assert issues[0]["level"] == "ERROR"
            assert "全てゼロ" in issues[0]["message"]

    def test_warning_for_low_hook_executions(self, tmp_path: Path) -> None:
        """フック実行回数が少なすぎる場合WARNING."""
        session_id = "short-session"
        metrics_file = tmp_path / "session-metrics.log"
        metrics_data = {
            "session_id": session_id,
            "hook_executions": 3,  # < THRESHOLD_MIN_HOOK_EXECUTIONS
            "blocks": 0,
            "approves": 3,
        }
        metrics_file.write_text(json.dumps(metrics_data) + "\n")
        # log_entry_countとの乖離がないようにエントリを作成
        self._create_session_log(tmp_path, session_id, 3)

        with (
            patch("log_health_check.METRICS_LOG_DIR", tmp_path),
            patch("log_health_check.EXECUTION_LOG_DIR", tmp_path),
        ):
            issues = check_log_health(session_id)
            assert len(issues) == 1
            assert issues[0]["level"] == "WARNING"
            assert "少なすぎます" in issues[0]["message"]

    def test_info_for_high_hook_executions(self, tmp_path: Path) -> None:
        """フック実行回数が多い場合INFO."""
        session_id = "long-session"
        metrics_file = tmp_path / "session-metrics.log"
        metrics_data = {
            "session_id": session_id,
            "hook_executions": 600,  # > THRESHOLD_MAX_HOOK_EXECUTIONS
            "blocks": 10,
            "approves": 590,
        }
        metrics_file.write_text(json.dumps(metrics_data) + "\n")
        # log_entry_countとの乖離がないようにエントリを作成
        self._create_session_log(tmp_path, session_id, 600)

        with (
            patch("log_health_check.METRICS_LOG_DIR", tmp_path),
            patch("log_health_check.EXECUTION_LOG_DIR", tmp_path),
        ):
            issues = check_log_health(session_id)
            assert len(issues) == 1
            assert issues[0]["level"] == "INFO"
            assert "長時間セッション" in issues[0]["message"]

    def test_warning_when_no_log_entries(self, tmp_path: Path) -> None:
        """ログエントリがない場合WARNING."""
        session_id = "no-log-session"
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()
        execution_dir = tmp_path / "execution"
        execution_dir.mkdir()
        # セッションログファイルを作成しない

        with (
            patch("log_health_check.METRICS_LOG_DIR", metrics_dir),
            patch("log_health_check.EXECUTION_LOG_DIR", execution_dir),
        ):
            issues = check_log_health(session_id)
            assert len(issues) == 1
            assert issues[0]["level"] == "WARNING"
            assert "エントリがありません" in issues[0]["message"]

    def test_warning_for_metrics_log_discrepancy(self, tmp_path: Path) -> None:
        """メトリクスとログエントリ数の乖離でWARNING."""
        session_id = "discrepancy-session"
        metrics_file = tmp_path / "session-metrics.log"

        # メトリクス: 100回
        metrics_data = {
            "session_id": session_id,
            "hook_executions": 100,
            "blocks": 5,
            "approves": 95,
        }
        metrics_file.write_text(json.dumps(metrics_data) + "\n")

        # ログエントリ: 10件（大きな乖離）
        self._create_session_log(tmp_path, session_id, 10)

        with (
            patch("log_health_check.METRICS_LOG_DIR", tmp_path),
            patch("log_health_check.EXECUTION_LOG_DIR", tmp_path),
        ):
            issues = check_log_health(session_id)
            assert len(issues) == 1
            assert issues[0]["level"] == "WARNING"
            assert "乖離" in issues[0]["message"]
            assert issues[0]["details"]["metrics_hook_executions"] == 100
            assert issues[0]["details"]["log_entry_count"] == 10

    def test_no_warning_for_small_discrepancy(self, tmp_path: Path) -> None:
        """小さな乖離では警告しない."""
        session_id = "small-discrepancy-session"
        metrics_file = tmp_path / "session-metrics.log"

        # メトリクス: 100回
        metrics_data = {
            "session_id": session_id,
            "hook_executions": 100,
            "blocks": 5,
            "approves": 95,
        }
        metrics_file.write_text(json.dumps(metrics_data) + "\n")

        # ログエントリ: 80件（許容範囲内の乖離 20%）
        self._create_session_log(tmp_path, session_id, 80)

        with (
            patch("log_health_check.METRICS_LOG_DIR", tmp_path),
            patch("log_health_check.EXECUTION_LOG_DIR", tmp_path),
        ):
            issues = check_log_health(session_id)
            assert not issues, f"Small discrepancy should not trigger warning: {issues}"

    def test_warning_for_stale_session_log(self, tmp_path: Path) -> None:
        """古いセッションログでWARNING (Issue #2068)."""
        session_id = "stale-hook-log-session"
        metrics_file = tmp_path / "session-metrics.log"

        # 正常なメトリクス
        metrics_data = {
            "session_id": session_id,
            "hook_executions": 100,
            "blocks": 5,
            "approves": 95,
        }
        metrics_file.write_text(json.dumps(metrics_data) + "\n")

        # 正常なログエントリ数
        session_log = self._create_session_log(tmp_path, session_id, 100)

        # セッションログを15分前に設定（閾値は10分）
        old_time = time.time() - (15 * 60)
        os.utime(session_log, (old_time, old_time))

        with (
            patch("log_health_check.METRICS_LOG_DIR", tmp_path),
            patch("log_health_check.EXECUTION_LOG_DIR", tmp_path),
        ):
            issues = check_log_health(session_id)
            assert len(issues) == 1
            assert issues[0]["level"] == "WARNING"
            assert "セッションログ" in issues[0]["message"]
            assert "更新が古い" in issues[0]["message"]

    def test_warning_for_stale_session_metrics_log(self, tmp_path: Path) -> None:
        """古いsession-metrics.logでWARNING (Issue #1488)."""
        session_id = "stale-metrics-log-session"
        metrics_file = tmp_path / "session-metrics.log"

        # 正常なメトリクス
        metrics_data = {
            "session_id": session_id,
            "hook_executions": 100,
            "blocks": 5,
            "approves": 95,
        }
        metrics_file.write_text(json.dumps(metrics_data) + "\n")

        # 正常なログエントリ数
        self._create_session_log(tmp_path, session_id, 100)

        # session-metrics.logを15分前に設定（閾値は10分）
        old_time = time.time() - (15 * 60)
        os.utime(metrics_file, (old_time, old_time))

        with (
            patch("log_health_check.METRICS_LOG_DIR", tmp_path),
            patch("log_health_check.EXECUTION_LOG_DIR", tmp_path),
        ):
            issues = check_log_health(session_id)
            assert len(issues) == 1
            assert issues[0]["level"] == "WARNING"
            assert "session-metrics.log" in issues[0]["message"]
            assert "更新が古い" in issues[0]["message"]

    def test_warning_for_both_stale_logs(self, tmp_path: Path) -> None:
        """両方のログファイルが古い場合、2つのWARNING (Issue #2068)."""
        session_id = "both-stale-logs-session"
        metrics_file = tmp_path / "session-metrics.log"

        # 正常なメトリクス
        metrics_data = {
            "session_id": session_id,
            "hook_executions": 100,
            "blocks": 5,
            "approves": 95,
        }
        metrics_file.write_text(json.dumps(metrics_data) + "\n")

        # 正常なログエントリ数
        session_log = self._create_session_log(tmp_path, session_id, 100)

        # 両方のファイルを15分前に設定（閾値は10分）
        old_time = time.time() - (15 * 60)
        os.utime(session_log, (old_time, old_time))
        os.utime(metrics_file, (old_time, old_time))

        with (
            patch("log_health_check.METRICS_LOG_DIR", tmp_path),
            patch("log_health_check.EXECUTION_LOG_DIR", tmp_path),
        ):
            issues = check_log_health(session_id)
            assert len(issues) == 2
            # 両方ともWARNING
            assert all(issue["level"] == "WARNING" for issue in issues)
            # セッションログとsession-metrics.logの両方がメッセージに含まれる
            messages = [issue["message"] for issue in issues]
            assert any("セッションログ" in msg for msg in messages)
            assert any("session-metrics.log" in msg for msg in messages)


class TestFormatHealthReport:
    """Tests for format_health_report function."""

    def test_empty_report_for_no_issues(self) -> None:
        """問題なしの場合は空文字を返す."""
        result = format_health_report([])
        assert result == ""

    def test_formats_error_with_icon(self) -> None:
        """ERRORは❌アイコンでフォーマット."""
        issues = [
            {
                "level": "ERROR",
                "message": "Test error",
                "details": {"possible_cause": "Test cause"},
            }
        ]
        result = format_health_report(issues)
        assert "❌" in result
        assert "[ERROR]" in result
        assert "Test error" in result
        assert "Test cause" in result

    def test_formats_warning_with_icon(self) -> None:
        """WARNINGは⚠️アイコンでフォーマット."""
        issues = [{"level": "WARNING", "message": "Test warning", "details": {}}]
        result = format_health_report(issues)
        assert "⚠️" in result
        assert "[WARNING]" in result
        assert "Test warning" in result

    def test_formats_info_with_icon(self) -> None:
        """INFOはℹ️アイコンでフォーマット."""
        issues = [{"level": "INFO", "message": "Test info", "details": {}}]
        result = format_health_report(issues)
        assert "ℹ️" in result
        assert "[INFO]" in result
        assert "Test info" in result


class TestThresholds:
    """Tests for threshold values."""

    def test_min_threshold_is_reasonable(self) -> None:
        """最小閾値が妥当な値である."""
        assert THRESHOLD_MIN_HOOK_EXECUTIONS == 5
        assert THRESHOLD_MIN_HOOK_EXECUTIONS > 0

    def test_max_threshold_is_reasonable(self) -> None:
        """最大閾値が妥当な値である."""
        assert THRESHOLD_MAX_HOOK_EXECUTIONS == 500
        assert THRESHOLD_MAX_HOOK_EXECUTIONS > THRESHOLD_MIN_HOOK_EXECUTIONS

    def test_discrepancy_threshold_is_reasonable(self) -> None:
        """乖離率閾値が妥当な値である."""
        assert THRESHOLD_METRICS_LOG_DISCREPANCY_RATIO == 0.5
        assert 0 < THRESHOLD_METRICS_LOG_DISCREPANCY_RATIO < 1


class TestMain:
    """Tests for main function."""

    def test_approves_when_stop_hook_active(self, capsys: object) -> None:
        """stop_hook_activeがtrueの場合はapproveを返す."""
        with patch("log_health_check.parse_hook_input") as mock_parse:
            mock_parse.return_value = {"stop_hook_active": True}
            main()

        captured = capsys.readouterr()  # type: ignore[attr-defined]
        result = json.loads(captured.out)
        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_uses_session_id_from_input(self, tmp_path: Path, capsys: object) -> None:
        """hook入力のsession_idを使用する."""
        session_id = "input-session-id"
        metrics_file = tmp_path / "session-metrics.log"
        metrics_data = {
            "session_id": session_id,
            "hook_executions": 100,
            "blocks": 5,
            "approves": 95,
        }
        metrics_file.write_text(json.dumps(metrics_data) + "\n")

        with (
            patch("log_health_check.parse_hook_input") as mock_parse,
            patch("log_health_check.METRICS_LOG_DIR", tmp_path),
            patch("log_health_check.log_hook_execution"),
        ):
            mock_parse.return_value = {"session_id": session_id}
            main()

        captured = capsys.readouterr()  # type: ignore[attr-defined]
        result = json.loads(captured.out)
        assert result["decision"] == "approve"

    def test_falls_back_to_claude_session_id(self, tmp_path: Path, capsys: object) -> None:
        """session_idがない場合はHookContextのフォールバックを使用."""
        session_id = "claude-session-id"
        metrics_file = tmp_path / "session-metrics.log"
        metrics_data = {
            "session_id": session_id,
            "hook_executions": 100,
            "blocks": 5,
            "approves": 95,
        }
        metrics_file.write_text(json.dumps(metrics_data) + "\n")

        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = session_id
        with (
            patch("log_health_check.parse_hook_input") as mock_parse,
            patch("log_health_check.create_hook_context", return_value=mock_ctx),
            patch("log_health_check.METRICS_LOG_DIR", tmp_path),
            patch("log_health_check.log_hook_execution"),
        ):
            mock_parse.return_value = {}
            main()

        captured = capsys.readouterr()  # type: ignore[attr-defined]
        result = json.loads(captured.out)
        assert result["decision"] == "approve"

    def test_includes_system_message_when_issues_found(
        self, tmp_path: Path, capsys: object
    ) -> None:
        """問題がある場合はsystemMessageを含める."""
        session_id = "problematic-session"
        metrics_file = tmp_path / "session-metrics.log"
        # メトリクス全ゼロでERRORを発生させる
        metrics_data = {
            "session_id": session_id,
            "hook_executions": 0,
            "blocks": 0,
            "approves": 0,
        }
        metrics_file.write_text(json.dumps(metrics_data) + "\n")

        with (
            patch("log_health_check.parse_hook_input") as mock_parse,
            patch("log_health_check.METRICS_LOG_DIR", tmp_path),
            patch("log_health_check.log_hook_execution"),
        ):
            mock_parse.return_value = {"session_id": session_id}
            main()

        captured = capsys.readouterr()  # type: ignore[attr-defined]
        result = json.loads(captured.out)
        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "全てゼロ" in result["systemMessage"]

    def test_no_system_message_when_healthy(self, tmp_path: Path, capsys: object) -> None:
        """正常な場合はsystemMessageを含めない."""
        session_id = "healthy-session"
        metrics_file = tmp_path / "session-metrics.log"
        # セッション固有ログファイル
        session_log = tmp_path / f"hook-execution-{session_id}.jsonl"
        metrics_data = {
            "session_id": session_id,
            "hook_executions": 100,
            "blocks": 5,
            "approves": 95,
        }
        metrics_file.write_text(json.dumps(metrics_data) + "\n")
        # log_entry_countとの乖離がないようにエントリを作成
        entries = [{"hook": f"hook{i}"} for i in range(100)]
        session_log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        with (
            patch("log_health_check.parse_hook_input") as mock_parse,
            patch("log_health_check.METRICS_LOG_DIR", tmp_path),
            patch("log_health_check.EXECUTION_LOG_DIR", tmp_path),
            patch("log_health_check.log_hook_execution"),
        ):
            mock_parse.return_value = {"session_id": session_id}
            main()

        captured = capsys.readouterr()  # type: ignore[attr-defined]
        result = json.loads(captured.out)
        assert result["decision"] == "approve"
        assert "systemMessage" not in result
