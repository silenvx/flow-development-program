#!/usr/bin/env python3
"""git-config-check.py のテスト。"""

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


# Import module with hyphen in name using importlib

spec = importlib.util.spec_from_file_location(
    "git_config_check",
    Path(__file__).parent.parent / "git-config-check.py",
)
git_config_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(git_config_check)


class TestGetCoreBare:
    """get_core_bare関数のテスト。"""

    @patch.object(git_config_check, "subprocess")
    def test_returns_true_when_bare_is_true(self, mock_subprocess):
        """core.bareがtrueの場合trueを返す。"""
        mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="true\n")
        mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired
        result = git_config_check.get_core_bare()
        assert result == "true"

    @patch.object(git_config_check, "subprocess")
    def test_returns_false_when_bare_is_false(self, mock_subprocess):
        """core.bareがfalseの場合falseを返す。"""
        mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="false\n")
        mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired
        result = git_config_check.get_core_bare()
        assert result == "false"

    @patch.object(git_config_check, "subprocess")
    def test_returns_none_on_error(self, mock_subprocess):
        """gitコマンドが失敗した場合Noneを返す。"""
        mock_subprocess.run.return_value = MagicMock(returncode=1, stdout="")
        mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired
        result = git_config_check.get_core_bare()
        assert result is None

    @patch.object(git_config_check, "subprocess")
    def test_returns_none_on_timeout(self, mock_subprocess):
        """タイムアウトした場合Noneを返す。"""
        mock_subprocess.run.side_effect = subprocess.TimeoutExpired("git", 5)
        mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired
        result = git_config_check.get_core_bare()
        assert result is None


class TestFixCoreBare:
    """fix_core_bare関数のテスト。"""

    @patch.object(git_config_check, "subprocess")
    def test_returns_true_on_success(self, mock_subprocess):
        """修正成功時にTrueを返す。"""
        mock_subprocess.run.return_value = MagicMock(returncode=0)
        mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired
        result = git_config_check.fix_core_bare()
        assert result is True

    @patch.object(git_config_check, "subprocess")
    def test_returns_false_on_failure(self, mock_subprocess):
        """修正失敗時にFalseを返す。"""
        mock_subprocess.run.return_value = MagicMock(returncode=1)
        mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired
        result = git_config_check.fix_core_bare()
        assert result is False


class TestMain:
    """main関数のテスト。"""

    @patch.object(git_config_check, "get_core_bare")
    def test_does_nothing_when_bare_is_false(self, mock_get, capsys):
        """core.bareがfalseの場合何もしない。"""
        mock_get.return_value = "false"
        git_config_check.main()
        captured = capsys.readouterr()
        assert captured.out == ""

    @patch.object(git_config_check, "get_core_bare")
    def test_does_nothing_when_bare_not_set(self, mock_get, capsys):
        """core.bareが設定されていない場合何もしない。"""
        mock_get.return_value = None
        git_config_check.main()
        captured = capsys.readouterr()
        assert captured.out == ""

    @patch.object(git_config_check, "fix_core_bare")
    @patch.object(git_config_check, "get_core_bare")
    def test_fixes_and_warns_when_bare_is_true(self, mock_get, mock_fix, capsys):
        """core.bareがtrueの場合、修正して警告を出力する。"""
        mock_get.return_value = "true"
        mock_fix.return_value = True
        git_config_check.main()
        captured = capsys.readouterr()
        assert "自動修正しました" in captured.out
        assert "core.bare=true" in captured.out
        assert "Issue #975" in captured.out

    @patch.object(git_config_check, "fix_core_bare")
    @patch.object(git_config_check, "get_core_bare")
    def test_warns_when_fix_fails(self, mock_get, mock_fix, capsys):
        """修正に失敗した場合、手動修正方法を出力する。"""
        mock_get.return_value = "true"
        mock_fix.return_value = False
        git_config_check.main()
        captured = capsys.readouterr()
        assert "自動修正に失敗しました" in captured.out
        assert "手動修正方法" in captured.out
