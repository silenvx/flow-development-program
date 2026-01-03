#!/usr/bin/env python3
"""
ci-recovery-tracker.py のテスト
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


ci_recovery_tracker = load_module("ci_recovery_tracker", "ci-recovery-tracker.py")


class TestCIRecoveryTracker:
    """ci-recovery-tracker.py のテスト"""

    def test_is_ci_check_command_positive(self):
        """CI確認コマンドの検出（正常系）"""
        assert ci_recovery_tracker.is_ci_check_command("gh pr checks 123")
        assert ci_recovery_tracker.is_ci_check_command("gh run view 456")
        assert ci_recovery_tracker.is_ci_check_command("gh run watch 789")

    def test_is_ci_check_command_negative(self):
        """CI確認コマンドの検出（非対象コマンド）"""
        assert not ci_recovery_tracker.is_ci_check_command("gh pr create")
        assert not ci_recovery_tracker.is_ci_check_command("npm test")
        assert not ci_recovery_tracker.is_ci_check_command("git status")

    def test_extract_ci_target_number_from_checks(self):
        """gh pr checksからPR番号を抽出"""
        assert ci_recovery_tracker.extract_ci_target_number("gh pr checks 123") == "123"

    def test_extract_ci_target_number_from_run_view(self):
        """gh run viewからrun IDを抽出"""
        assert ci_recovery_tracker.extract_ci_target_number("gh run view 456") == "456"

    def test_extract_ci_target_number_no_number(self):
        """番号がない場合はNone"""
        assert ci_recovery_tracker.extract_ci_target_number("gh pr list") is None

    def test_detect_ci_status_failure_x_mark(self):
        """X markでCI失敗を検出"""
        assert ci_recovery_tracker.detect_ci_status("X CI / test") == "failure"

    def test_detect_ci_status_failure_keyword(self):
        """FAILUREキーワードでCI失敗を検出"""
        assert ci_recovery_tracker.detect_ci_status("FAILURE: Build failed") == "failure"

    def test_detect_ci_status_failure_emoji(self):
        """❌絵文字でCI失敗を検出"""
        assert ci_recovery_tracker.detect_ci_status("❌ Tests failed") == "failure"

    def test_detect_ci_status_success_check_mark(self):
        """✓ markでCI成功を検出"""
        assert ci_recovery_tracker.detect_ci_status("✓ CI / test") == "success"

    def test_detect_ci_status_success_passed(self):
        """'passed'キーワードでCI成功を検出"""
        assert ci_recovery_tracker.detect_ci_status("All checks have passed") == "success"

    def test_detect_ci_status_success_emoji(self):
        """✅絵文字でCI成功を検出"""
        assert ci_recovery_tracker.detect_ci_status("✅ Build succeeded") == "success"

    def test_detect_ci_status_unknown(self):
        """不明な状態はNone"""
        assert ci_recovery_tracker.detect_ci_status("pending") is None
        assert ci_recovery_tracker.detect_ci_status("in_progress") is None


class TestCIRecoveryTrackerBranchHandling:
    """ブランチ切り替え時のCI追跡テスト"""

    def test_branch_change_resets_failure_tracking(self):
        """ブランチ変更時は失敗追跡がリセットされる"""
        # このテストはロジックの確認用
        # failure_time が None か、branch が異なる場合に記録される
        tracking_same_branch = {"failure_time": "2025-01-01T00:00:00", "branch": "main"}
        tracking_different_branch = {"failure_time": "2025-01-01T00:00:00", "branch": "feature"}
        tracking_none = {"failure_time": None, "branch": None}

        current_branch = "main"

        # 同じブランチでは再記録しない
        should_record_same = (
            tracking_same_branch["failure_time"] is None
            or tracking_same_branch["branch"] != current_branch
        )
        assert not should_record_same

        # 異なるブランチでは記録する
        should_record_different = (
            tracking_different_branch["failure_time"] is None
            or tracking_different_branch["branch"] != current_branch
        )
        assert should_record_different

        # failure_time が None なら記録する
        should_record_none = (
            tracking_none["failure_time"] is None or tracking_none["branch"] != current_branch
        )
        assert should_record_none
