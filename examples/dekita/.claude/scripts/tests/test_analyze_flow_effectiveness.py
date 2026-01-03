#!/usr/bin/env python3
"""
analyze-flow-effectiveness.py のテスト
"""

import importlib.util
import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# テスト対象のモジュールをインポート（ハイフン付きファイル名のため動的インポート）
_script_path = Path(__file__).parent.parent / "analyze-flow-effectiveness.py"
_spec = importlib.util.spec_from_file_location("analyze_flow_effectiveness", _script_path)
_module = importlib.util.module_from_spec(_spec)
sys.modules["analyze_flow_effectiveness"] = _module
_spec.loader.exec_module(_module)

# モジュールから必要な要素をインポート
SCRIPT_DIR = _module.SCRIPT_DIR
AnalysisReport = _module.AnalysisReport
GitOperationsStats = _module.GitOperationsStats
HookStats = _module.HookStats
PRStats = _module.PRStats
SessionStats = _module.SessionStats
_get_main_project_root = _module._get_main_project_root
analyze_hooks = _module.analyze_hooks
analyze_sessions = _module.analyze_sessions
detect_issues = _module.detect_issues
format_report_markdown = _module.format_report_markdown
generate_recommendations = _module.generate_recommendations
load_hook_logs = _module.load_hook_logs


class TestGetMainProjectRoot:
    """_get_main_project_rootのテスト（パス解析ロジック）"""

    def test_returns_absolute_path(self):
        """戻り値が絶対パスであることを確認"""
        result = _get_main_project_root()
        assert result.is_absolute()

    def test_returns_existing_directory(self):
        """戻り値が存在するディレクトリであることを確認"""
        result = _get_main_project_root()
        assert result.exists()
        assert result.is_dir()

    def test_contains_claude_directory(self):
        """プロジェクトルートに.claudeディレクトリが存在することを確認"""
        result = _get_main_project_root()
        claude_dir = result / ".claude"
        assert claude_dir.exists(), f"Expected .claude directory at {claude_dir}"

    def test_relative_path_resolution(self, tmp_path, monkeypatch):
        """相対パス（.git）が返される場合の処理を確認"""
        # モック用のgitディレクトリを作成
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        # subprocess.runをモックして相対パス ".git" を返す
        def mock_run(*args, **kwargs):
            class MockResult:
                returncode = 0
                stdout = ".git"

            return MockResult()

        monkeypatch.setattr(subprocess, "run", mock_run)

        # SCRIPT_DIRをtmp_pathに設定してテスト
        monkeypatch.setattr("analyze_flow_effectiveness.SCRIPT_DIR", tmp_path)

        # 関数を再インポートして実行
        from analyze_flow_effectiveness import _get_main_project_root

        result = _get_main_project_root()

        # 相対パスが正しく解決されて絶対パスになることを確認
        assert result.is_absolute()

    def test_absolute_path_from_git(self, tmp_path, monkeypatch):
        """絶対パスが返される場合の処理を確認"""
        # モック用のgitディレクトリを作成
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        # subprocess.runをモックして絶対パスを返す
        def mock_run(*args, **kwargs):
            class MockResult:
                returncode = 0
                stdout = str(git_dir)

            return MockResult()

        monkeypatch.setattr(subprocess, "run", mock_run)

        from analyze_flow_effectiveness import _get_main_project_root

        result = _get_main_project_root()

        assert result.is_absolute()
        assert result == tmp_path

    def test_git_command_failure_fallback(self, monkeypatch):
        """gitコマンド失敗時のフォールバック処理を確認"""

        def mock_run(*args, **kwargs):
            class MockResult:
                returncode = 1
                stdout = ""

            return MockResult()

        monkeypatch.setattr(subprocess, "run", mock_run)

        from analyze_flow_effectiveness import _get_main_project_root

        result = _get_main_project_root()

        # フォールバック: SCRIPT_DIR.parent.parent が返される
        assert result.is_absolute()

    def test_worktree_scenario(self, tmp_path, monkeypatch):
        """worktree内から実行した場合のパス解析を確認"""
        # メインリポジトリの.gitディレクトリをシミュレート
        main_repo = tmp_path / "main_repo"
        main_repo.mkdir()
        main_git = main_repo / ".git"
        main_git.mkdir()

        # worktreeからはcommon dirとしてメインの.gitを参照
        def mock_run(*args, **kwargs):
            class MockResult:
                returncode = 0
                stdout = str(main_git)

            return MockResult()

        monkeypatch.setattr(subprocess, "run", mock_run)

        from analyze_flow_effectiveness import _get_main_project_root

        result = _get_main_project_root()

        assert result == main_repo


