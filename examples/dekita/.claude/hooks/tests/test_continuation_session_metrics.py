#!/usr/bin/env python3
"""continuation-session-metrics.py のテスト。"""

import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


# Import module with hyphen in name using importlib
spec = importlib.util.spec_from_file_location(
    "continuation_session_metrics",
    Path(__file__).parent.parent / "continuation-session-metrics.py",
)
continuation_session_metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(continuation_session_metrics)


class TestIsContinuationSession:
    """is_continuation_session関数のテスト。"""

    def test_returns_false_when_handoff_state_not_exists(self, tmp_path):
        """handoff-state.jsonが存在しない場合Falseを返す。"""
        with patch.object(continuation_session_metrics, "HOOKS_DIR", tmp_path / "hooks"):
            result = continuation_session_metrics.is_continuation_session()
            assert result is False

    def test_returns_true_when_handoff_state_recent(self, tmp_path):
        """handoff-state.jsonが最近更新されている場合Trueを返す。"""
        # HOOKS_DIR.parent / "state" にhandoff-state.jsonを作成
        # HOOKS_DIRを tmp_path / "hooks" に設定すると、
        # HOOKS_DIR.parent = tmp_path になる
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir(parents=True)
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True)
        handoff_file = state_dir / "handoff-state.json"
        handoff_file.write_text("{}")

        with patch.object(continuation_session_metrics, "HOOKS_DIR", hooks_dir):
            result = continuation_session_metrics.is_continuation_session()
            assert result is True

    def test_returns_false_when_handoff_state_old(self, tmp_path):
        """handoff-state.jsonが古い場合Falseを返す。"""
        import os

        # HOOKS_DIR.parent / "state" にhandoff-state.jsonを作成
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir(parents=True)
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True)
        handoff_file = state_dir / "handoff-state.json"
        handoff_file.write_text("{}")

        # Set mtime to 10 minutes ago (older than CONTINUATION_WINDOW_MINUTES)
        old_time = datetime.now(UTC) - timedelta(minutes=10)
        os.utime(handoff_file, (old_time.timestamp(), old_time.timestamp()))

        with patch.object(continuation_session_metrics, "HOOKS_DIR", hooks_dir):
            result = continuation_session_metrics.is_continuation_session()
            assert result is False

    def test_returns_false_on_file_access_error(self, tmp_path):
        """ファイルアクセスエラーの場合Falseを返す。"""
        with (
            patch.object(continuation_session_metrics, "HOOKS_DIR", tmp_path),
            patch.object(Path, "exists", side_effect=OSError("Permission denied")),
        ):
            result = continuation_session_metrics.is_continuation_session()
            assert result is False


class TestGetRecordedSessionIds:
    """get_recorded_session_ids関数のテスト。"""

    def test_returns_empty_set_when_log_not_exists(self, tmp_path):
        """ログファイルが存在しない場合、空セットを返す。"""
        with patch.object(
            continuation_session_metrics,
            "SESSION_METRICS_LOG",
            tmp_path / "nonexistent.log",
        ):
            result = continuation_session_metrics.get_recorded_session_ids()
            assert result == set()

    def test_returns_session_ids_from_log(self, tmp_path):
        """ログファイルからセッションIDを取得する。"""
        log_file = tmp_path / "session-metrics.log"
        log_file.write_text(
            '{"session_id": "session-1", "type": "session_end"}\n'
            '{"session_id": "session-2", "type": "session_end"}\n'
        )

        with patch.object(continuation_session_metrics, "SESSION_METRICS_LOG", log_file):
            result = continuation_session_metrics.get_recorded_session_ids()
            assert result == {"session-1", "session-2"}

    def test_excludes_session_continuation_type(self, tmp_path):
        """type: session_continuationのエントリを除外する。"""
        log_file = tmp_path / "session-metrics.log"
        log_file.write_text(
            '{"session_id": "session-1", "type": "session_end"}\n'
            '{"session_id": "session-2", "type": "session_continuation"}\n'
            '{"session_id": "session-3", "type": "session_end"}\n'
        )

        with patch.object(continuation_session_metrics, "SESSION_METRICS_LOG", log_file):
            result = continuation_session_metrics.get_recorded_session_ids()
            assert result == {"session-1", "session-3"}
            assert "session-2" not in result

    def test_handles_invalid_json_lines(self, tmp_path):
        """無効なJSON行をスキップする。"""
        log_file = tmp_path / "session-metrics.log"
        log_file.write_text(
            '{"session_id": "session-1", "type": "session_end"}\n'
            "invalid json line\n"
            '{"session_id": "session-3", "type": "session_end"}\n'
        )

        with patch.object(continuation_session_metrics, "SESSION_METRICS_LOG", log_file):
            result = continuation_session_metrics.get_recorded_session_ids()
            assert result == {"session-1", "session-3"}


