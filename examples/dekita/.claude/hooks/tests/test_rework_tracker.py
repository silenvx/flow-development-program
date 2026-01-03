#!/usr/bin/env python3
"""
rework-tracker.py のテスト
"""

import importlib.util
import sys
from pathlib import Path

# hooks ディレクトリをパスに追加
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))


# ハイフン付きファイル名のモジュールをロード
def load_module(name: str, filename: str):
    """Load a Python module from a hyphenated filename."""
    spec = importlib.util.spec_from_file_location(
        name,
        HOOKS_DIR / filename,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rework_tracker = load_module("rework_tracker", "rework-tracker.py")


class TestReworkTracker:
    """rework-tracker.py のテスト"""

    # Note: get_session_id tests are in test_common.py since the function is in common.py

    def test_get_rework_window_minutes(self):
        """手戻りウィンドウのデフォルト値を確認"""
        # デフォルトは5分
        assert rework_tracker.REWORK_WINDOW_MINUTES == 5

    def test_get_rework_threshold(self):
        """手戻り閾値のデフォルト値を確認"""
        # デフォルトは3回
        assert rework_tracker.REWORK_THRESHOLD == 3

    def test_get_rework_high_threshold(self):
        """高閾値のデフォルト値を確認 (Issue #1335)"""
        # デフォルトは5回
        assert rework_tracker.REWORK_HIGH_THRESHOLD == 5

    def test_get_rework_critical_threshold(self):
        """クリティカル閾値のデフォルト値を確認 (Issue #1362)"""
        # デフォルトは7回
        assert rework_tracker.REWORK_CRITICAL_THRESHOLD == 7

    def test_high_threshold_greater_than_normal(self):
        """高閾値は通常閾値より大きいこと (Issue #1335)"""
        assert rework_tracker.REWORK_HIGH_THRESHOLD > rework_tracker.REWORK_THRESHOLD

    def test_critical_threshold_greater_than_high(self):
        """クリティカル閾値は高閾値より大きいこと (Issue #1362)"""
        assert rework_tracker.REWORK_CRITICAL_THRESHOLD > rework_tracker.REWORK_HIGH_THRESHOLD


class TestGenerateWarningMessage:
    """generate_warning_message関数の統合テスト (Issue #1335 Copilot review)"""

    def test_below_threshold_returns_none(self):
        """閾値未満（2回）の場合はNoneを返す"""
        result = rework_tracker.generate_warning_message("/path/to/test.py", 2, 5)
        assert result is None

    def test_normal_threshold_returns_info_message(self):
        """通常閾値（3回）の場合は📊で始まる情報メッセージを返す"""
        result = rework_tracker.generate_warning_message("/path/to/test.py", 3, 5)

        assert result is not None
        assert "📊" in result
        assert "手戻り検出" in result
        assert "test.py" in result
        assert "5分以内に3回編集" in result
        assert "試行錯誤" not in result  # 高閾値メッセージには含まれるが通常は含まれない

    def test_normal_threshold_4_edits(self):
        """通常閾値（4回）でも情報メッセージを返す"""
        result = rework_tracker.generate_warning_message("/path/to/file.py", 4, 5)

        assert result is not None
        assert "📊" in result
        assert "4回編集" in result

    def test_high_threshold_returns_warning_message(self):
        """高閾値（5回）の場合は⚠️で始まる警告メッセージを返す"""
        result = rework_tracker.generate_warning_message("/path/to/test.py", 5, 5)

        assert result is not None
        assert "⚠️" in result
        assert "高頻度編集検出" in result
        assert "test.py" in result
        assert "5分以内に5回編集" in result
        assert "試行錯誤" in result

    def test_high_threshold_includes_root_cause_analysis(self):
        """高閾値メッセージには原因分析の質問が含まれる (Issue #1362)"""
        result = rework_tracker.generate_warning_message("/path/to/test.py", 5, 5)

        assert result is not None
        assert "テストを先に書いていますか？" in result
        assert "変更の要件は明確ですか？" in result
        assert "設計を見直す必要はありませんか？" in result

    def test_high_threshold_6_edits(self):
        """高閾値（6回）でも警告メッセージを返す"""
        result = rework_tracker.generate_warning_message("/path/to/file.py", 6, 5)

        assert result is not None
        assert "⚠️" in result
        assert "6回編集" in result
        # 6回はクリティカルではないので停止推奨メッセージではない
        assert "🛑" not in result

    def test_critical_threshold_returns_stop_message(self):
        """クリティカル閾値（7回）の場合は🛑で始まる停止推奨メッセージを返す (Issue #1362)"""
        result = rework_tracker.generate_warning_message("/path/to/test.py", 7, 5)

        assert result is not None
        assert "🛑" in result
        assert "停止推奨" in result
        assert "test.py" in result
        assert "5分以内に7回編集" in result
        assert "試行錯誤" in result

    def test_critical_threshold_includes_stop_instructions(self):
        """クリティカル閾値メッセージには停止と見直しの指示が含まれる (Issue #1362)"""
        result = rework_tracker.generate_warning_message("/path/to/test.py", 7, 5)

        assert result is not None
        assert "作業を一時停止する" in result
        assert "アプローチを振り返る" in result
        assert "プランを見直す" in result
        assert "全体設計を明確に" in result

    def test_critical_threshold_10_edits(self):
        """クリティカル閾値超過（10回）でも停止推奨メッセージを返す (Issue #1362)"""
        result = rework_tracker.generate_warning_message("/path/to/file.py", 10, 5)

        assert result is not None
        assert "🛑" in result
        assert "停止推奨" in result
        assert "10回編集" in result

    def test_message_uses_file_basename(self):
        """メッセージにはファイル名（パスではなく）が含まれる"""
        result = rework_tracker.generate_warning_message("/very/long/path/to/my_file.py", 5, 5)

        assert result is not None
        assert "my_file.py" in result
        assert "/very/long/path/to/" not in result