class TestScriptExecutionFromDifferentDirectories:
    """異なるディレクトリからのスクリプト実行テスト"""

    def test_script_execution_from_project_root(self):
        """プロジェクトルートからのスクリプト実行が正常に動作することを確認"""
        project_root = _get_main_project_root()
        script_path = project_root / ".claude" / "scripts" / "analyze-flow-effectiveness.py"

        result = subprocess.run(
            ["python", str(script_path), "report"],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            timeout=30,
        )

        # スクリプトが正常終了し、レポートが出力されることを確認
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert "開発フロー評価レポート" in result.stdout

    def test_script_execution_from_scripts_directory(self):
        """scriptsディレクトリからのスクリプト実行が正常に動作することを確認"""
        project_root = _get_main_project_root()
        scripts_dir = project_root / ".claude" / "scripts"
        script_path = scripts_dir / "analyze-flow-effectiveness.py"

        result = subprocess.run(
            ["python", str(script_path), "report"],
            capture_output=True,
            text=True,
            cwd=str(scripts_dir),
            timeout=30,
        )

        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert "開発フロー評価レポート" in result.stdout

    def test_script_execution_from_temp_directory(self, tmp_path):
        """一時ディレクトリからのスクリプト実行が正常に動作することを確認"""
        project_root = _get_main_project_root()
        script_path = project_root / ".claude" / "scripts" / "analyze-flow-effectiveness.py"

        result = subprocess.run(
            ["python", str(script_path), "report"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            timeout=30,
        )

        assert result.returncode == 0, f"Script failed: {result.stderr}"
        assert "開発フロー評価レポート" in result.stdout


class TestHookStats:
    """HookStatsのテスト"""

    def test_block_rate_with_data(self):
        stats = HookStats(name="test", total=100, approves=90, blocks=10)
        assert stats.block_rate == 0.10

    def test_block_rate_empty(self):
        stats = HookStats(name="test", total=0)
        assert stats.block_rate == 0.0

    def test_block_rate_no_blocks(self):
        stats = HookStats(name="test", total=50, approves=50, blocks=0)
        assert stats.block_rate == 0.0


class TestSessionStats:
    """SessionStatsのテスト"""

    def test_duration_with_times(self):
        start = datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC)
        end = datetime(2024, 1, 1, 12, 30, 0, tzinfo=UTC)
        stats = SessionStats(session_id="test", start_time=start, end_time=end)
        assert stats.duration_seconds == 9000  # 2.5 hours

    def test_duration_without_times(self):
        stats = SessionStats(session_id="test")
        assert stats.duration_seconds is None


class TestPRStats:
    """PRStatsのテスト"""

    def test_cycle_time_with_dates(self):
        created = datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC)
        merged = datetime(2024, 1, 1, 11, 0, 0, tzinfo=UTC)
        stats = PRStats(pr_number=1, created_at=created, merged_at=merged)
        assert stats.cycle_time_seconds == 3600  # 1 hour

    def test_cycle_time_not_merged(self):
        created = datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC)
        stats = PRStats(pr_number=1, created_at=created)
        assert stats.cycle_time_seconds is None


class TestLoadHookLogs:
    """load_hook_logsのテスト"""

    def test_load_valid_logs(self, tmp_path):
        log_file = tmp_path / "hook-execution.log"
        logs = [
            {"timestamp": "2024-01-01T10:00:00+00:00", "hook": "test", "decision": "approve"},
            {"timestamp": "2024-01-01T11:00:00+00:00", "hook": "test", "decision": "block"},
        ]
        log_file.write_text("\n".join(json.dumps(entry) for entry in logs))

        with patch("analyze_flow_effectiveness.HOOK_LOG", log_file):
            result = load_hook_logs()

        assert len(result) == 2

    def test_load_with_since_filter(self, tmp_path):
        log_file = tmp_path / "hook-execution.log"
        logs = [
            {"timestamp": "2024-01-01T10:00:00+00:00", "hook": "test", "decision": "approve"},
            {"timestamp": "2024-01-02T10:00:00+00:00", "hook": "test", "decision": "block"},
        ]
        log_file.write_text("\n".join(json.dumps(entry) for entry in logs))

        since = datetime(2024, 1, 2, 0, 0, 0, tzinfo=UTC)

        with patch("analyze_flow_effectiveness.HOOK_LOG", log_file):
            result = load_hook_logs(since)

        assert len(result) == 1
        assert result[0]["decision"] == "block"

    def test_load_nonexistent_file(self, tmp_path):
        log_file = tmp_path / "nonexistent.log"

        with patch("analyze_flow_effectiveness.HOOK_LOG", log_file):
            result = load_hook_logs()

        assert result == []