class TestGetLastRecordedSessionId:
    """get_last_recorded_session_id関数のテスト。"""

    def test_returns_none_when_log_not_exists(self, tmp_path):
        """ログファイルが存在しない場合Noneを返す。"""
        with patch.object(
            continuation_session_metrics,
            "SESSION_METRICS_LOG",
            tmp_path / "nonexistent.log",
        ):
            result = continuation_session_metrics.get_last_recorded_session_id()
            assert result is None

    def test_returns_last_session_id(self, tmp_path):
        """最後のセッションIDを返す。"""
        log_file = tmp_path / "session-metrics.log"
        log_file.write_text(
            '{"session_id": "session-1", "type": "session_end"}\n'
            '{"session_id": "session-2", "type": "session_end"}\n'
            '{"session_id": "session-3", "type": "session_end"}\n'
        )

        with patch.object(continuation_session_metrics, "SESSION_METRICS_LOG", log_file):
            result = continuation_session_metrics.get_last_recorded_session_id()
            assert result == "session-3"

    def test_excludes_session_continuation_type(self, tmp_path):
        """type: session_continuationを除外して最後のメトリクスを返す。"""
        log_file = tmp_path / "session-metrics.log"
        log_file.write_text(
            '{"session_id": "session-1", "type": "session_end"}\n'
            '{"session_id": "session-2", "type": "session_end"}\n'
            '{"session_id": "session-3", "type": "session_continuation"}\n'
        )

        with patch.object(continuation_session_metrics, "SESSION_METRICS_LOG", log_file):
            result = continuation_session_metrics.get_last_recorded_session_id()
            # session-3はcontinuationなので除外、session-2が最後
            assert result == "session-2"


class TestGetSessionIdsFromHookLog:
    """get_session_ids_from_hook_log関数のテスト。"""

    def test_returns_empty_list_when_log_not_exists(self, tmp_path):
        """ログファイルが存在しない場合、空リストを返す。"""
        with patch.object(
            continuation_session_metrics, "read_all_session_log_entries", return_value=[]
        ):
            result = continuation_session_metrics.get_session_ids_from_hook_log()
            assert result == []

    def test_returns_session_ids_sorted_by_recency(self, tmp_path):
        """セッションIDを最新順にソートして返す。"""
        now = datetime.now(UTC)
        mock_entries = [
            {"session_id": "session-1", "timestamp": (now - timedelta(hours=2)).isoformat()},
            {"session_id": "session-2", "timestamp": (now - timedelta(hours=1)).isoformat()},
            {"session_id": "session-3", "timestamp": now.isoformat()},
        ]

        with patch.object(
            continuation_session_metrics, "read_all_session_log_entries", return_value=mock_entries
        ):
            result = continuation_session_metrics.get_session_ids_from_hook_log(hours=24)
            # 最新順: session-3, session-2, session-1
            assert result == ["session-3", "session-2", "session-1"]

    def test_excludes_old_entries(self, tmp_path):
        """指定時間より古いエントリを除外する。"""
        now = datetime.now(UTC)
        mock_entries = [
            {"session_id": "old-session", "timestamp": (now - timedelta(hours=48)).isoformat()},
            {"session_id": "recent-session", "timestamp": now.isoformat()},
        ]

        with patch.object(
            continuation_session_metrics, "read_all_session_log_entries", return_value=mock_entries
        ):
            result = continuation_session_metrics.get_session_ids_from_hook_log(hours=24)
            assert result == ["recent-session"]
            assert "old-session" not in result

    def test_handles_duplicate_session_ids(self, tmp_path):
        """同じセッションIDが複数回出現する場合、最新のタイムスタンプを使用。"""
        now = datetime.now(UTC)
        mock_entries = [
            {"session_id": "session-1", "timestamp": (now - timedelta(hours=2)).isoformat()},
            {"session_id": "session-1", "timestamp": now.isoformat()},
        ]

        with patch.object(
            continuation_session_metrics, "read_all_session_log_entries", return_value=mock_entries
        ):
            result = continuation_session_metrics.get_session_ids_from_hook_log(hours=24)
            # 重複は排除され、1つだけ返される
            assert result == ["session-1"]


