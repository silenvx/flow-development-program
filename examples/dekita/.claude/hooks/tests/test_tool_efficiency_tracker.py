#!/usr/bin/env python3
"""
tool-efficiency-tracker.py のテスト
"""

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
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


tool_efficiency_tracker = load_module("tool_efficiency_tracker", "tool-efficiency-tracker.py")


class TestToolEfficiencyTracker:
    """tool-efficiency-tracker.py のテスト"""

    def test_extract_target_read(self):
        """Readツールのターゲット抽出"""
        tool_input = {"file_path": "/path/to/file.py"}
        assert tool_efficiency_tracker.extract_target("Read", tool_input) == "/path/to/file.py"

    def test_extract_target_edit(self):
        """Editツールのターゲット抽出"""
        tool_input = {"file_path": "/path/to/file.py", "old_string": "foo", "new_string": "bar"}
        assert tool_efficiency_tracker.extract_target("Edit", tool_input) == "/path/to/file.py"

    def test_extract_target_glob(self):
        """Globツールのターゲット抽出"""
        tool_input = {"pattern": "**/*.py"}
        assert tool_efficiency_tracker.extract_target("Glob", tool_input) == "**/*.py"

    def test_extract_target_grep(self):
        """Grepツールのターゲット抽出"""
        tool_input = {"pattern": "TODO"}
        assert tool_efficiency_tracker.extract_target("Grep", tool_input) == "TODO"

    def test_extract_target_bash(self):
        """Bashツールのターゲット抽出"""
        tool_input = {"command": "npm run test"}
        assert tool_efficiency_tracker.extract_target("Bash", tool_input) == "npm run test"

    def test_detect_read_edit_loop_no_pattern(self):
        """Read-Editループ検出（パターンなし）"""
        calls = [
            {"tool": "Read", "target": "/file.py", "timestamp": datetime.now(UTC).isoformat()},
            {"tool": "Edit", "target": "/file.py", "timestamp": datetime.now(UTC).isoformat()},
        ]
        assert tool_efficiency_tracker.detect_read_edit_loop(calls) is None

    def test_detect_read_edit_loop_with_pattern(self):
        """Read-Editループ検出（パターンあり）"""
        now = datetime.now(UTC)
        calls = [
            {"tool": "Read", "target": "/file.py", "timestamp": now.isoformat()},
            {"tool": "Edit", "target": "/file.py", "timestamp": now.isoformat()},
            {"tool": "Read", "target": "/file.py", "timestamp": now.isoformat()},
            {"tool": "Edit", "target": "/file.py", "timestamp": now.isoformat()},
            {"tool": "Read", "target": "/file.py", "timestamp": now.isoformat()},
            {"tool": "Edit", "target": "/file.py", "timestamp": now.isoformat()},
        ]
        result = tool_efficiency_tracker.detect_read_edit_loop(calls)
        assert result is not None
        assert result["pattern"] == "read_edit_loop"
        assert result["file"] == "/file.py"

    def test_detect_repeated_search_no_pattern(self):
        """繰り返し検索検出（パターンなし）"""
        calls = [
            {"tool": "Grep", "target": "TODO", "timestamp": datetime.now(UTC).isoformat()},
            {"tool": "Grep", "target": "FIXME", "timestamp": datetime.now(UTC).isoformat()},
        ]
        assert tool_efficiency_tracker.detect_repeated_search(calls) is None

    def test_detect_repeated_search_with_pattern(self):
        """繰り返し検索検出（パターンあり）"""
        now = datetime.now(UTC)
        calls = [
            {"tool": "Grep", "target": "TODO", "timestamp": now.isoformat()},
            {"tool": "Grep", "target": "todo", "timestamp": now.isoformat()},
            {"tool": "Grep", "target": "TODO", "timestamp": now.isoformat()},
        ]
        result = tool_efficiency_tracker.detect_repeated_search(calls)
        assert result is not None
        assert result["pattern"] == "repeated_search"
        assert result["count"] >= 3

    def test_detect_bash_retry_no_pattern(self):
        """Bashリトライ検出（パターンなし）"""
        calls = [
            {
                "tool": "Bash",
                "target": "npm test",
                "success": True,
                "timestamp": datetime.now(UTC).isoformat(),
            },
            {
                "tool": "Bash",
                "target": "npm test",
                "success": False,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        ]
        assert tool_efficiency_tracker.detect_bash_retry(calls) is None

    def test_detect_bash_retry_with_pattern(self):
        """Bashリトライ検出（パターンあり）"""
        now = datetime.now(UTC)
        calls = [
            {"tool": "Bash", "target": "npm test", "success": False, "timestamp": now.isoformat()},
            {"tool": "Bash", "target": "npm test", "success": False, "timestamp": now.isoformat()},
            {"tool": "Bash", "target": "npm test", "success": False, "timestamp": now.isoformat()},
        ]
        result = tool_efficiency_tracker.detect_bash_retry(calls)
        assert result is not None
        assert result["pattern"] == "bash_retry"
        assert result["failure_count"] >= 3

    def test_detect_high_frequency_rework_no_pattern(self):
        """高頻度Rework検出（パターンなし）- Issue #1630"""
        now = datetime.now(UTC)
        calls = [
            {"tool": "Edit", "target": "/file.py", "timestamp": now.isoformat()},
            {"tool": "Edit", "target": "/file.py", "timestamp": now.isoformat()},
        ]
        assert tool_efficiency_tracker.detect_high_frequency_rework(calls, now) is None

    def test_detect_high_frequency_rework_with_pattern(self):
        """高頻度Rework検出（パターンあり）- Issue #1630"""
        now = datetime.now(UTC)
        calls = [
            {"tool": "Edit", "target": "/file.py", "timestamp": now.isoformat()},
            {"tool": "Edit", "target": "/file.py", "timestamp": now.isoformat()},
            {"tool": "Edit", "target": "/file.py", "timestamp": now.isoformat()},
        ]
        result = tool_efficiency_tracker.detect_high_frequency_rework(calls, now)
        assert result is not None
        assert result["pattern"] == "high_frequency_rework"
        assert result["file"] == "/file.py"
        assert result["edit_count"] == 3

    def test_detect_high_frequency_rework_different_files(self):
        """高頻度Rework検出（異なるファイル）- Issue #1630"""
        now = datetime.now(UTC)
        calls = [
            {"tool": "Edit", "target": "/file1.py", "timestamp": now.isoformat()},
            {"tool": "Edit", "target": "/file2.py", "timestamp": now.isoformat()},
            {"tool": "Edit", "target": "/file3.py", "timestamp": now.isoformat()},
        ]
        # 異なるファイルへの編集は高頻度Reworkとして検出されない
        assert tool_efficiency_tracker.detect_high_frequency_rework(calls, now) is None

    def test_detect_high_frequency_rework_outside_window(self):
        """高頻度Rework検出（5分間ウィンドウ外）- Issue #1630"""
        now = datetime.now(UTC)
        old = now - timedelta(minutes=10)  # 10分前（5分間ウィンドウ外）
        calls = [
            {"tool": "Edit", "target": "/file.py", "timestamp": old.isoformat()},
            {"tool": "Edit", "target": "/file.py", "timestamp": old.isoformat()},
            {"tool": "Edit", "target": "/file.py", "timestamp": old.isoformat()},
        ]
        # 5分間ウィンドウ外の編集は検出されない
        assert tool_efficiency_tracker.detect_high_frequency_rework(calls, now) is None

    def test_detect_high_frequency_rework_within_window(self):
        """高頻度Rework検出（5分間ウィンドウ内）- Issue #1630"""
        now = datetime.now(UTC)
        recent = now - timedelta(minutes=2)  # 2分前（5分間ウィンドウ内）
        calls = [
            {"tool": "Edit", "target": "/file.py", "timestamp": recent.isoformat()},
            {"tool": "Edit", "target": "/file.py", "timestamp": recent.isoformat()},
            {"tool": "Edit", "target": "/file.py", "timestamp": recent.isoformat()},
        ]
        result = tool_efficiency_tracker.detect_high_frequency_rework(calls, now)
        assert result is not None
        assert result["pattern"] == "high_frequency_rework"
        assert result["file"] == "/file.py"
