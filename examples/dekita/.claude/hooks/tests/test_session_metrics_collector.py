#!/usr/bin/env python3
"""
session_metrics_collector.py のテスト
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# テスト対象のモジュールをインポート
sys.path.insert(0, str(Path(__file__).parent.parent))

from session_metrics_collector import collect_session_metrics


class TestCollectSessionMetrics:
    """collect_session_metricsのテスト"""

    def test_collect_returns_bool(self):
        """収集関数がboolを返すことを確認"""
        # スクリプトが存在しない場合はFalseを返す
        with patch("session_metrics_collector.SCRIPT_DIR", Path("/nonexistent")):
            result = collect_session_metrics("test-session-123")
            assert not result

    def test_passes_session_id_via_env(self):
        """session_idが環境変数で渡されることを確認"""
        with patch("session_metrics_collector.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # 実際のスクリプトパスをモック
            with patch("session_metrics_collector.SCRIPT_DIR") as mock_dir:
                mock_script = MagicMock()
                mock_script.exists.return_value = True
                mock_dir.__truediv__ = MagicMock(return_value=mock_script)

                collect_session_metrics("my-session-uuid")

                # subprocess.runが呼ばれたことを確認
                mock_run.assert_called_once()
                # Issue #2317: コマンドライン引数に--session-idが含まれていることを確認
                call_args = mock_run.call_args[0][0]  # 位置引数のリスト
                assert "--session-id" in call_args
                session_id_index = call_args.index("--session-id")
                assert call_args[session_id_index + 1] == "my-session-uuid"


class TestSessionMetricsCollectorHook:
    """セッションメトリクス収集フックのテスト"""

    def test_stop_hook_active_skipped(self):
        """stop_hook_active=trueの場合は即座にapprove"""
        hook_input = {"stop_hook_active": True}

        with (
            patch("sys.stdin") as mock_stdin,
            patch("builtins.print") as mock_print,
        ):
            mock_stdin.read.return_value = json.dumps(hook_input)

            import session_metrics_collector

            with (
                patch.object(session_metrics_collector, "log_hook_execution"),
                patch.object(session_metrics_collector, "collect_session_metrics"),
            ):
                session_metrics_collector.main()
                output = json.loads(mock_print.call_args[0][0])
                assert output["decision"] == "approve"

    def test_normal_execution_approves(self):
        """通常実行時はapproveを返す"""
        hook_input = {}

        with (
            patch("sys.stdin") as mock_stdin,
            patch("builtins.print") as mock_print,
        ):
            mock_stdin.read.return_value = json.dumps(hook_input)

            import session_metrics_collector

            with (
                patch.object(session_metrics_collector, "log_hook_execution"),
                patch.object(
                    session_metrics_collector, "collect_session_metrics", return_value=True
                ),
            ):
                session_metrics_collector.main()
                output = json.loads(mock_print.call_args[0][0])
                assert output["decision"] == "approve"

    def test_uses_session_id_from_hook_input(self):
        """hook入力のsession_idを優先して使用する (Issue #1308)"""
        hook_input = {"session_id": "uuid-from-hook-input"}

        with (
            patch("sys.stdin") as mock_stdin,
            patch("builtins.print"),
        ):
            mock_stdin.read.return_value = json.dumps(hook_input)

            import session_metrics_collector

            with (
                patch.object(session_metrics_collector, "log_hook_execution"),
                patch.object(
                    session_metrics_collector, "collect_session_metrics", return_value=True
                ) as mock_collect,
            ):
                session_metrics_collector.main()
                # hook入力のsession_idが使われることを確認
                mock_collect.assert_called_once_with("uuid-from-hook-input")

    def test_falls_back_to_ctx_get_session_id(self):
        """hook入力にsession_idがない場合はctx.get_session_id()にフォールバック"""
        hook_input = {}  # session_idなし

        with (
            patch("sys.stdin") as mock_stdin,
            patch("builtins.print"),
        ):
            mock_stdin.read.return_value = json.dumps(hook_input)

            from unittest.mock import MagicMock

            import session_metrics_collector

            mock_ctx = MagicMock()
            mock_ctx.get_session_id.return_value = "fallback-id"

            with (
                patch.object(
                    session_metrics_collector, "create_hook_context", return_value=mock_ctx
                ),
                patch.object(session_metrics_collector, "log_hook_execution"),
                patch.object(
                    session_metrics_collector, "collect_session_metrics", return_value=True
                ) as mock_collect,
            ):
                session_metrics_collector.main()
                # ctx.get_session_id()が呼ばれたことを確認
                mock_ctx.get_session_id.assert_called_once()
                # フォールバックIDが使われることを確認
                mock_collect.assert_called_once_with("fallback-id")

    def test_empty_session_id_falls_back(self):
        """空文字列のsession_idはフォールバックする (Issue #1308)"""
        hook_input = {"session_id": ""}  # 空文字列

        with (
            patch("sys.stdin") as mock_stdin,
            patch("builtins.print"),
        ):
            mock_stdin.read.return_value = json.dumps(hook_input)

            from unittest.mock import MagicMock

            import session_metrics_collector

            mock_ctx = MagicMock()
            mock_ctx.get_session_id.return_value = "fallback-id"

            with (
                patch.object(
                    session_metrics_collector, "create_hook_context", return_value=mock_ctx
                ),
                patch.object(session_metrics_collector, "log_hook_execution"),
                patch.object(
                    session_metrics_collector, "collect_session_metrics", return_value=True
                ) as mock_collect,
            ):
                session_metrics_collector.main()
                # 空文字列の場合もctx.get_session_id()にフォールバック
                mock_ctx.get_session_id.assert_called_once()
                mock_collect.assert_called_once_with("fallback-id")