class TestCollectMetricsForSession:
    """collect_metrics_for_session関数のテスト。"""

    def test_returns_false_when_script_not_exists(self, tmp_path):
        """スクリプトが存在しない場合Falseを返す。"""
        with patch.object(continuation_session_metrics, "SCRIPT_DIR", tmp_path):
            result = continuation_session_metrics.collect_metrics_for_session("test-session")
            assert result is False

    def test_returns_true_on_success(self, tmp_path):
        """スクリプト実行成功時にTrueを返す。"""
        script = tmp_path / "collect-session-metrics.py"
        script.write_text("# dummy script")

        with (
            patch.object(continuation_session_metrics, "SCRIPT_DIR", tmp_path),
            patch.object(continuation_session_metrics, "subprocess") as mock_subprocess,
        ):
            mock_subprocess.run.return_value = MagicMock(returncode=0)
            mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired

            result = continuation_session_metrics.collect_metrics_for_session("test-session")
            assert result is True

    def test_passes_session_id_via_env(self, tmp_path):
        """セッションIDが環境変数で渡されることを確認。"""
        script = tmp_path / "collect-session-metrics.py"
        script.write_text("# dummy script")

        with (
            patch.object(continuation_session_metrics, "SCRIPT_DIR", tmp_path),
            patch.object(continuation_session_metrics, "subprocess") as mock_subprocess,
        ):
            mock_subprocess.run.return_value = MagicMock(returncode=0)
            mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired

            continuation_session_metrics.collect_metrics_for_session("my-session-id")

            # Issue #2317: コマンドライン引数に--session-idが含まれていることを確認
            call_args = mock_subprocess.run.call_args[0][0]  # 位置引数のリスト
            assert "--session-id" in call_args
            session_id_index = call_args.index("--session-id")
            assert call_args[session_id_index + 1] == "my-session-id"

    def test_returns_false_on_timeout(self, tmp_path):
        """タイムアウト時にFalseを返す。"""
        script = tmp_path / "collect-session-metrics.py"
        script.write_text("# dummy script")

        with (
            patch.object(continuation_session_metrics, "SCRIPT_DIR", tmp_path),
            patch.object(continuation_session_metrics, "subprocess") as mock_subprocess,
        ):
            mock_subprocess.run.side_effect = subprocess.TimeoutExpired("cmd", 30)
            mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired

            result = continuation_session_metrics.collect_metrics_for_session("test-session")
            assert result is False


class TestRecordContinuationMarker:
    """record_continuation_marker関数のテスト。"""

    def test_creates_log_directory(self, tmp_path):
        """ログディレクトリを作成する。"""
        log_dir = tmp_path / "metrics"
        log_file = log_dir / "session-metrics.log"

        with (
            patch.object(continuation_session_metrics, "METRICS_LOG_DIR", log_dir),
            patch.object(continuation_session_metrics, "SESSION_METRICS_LOG", log_file),
        ):
            continuation_session_metrics.record_continuation_marker(
                "current-session", "previous-session"
            )

            assert log_dir.exists()

    def test_writes_continuation_marker(self, tmp_path):
        """継続マーカーをログに書き込む。"""
        log_dir = tmp_path / "metrics"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "session-metrics.log"

        with (
            patch.object(continuation_session_metrics, "METRICS_LOG_DIR", log_dir),
            patch.object(continuation_session_metrics, "SESSION_METRICS_LOG", log_file),
        ):
            continuation_session_metrics.record_continuation_marker(
                "current-session", "previous-session"
            )

            content = log_file.read_text()
            entry = json.loads(content.strip())
            assert entry["session_id"] == "current-session"
            assert entry["type"] == "session_continuation"
            assert entry["previous_session_id"] == "previous-session"