class TestAnalyzeHooks:
    """analyze_hooksのテスト"""

    def test_analyze_hooks(self):
        logs = [
            {"hook": "hook1", "decision": "approve"},
            {"hook": "hook1", "decision": "approve"},
            {"hook": "hook1", "decision": "block", "reason": "test reason"},
            {"hook": "hook2", "decision": "approve"},
        ]

        stats = analyze_hooks(logs)

        assert "hook1" in stats
        assert stats["hook1"].total == 3
        assert stats["hook1"].approves == 2
        assert stats["hook1"].blocks == 1
        assert stats["hook1"].block_reasons["test reason"] == 1

        assert "hook2" in stats
        assert stats["hook2"].total == 1


class TestAnalyzeSessions:
    """analyze_sessionsのテスト"""

    def test_analyze_sessions(self):
        logs = [
            {
                "session_id": "s1",
                "timestamp": "2024-01-01T10:00:00+00:00",
                "decision": "approve",
                "branch": "main",
            },
            {
                "session_id": "s1",
                "timestamp": "2024-01-01T11:00:00+00:00",
                "decision": "block",
                "branch": "feature",
            },
            {"session_id": "s2", "timestamp": "2024-01-01T12:00:00+00:00", "decision": "approve"},
        ]

        stats = analyze_sessions(logs)

        assert "s1" in stats
        assert stats["s1"].hook_executions == 2
        assert stats["s1"].blocks == 1
        assert "main" in stats["s1"].branches
        assert "feature" in stats["s1"].branches

        assert "s2" in stats
        assert stats["s2"].hook_executions == 1


class TestDetectIssues:
    """detect_issuesのテスト"""

    def test_detect_low_block_rate(self):
        hook_stats = {
            "hook1": HookStats(name="hook1", total=1000, approves=999, blocks=1),
        }
        issues = detect_issues(hook_stats, {}, {})

        assert any(i["type"] == "low_block_rate" for i in issues)

    def test_detect_high_block_rate(self):
        hook_stats = {
            "hook1": HookStats(name="hook1", total=100, approves=70, blocks=30),
        }
        issues = detect_issues(hook_stats, {}, {})

        assert any(i["type"] == "high_block_rate" for i in issues)

    def test_detect_long_session(self):
        start = datetime.now(UTC) - timedelta(hours=5)
        end = datetime.now(UTC)
        session_stats = {
            "s1": SessionStats(session_id="s1", start_time=start, end_time=end),
        }
        issues = detect_issues({}, session_stats, {})

        assert any(i["type"] == "long_session" for i in issues)

    def test_detect_long_pr_cycle(self):
        created = datetime.now(UTC) - timedelta(hours=30)
        merged = datetime.now(UTC)
        pr_stats = {
            123: PRStats(pr_number=123, created_at=created, merged_at=merged),
        }
        issues = detect_issues({}, {}, pr_stats)

        assert any(i["type"] == "long_pr_cycle" for i in issues)


class TestGenerateRecommendations:
    """generate_recommendationsのテスト"""

    def test_generate_block_reasons_recommendation(self):
        hook_stats = {
            "hook1": HookStats(
                name="hook1",
                total=10,
                blocks=5,
                block_reasons=Counter({"reason1": 3, "reason2": 2}),
            ),
        }
        recommendations = generate_recommendations(hook_stats, {}, {}, [])

        assert any("ブロック理由" in r for r in recommendations)

    def test_generate_coverage_recommendation(self):
        hook_stats = {}  # No hooks
        recommendations = generate_recommendations(hook_stats, {}, {}, [])

        assert any("カバレッジ不足" in r for r in recommendations)


class TestFormatReportMarkdown:
    """format_report_markdownのテスト"""

    def test_format_basic_report(self):
        report = AnalysisReport(
            generated_at=datetime.now(UTC),
            period_start=None,
            period_end=None,
            hook_stats={
                "hook1": HookStats(name="hook1", total=10, approves=9, blocks=1),
            },
            session_stats={},
            pr_stats={},
            git_operations_stats=GitOperationsStats(),
            issues=[],
            recommendations=[],
        )

        markdown = format_report_markdown(report)

        assert "# 開発フロー評価レポート" in markdown
        assert "hook1" in markdown
        assert "10" in markdown

    def test_format_report_with_issues(self):
        report = AnalysisReport(
            generated_at=datetime.now(UTC),
            period_start=None,
            period_end=None,
            hook_stats={},
            session_stats={},
            pr_stats={},
            git_operations_stats=GitOperationsStats(),
            issues=[
                {
                    "type": "test",
                    "severity": "warning",
                    "message": "Test issue",
                    "detail": "Test detail",
                    "recommendation": "Test recommendation",
                }
            ],
            recommendations=[],
        )

        markdown = format_report_markdown(report)

        assert "検出された問題" in markdown
        assert "Test issue" in markdown


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