class TestMain:
    """main関数のテスト。"""

    def test_normal_session_does_nothing(self, capsys):
        """通常セッションの場合、何もせずapproveを出力。"""
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "session-1"
        with (
            patch.object(continuation_session_metrics, "parse_hook_input", return_value={}),
            patch.object(
                continuation_session_metrics, "create_hook_context", return_value=mock_ctx
            ),
            patch.object(
                continuation_session_metrics, "is_continuation_session", return_value=False
            ),
            patch.object(continuation_session_metrics, "log_hook_execution"),
        ):
            continuation_session_metrics.main()

            captured = capsys.readouterr()
            output = json.loads(captured.out.strip())
            assert output == {"continue": True}

    def test_continuation_session_collects_metrics(self, capsys, tmp_path):
        """継続セッションの場合、未記録セッションのメトリクスを収集。"""
        log_file = tmp_path / "session-metrics.log"

        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "current-session"
        with (
            patch.object(continuation_session_metrics, "parse_hook_input", return_value={}),
            patch.object(
                continuation_session_metrics,
                "create_hook_context",
                return_value=mock_ctx,
            ),
            patch.object(
                continuation_session_metrics, "is_continuation_session", return_value=True
            ),
            patch.object(
                continuation_session_metrics,
                "get_last_recorded_session_id",
                return_value="old-session",
            ),
            patch.object(
                continuation_session_metrics,
                "get_recorded_session_ids",
                return_value={"old-session"},
            ),
            patch.object(
                continuation_session_metrics,
                "get_session_ids_from_hook_log",
                return_value=["unrecorded-1", "unrecorded-2", "old-session"],
            ),
            patch.object(
                continuation_session_metrics, "collect_metrics_for_session", return_value=True
            ) as mock_collect,
            patch.object(continuation_session_metrics, "METRICS_LOG_DIR", tmp_path),
            patch.object(continuation_session_metrics, "SESSION_METRICS_LOG", log_file),
            patch.object(continuation_session_metrics, "log_hook_execution"),
        ):
            continuation_session_metrics.main()

            # 未記録セッション（unrecorded-1, unrecorded-2）のメトリクスを収集
            # current-sessionとold-sessionは除外
            assert mock_collect.call_count == 2
            mock_collect.assert_any_call("unrecorded-1")
            mock_collect.assert_any_call("unrecorded-2")

            captured = capsys.readouterr()
            output = json.loads(captured.out.strip())
            # Issue #2006: 継続セッションではメッセージが含まれるようになった
            assert output["continue"] is True
            assert "message" in output

    def test_continuation_session_limits_collection(self, capsys, tmp_path):
        """継続セッションの場合、MAX_SESSIONS_TO_COLLECT以上は収集しない。"""
        log_file = tmp_path / "session-metrics.log"

        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "current-session"
        with (
            patch.object(continuation_session_metrics, "parse_hook_input", return_value={}),
            patch.object(
                continuation_session_metrics,
                "create_hook_context",
                return_value=mock_ctx,
            ),
            patch.object(
                continuation_session_metrics, "is_continuation_session", return_value=True
            ),
            patch.object(
                continuation_session_metrics, "get_last_recorded_session_id", return_value=None
            ),
            patch.object(
                continuation_session_metrics, "get_recorded_session_ids", return_value=set()
            ),
            patch.object(
                continuation_session_metrics,
                "get_session_ids_from_hook_log",
                return_value=["s1", "s2", "s3", "s4", "s5"],  # 5 sessions
            ),
            patch.object(
                continuation_session_metrics, "collect_metrics_for_session", return_value=True
            ) as mock_collect,
            patch.object(continuation_session_metrics, "METRICS_LOG_DIR", tmp_path),
            patch.object(continuation_session_metrics, "SESSION_METRICS_LOG", log_file),
            patch.object(continuation_session_metrics, "log_hook_execution"),
        ):
            continuation_session_metrics.main()

            # MAX_SESSIONS_TO_COLLECT = 3 なので3つだけ収集
            # s1 = current-sessionなので除外
            assert mock_collect.call_count == 3

    def test_excludes_current_session_from_collection(self, capsys, tmp_path):
        """現在のセッションはメトリクス収集対象から除外する。"""
        log_file = tmp_path / "session-metrics.log"

        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "current-session"
        with (
            patch.object(continuation_session_metrics, "parse_hook_input", return_value={}),
            patch.object(
                continuation_session_metrics,
                "create_hook_context",
                return_value=mock_ctx,
            ),
            patch.object(
                continuation_session_metrics, "is_continuation_session", return_value=True
            ),
            patch.object(
                continuation_session_metrics, "get_last_recorded_session_id", return_value=None
            ),
            patch.object(
                continuation_session_metrics, "get_recorded_session_ids", return_value=set()
            ),
            patch.object(
                continuation_session_metrics,
                "get_session_ids_from_hook_log",
                return_value=["current-session", "other-session"],
            ),
            patch.object(
                continuation_session_metrics, "collect_metrics_for_session", return_value=True
            ) as mock_collect,
            patch.object(continuation_session_metrics, "METRICS_LOG_DIR", tmp_path),
            patch.object(continuation_session_metrics, "SESSION_METRICS_LOG", log_file),
            patch.object(continuation_session_metrics, "log_hook_execution"),
        ):
            continuation_session_metrics.main()

            # current-sessionは除外、other-sessionのみ収集
            assert mock_collect.call_count == 1
            mock_collect.assert_called_once_with("other-session")

    def test_continuation_marker_uses_collected_session_as_previous(self, capsys, tmp_path):
        """継続マーカーは収集したセッションのうち最新を前セッションとして使用する。

        Codex CLI review指摘: 収集前の値だとチェーンが不正確になる問題の修正確認。
        """
        log_file = tmp_path / "session-metrics.log"

        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "current-session"
        with (
            patch.object(continuation_session_metrics, "parse_hook_input", return_value={}),
            patch.object(
                continuation_session_metrics,
                "create_hook_context",
                return_value=mock_ctx,
            ),
            patch.object(
                continuation_session_metrics, "is_continuation_session", return_value=True
            ),
            patch.object(
                continuation_session_metrics, "get_recorded_session_ids", return_value=set()
            ),
            patch.object(
                continuation_session_metrics,
                "get_session_ids_from_hook_log",
                return_value=["prev-session-1", "prev-session-2"],
            ),
            patch.object(
                continuation_session_metrics, "collect_metrics_for_session", return_value=True
            ),
            patch.object(continuation_session_metrics, "record_continuation_marker") as mock_marker,
            patch.object(continuation_session_metrics, "METRICS_LOG_DIR", tmp_path),
            patch.object(continuation_session_metrics, "SESSION_METRICS_LOG", log_file),
            patch.object(continuation_session_metrics, "log_hook_execution"),
        ):
            continuation_session_metrics.main()

            # 収集したセッションのうち最初（= 最新）が前セッションとして使われる
            mock_marker.assert_called_once_with("current-session", "prev-session-1")

    def test_continuation_marker_falls_back_to_last_recorded(self, capsys, tmp_path):
        """収集がない場合、継続マーカーは最後の記録済みセッションを使用する。"""
        log_file = tmp_path / "session-metrics.log"

        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "current-session"
        with (
            patch.object(continuation_session_metrics, "parse_hook_input", return_value={}),
            patch.object(
                continuation_session_metrics,
                "create_hook_context",
                return_value=mock_ctx,
            ),
            patch.object(
                continuation_session_metrics, "is_continuation_session", return_value=True
            ),
            patch.object(
                continuation_session_metrics,
                "get_recorded_session_ids",
                return_value={"already-recorded"},
            ),
            patch.object(
                continuation_session_metrics,
                "get_session_ids_from_hook_log",
                return_value=["already-recorded"],  # 全て記録済み
            ),
            patch.object(
                continuation_session_metrics,
                "get_last_recorded_session_id",
                return_value="fallback-session",
            ),
            patch.object(continuation_session_metrics, "record_continuation_marker") as mock_marker,
            patch.object(continuation_session_metrics, "METRICS_LOG_DIR", tmp_path),
            patch.object(continuation_session_metrics, "SESSION_METRICS_LOG", log_file),
            patch.object(continuation_session_metrics, "log_hook_execution"),
        ):
            continuation_session_metrics.main()

            # 収集がないのでフォールバック
            mock_marker.assert_called_once_with("current-session", "fallback-session")


class TestGetHandoffSummary:
    """get_handoff_summary関数のテスト (Issue #1542)。

    Note: テストで hooks_dir = tmp_path / "hooks" と設定すると、
    HOOKS_DIR.parent = tmp_path となる。したがって、
    tmp_path / "handoff" と hooks_dir.parent / "handoff" は等価である。
    (Copilot誤検知対策: Issue #1560)
    """

    def test_returns_empty_dict_when_handoff_dir_not_exists(self, tmp_path):
        """handoffディレクトリが存在しない場合、空辞書を返す。"""
        # hooks_dir = tmp_path / "hooks" なので hooks_dir.parent = tmp_path
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir(parents=True)
        # handoffディレクトリは作成しない

        with patch.object(continuation_session_metrics, "HOOKS_DIR", hooks_dir):
            result = continuation_session_metrics.get_handoff_summary()
            assert result == {}

    def test_returns_empty_dict_when_no_handoff_files(self, tmp_path):
        """handoffファイルが存在しない場合、空辞書を返す。"""
        # hooks_dir.parent = tmp_path なので tmp_path / "handoff" は正しい
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir(parents=True)
        handoff_dir = tmp_path / "handoff"
        handoff_dir.mkdir(parents=True)
        # handoffファイルは作成しない

        with patch.object(continuation_session_metrics, "HOOKS_DIR", hooks_dir):
            result = continuation_session_metrics.get_handoff_summary()
            assert result == {}

    def test_returns_summary_from_valid_handoff_file(self, tmp_path):
        """有効なhandoffファイルが存在する場合、サマリー情報を返す。"""
        # hooks_dir.parent = tmp_path なので tmp_path / "handoff" は正しい
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir(parents=True)
        handoff_dir = tmp_path / "handoff"
        handoff_dir.mkdir(parents=True)

        handoff_data = {
            "work_status": "Implementing feature X",
            "next_action": "Run tests",
            "session_summary": {
                "blocks": 3,
                "block_reasons": ["rate limit", "CI failure", "review pending"],
            },
            "pending_tasks": [{"task": "task1"}, {"task": "task2"}],
            "open_prs": [{"number": 123}],
        }
        handoff_file = handoff_dir / "session-abc.json"
        handoff_file.write_text(json.dumps(handoff_data))

        with patch.object(continuation_session_metrics, "HOOKS_DIR", hooks_dir):
            result = continuation_session_metrics.get_handoff_summary()

            assert result["previous_work_status"] == "Implementing feature X"
            assert result["previous_next_action"] == "Run tests"
            assert result["previous_block_count"] == 3
            assert result["previous_block_reasons"] == [
                "rate limit",
                "CI failure",
                "review pending",
            ]
            assert result["pending_tasks_count"] == 2
            assert result["open_prs_count"] == 1

    def test_loads_specific_session_file_when_session_id_provided(self, tmp_path):
        """session_id指定時に対応するファイルをロードする。"""
        # hooks_dir.parent = tmp_path なので tmp_path / "handoff" は正しい
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir(parents=True)
        handoff_dir = tmp_path / "handoff"
        handoff_dir.mkdir(parents=True)

        # 古いファイル（session-old）
        old_data = {"work_status": "Old work"}
        old_file = handoff_dir / "session-old.json"
        old_file.write_text(json.dumps(old_data))

        # 新しいファイル（session-new）- 指定対象
        new_data = {"work_status": "New work", "next_action": "Deploy"}
        new_file = handoff_dir / "session-new.json"
        new_file.write_text(json.dumps(new_data))

        with patch.object(continuation_session_metrics, "HOOKS_DIR", hooks_dir):
            result = continuation_session_metrics.get_handoff_summary("session-new")

            assert result["previous_work_status"] == "New work"
            assert result["previous_next_action"] == "Deploy"

    def test_fallback_to_latest_when_session_file_not_exists(self, tmp_path):
        """session_id指定時にファイルが存在しない場合、最新ファイルにフォールバック。"""
        import os

        # hooks_dir.parent = tmp_path なので tmp_path / "handoff" は正しい
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir(parents=True)
        handoff_dir = tmp_path / "handoff"
        handoff_dir.mkdir(parents=True)

        # 古いファイル
        old_data = {"work_status": "Old work"}
        old_file = handoff_dir / "session-old.json"
        old_file.write_text(json.dumps(old_data))
        # Set old mtime
        old_mtime = datetime.now(UTC).timestamp() - 3600
        os.utime(old_file, (old_mtime, old_mtime))

        # 新しいファイル（最新）
        new_data = {"work_status": "Latest work"}
        new_file = handoff_dir / "session-latest.json"
        new_file.write_text(json.dumps(new_data))

        with patch.object(continuation_session_metrics, "HOOKS_DIR", hooks_dir):
            # 存在しないセッションIDを指定
            result = continuation_session_metrics.get_handoff_summary("nonexistent-session")

            # 最新ファイルにフォールバック
            assert result["previous_work_status"] == "Latest work"

    def test_returns_empty_dict_on_invalid_json(self, tmp_path):
        """無効なJSONファイルの場合、空辞書を返す。"""
        # hooks_dir.parent = tmp_path なので tmp_path / "handoff" は正しい
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir(parents=True)
        handoff_dir = tmp_path / "handoff"
        handoff_dir.mkdir(parents=True)

        # 無効なJSON
        invalid_file = handoff_dir / "session-invalid.json"
        invalid_file.write_text("not valid json {{{")

        with patch.object(continuation_session_metrics, "HOOKS_DIR", hooks_dir):
            result = continuation_session_metrics.get_handoff_summary()
            assert result == {}

    def test_returns_empty_dict_on_os_error(self, tmp_path):
        """OSErrorが発生する場合、空辞書を返す。"""
        # hooks_dir.parent = tmp_path なので tmp_path / "handoff" は正しい
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir(parents=True)
        handoff_dir = tmp_path / "handoff"
        handoff_dir.mkdir(parents=True)

        handoff_file = handoff_dir / "session-test.json"
        handoff_file.write_text('{"work_status": "test"}')

        with (
            patch.object(continuation_session_metrics, "HOOKS_DIR", hooks_dir),
            patch("builtins.open", side_effect=OSError("Permission denied")),
        ):
            result = continuation_session_metrics.get_handoff_summary()
            assert result == {}

    def test_excludes_none_values_from_result(self, tmp_path):
        """None値は結果から除外される。"""
        # hooks_dir.parent = tmp_path なので tmp_path / "handoff" は正しい
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir(parents=True)
        handoff_dir = tmp_path / "handoff"
        handoff_dir.mkdir(parents=True)

        # work_statusとnext_actionがない
        handoff_data = {
            "session_summary": {"blocks": 5},
            "pending_tasks": [],
            "open_prs": [],
        }
        handoff_file = handoff_dir / "session-partial.json"
        handoff_file.write_text(json.dumps(handoff_data))

        with patch.object(continuation_session_metrics, "HOOKS_DIR", hooks_dir):
            result = continuation_session_metrics.get_handoff_summary()

            # None値は除外される
            assert "previous_work_status" not in result
            assert "previous_next_action" not in result
            # 0やemptyリストは含まれる
            assert result["previous_block_count"] == 5
            assert result["previous_block_reasons"] == []
            assert result["pending_tasks_count"] == 0
            assert result["open_prs_count"] == 0

    def test_limits_block_reasons_to_three(self, tmp_path):
        """block_reasonsは最大3つまでに制限される。"""
        # hooks_dir.parent = tmp_path なので tmp_path / "handoff" は正しい
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir(parents=True)
        handoff_dir = tmp_path / "handoff"
        handoff_dir.mkdir(parents=True)

        handoff_data = {
            "session_summary": {
                "block_reasons": ["reason1", "reason2", "reason3", "reason4", "reason5"],
            },
        }
        handoff_file = handoff_dir / "session-many-reasons.json"
        handoff_file.write_text(json.dumps(handoff_data))

        with patch.object(continuation_session_metrics, "HOOKS_DIR", hooks_dir):
            result = continuation_session_metrics.get_handoff_summary()

            assert len(result["previous_block_reasons"]) == 3
            assert result["previous_block_reasons"] == ["reason1", "reason2", "reason3"]


class TestBuildDevelopmentFlowReminder:
    """build_development_flow_reminder関数のテスト (Issue #2006)。

    セッション継続時に開発フローの意識がリセットされる問題に対応するため、
    チェックリストを表示する機能をテスト。
    """

    def test_with_full_handoff_summary(self):
        """全ての情報がある場合のリマインダー生成。"""
        handoff_summary = {
            "previous_work_status": "Issue #123 の実装中",
            "previous_next_action": "テストを追加する",
            "previous_block_count": 2,
            "previous_block_reasons": ["codex-review-check", "planning-enforcement"],
            "pending_tasks_count": 3,
            "open_prs_count": 1,
        }

        result = continuation_session_metrics.build_development_flow_reminder(handoff_summary)

        assert "セッション継続 - 開発フローチェックリスト" in result
        assert "Issue #123 の実装中" in result
        assert "テストを追加する" in result
        assert "保留タスク: 3件" in result
        assert "オープンPR: 1件" in result
        assert "Issue作成前に調査・探索を実施したか" in result
        assert "Worktree作成前にプランを作成したか" in result
        assert "Push前にCodexレビューを実施したか" in result

    def test_with_empty_handoff_summary(self):
        """空のハンドオフサマリーの場合。"""
        handoff_summary = {}

        result = continuation_session_metrics.build_development_flow_reminder(handoff_summary)

        assert "セッション継続 - 開発フローチェックリスト" in result
        assert "前セッションの状態: 不明" in result
        # 保留タスクやオープンPRは表示されない
        assert "保留タスク" not in result
        assert "オープンPR" not in result
        # チェックリストは常に表示
        assert "Issue作成前に調査・探索を実施したか" in result

    def test_with_only_work_status(self):
        """作業状態のみの場合。"""
        handoff_summary = {
            "previous_work_status": "PR #456 をレビュー中",
        }

        result = continuation_session_metrics.build_development_flow_reminder(handoff_summary)

        assert "PR #456 をレビュー中" in result
        # next_actionがないので「次のアクション」行は表示されない
        assert "次のアクション" not in result

    def test_with_zero_counts(self):
        """カウントが0の場合は表示しない。"""
        handoff_summary = {
            "previous_work_status": "完了",
            "pending_tasks_count": 0,
            "open_prs_count": 0,
        }

        result = continuation_session_metrics.build_development_flow_reminder(handoff_summary)

        assert "保留タスク" not in result
        assert "オープンPR" not in result

    def test_contains_all_checklist_items(self):
        """チェックリストの全項目が含まれていること。"""
        handoff_summary = {}

        result = continuation_session_metrics.build_development_flow_reminder(handoff_summary)

        # 重要な開発フローのチェック項目
        assert "調査・探索" in result
        assert "プラン" in result
        assert "Codexレビュー" in result
        # フックがブロックすることの説明
        assert "各ステップのスキップは個別フックがブロックします" in result

    def test_only_pending_tasks_shown(self):
        """保留タスクのみがある場合。"""
        handoff_summary = {
            "pending_tasks_count": 5,
            "open_prs_count": 0,
        }

        result = continuation_session_metrics.build_development_flow_reminder(handoff_summary)

        assert "保留タスク: 5件" in result
        assert "オープンPR" not in result

    def test_only_open_prs_shown(self):
        """オープンPRのみがある場合。"""
        handoff_summary = {
            "pending_tasks_count": 0,
            "open_prs_count": 2,
        }

        result = continuation_session_metrics.build_development_flow_reminder(handoff_summary)

        assert "保留タスク" not in result
        assert "オープンPR: 2件" in result


class TestMainWithDevelopmentFlowReminder:
    """main関数のテスト - 開発フローリマインダー機能 (Issue #2006)。"""

    def test_continuation_session_returns_reminder_message(self, capsys, tmp_path):
        """継続セッションの場合、開発フローリマインダーメッセージを含む。"""
        log_file = tmp_path / "session-metrics.log"

        handoff_summary = {
            "previous_work_status": "Issue #789 の実装中",
            "previous_next_action": "PRを作成する",
            "pending_tasks_count": 2,
            "open_prs_count": 0,
        }

        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "current-session"
        with (
            patch.object(continuation_session_metrics, "parse_hook_input", return_value={}),
            patch.object(
                continuation_session_metrics,
                "create_hook_context",
                return_value=mock_ctx,
            ),
            patch.object(
                continuation_session_metrics, "is_continuation_session", return_value=True
            ),
            patch.object(
                continuation_session_metrics,
                "get_last_recorded_session_id",
                return_value="old-session",
            ),
            patch.object(
                continuation_session_metrics,
                "get_recorded_session_ids",
                return_value={"old-session"},
            ),
            patch.object(
                continuation_session_metrics,
                "get_session_ids_from_hook_log",
                return_value=["old-session"],
            ),
            patch.object(
                continuation_session_metrics,
                "get_handoff_summary",
                return_value=handoff_summary,
            ),
            patch.object(continuation_session_metrics, "METRICS_LOG_DIR", tmp_path),
            patch.object(continuation_session_metrics, "SESSION_METRICS_LOG", log_file),
            patch.object(continuation_session_metrics, "log_hook_execution"),
        ):
            continuation_session_metrics.main()

            captured = capsys.readouterr()
            output = json.loads(captured.out.strip())

            assert output["continue"] is True
            assert "message" in output
            assert "セッション継続 - 開発フローチェックリスト" in output["message"]
            assert "Issue #789 の実装中" in output["message"]
            assert "PRを作成する" in output["message"]
            assert "保留タスク: 2件" in output["message"]
            assert "Issue作成前に調査・探索を実施したか" in output["message"]

    def test_normal_session_has_no_message(self, capsys):
        """通常セッションの場合、メッセージは含まれない。"""
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "session-1"
        with (
            patch.object(continuation_session_metrics, "parse_hook_input", return_value={}),
            patch.object(
                continuation_session_metrics, "create_hook_context", return_value=mock_ctx
            ),
            patch.object(
                continuation_session_metrics, "is_continuation_session", return_value=False
            ),
            patch.object(continuation_session_metrics, "log_hook_execution"),
        ):
            continuation_session_metrics.main()

            captured = capsys.readouterr()
            output = json.loads(captured.out.strip())

            assert output == {"continue": True}
            assert "message" not in output
