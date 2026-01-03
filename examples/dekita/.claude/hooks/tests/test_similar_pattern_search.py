"""Tests for similar-pattern-search.py hook."""

import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

# Load hook module with hyphenated filename
HOOKS_DIR = Path(__file__).parent.parent
HOOK_PATH = HOOKS_DIR / "similar-pattern-search.py"
spec = importlib.util.spec_from_file_location("similar_pattern_search", HOOK_PATH)
similar_pattern_search = importlib.util.module_from_spec(spec)
spec.loader.exec_module(similar_pattern_search)


def run_hook(
    tool_name: str,
    tool_input: dict[str, str],
    tool_output: str = "",
    exit_code: int = 0,
    monkeypatch=None,
) -> dict:
    """テスト用ヘルパー: フックを実行し結果を返す"""
    hook_input = {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_output": tool_output,
        "exit_code": exit_code,
    }

    monkeypatch.setattr(
        "sys.stdin",
        type("stdin", (), {"read": lambda self: json.dumps(hook_input)})(),
    )

    captured_output = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured_output)

    similar_pattern_search.main()

    return json.loads(captured_output.getvalue())


class TestIsPrMergeCommand:
    """Test cases for is_pr_merge_command function."""

    def test_merge_command(self):
        """gh pr merge コマンドを検出"""
        assert similar_pattern_search.is_pr_merge_command("gh pr merge 123")

    def test_merge_with_flags(self):
        """フラグ付きマージコマンドを検出"""
        assert similar_pattern_search.is_pr_merge_command("gh pr merge --squash 123")

    def test_non_merge_command(self):
        """マージ以外のコマンドは検出しない"""
        assert not similar_pattern_search.is_pr_merge_command("gh pr view 123")


class TestExtractPrNumber:
    """Test cases for extract_pr_number function."""

    def test_with_number(self):
        """コマンドからPR番号を抽出"""
        result = similar_pattern_search.extract_pr_number("gh pr merge 123")
        assert result == 123

    def test_with_hash(self):
        """#付きPR番号を抽出"""
        result = similar_pattern_search.extract_pr_number("gh pr merge #456")
        assert result == 456

    @patch("subprocess.run")
    def test_without_number_and_gh_fails(self, mock_run):
        """コマンドにPR番号がなくgh pr viewも失敗した場合"""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = similar_pattern_search.extract_pr_number("gh pr merge --squash")
        assert result is None


class TestExtractFunctionPatterns:
    """Test cases for extract_function_patterns function."""

    def test_basic_extraction(self):
        """基本的な関数呼び出しを抽出"""
        diff = """
+json.dumps(data)
+subprocess.run(cmd)
"""
        patterns = similar_pattern_search.extract_function_patterns(diff)
        assert "json.dumps" in patterns
        assert "subprocess.run" in patterns

    def test_ignores_common_functions(self):
        """一般的な関数は除外"""
        diff = """
+print(x)
+len(arr)
+str(num)
"""
        patterns = similar_pattern_search.extract_function_patterns(diff)
        assert "print" not in patterns
        assert "len" not in patterns
        assert "str" not in patterns

    def test_ignores_deleted_lines(self):
        """削除行は無視"""
        diff = """
-json.dumps(old_data)
+new_function(data)
"""
        patterns = similar_pattern_search.extract_function_patterns(diff)
        assert "json.dumps" not in patterns
        assert "new_function" in patterns

    def test_ignores_diff_headers(self):
        """差分ヘッダーは無視（unified diff形式の+++行）"""
        # unified diff形式: +++はファイルヘッダーであり、内容行ではない
        diff = """\
+++ b/file.py
+json.dumps(data)
"""
        patterns = similar_pattern_search.extract_function_patterns(diff)
        # +++ line should be ignored (starts with +++ not just +)
        assert len(patterns) == 1
        assert "json.dumps" in patterns

    def test_method_calls(self):
        """メソッド呼び出しを抽出"""
        diff = """
+result.to_json()
+data.encode()
"""
        patterns = similar_pattern_search.extract_function_patterns(diff)
        assert "result.to_json" in patterns

    def test_ignores_function_definitions(self):
        """関数定義は除外し、関数呼び出しのみを抽出"""
        diff = """\
+def my_function():
+    return json.dumps(data)
+class MyClass():
+    pass
"""
        patterns = similar_pattern_search.extract_function_patterns(diff)
        assert "my_function" not in patterns
        assert "MyClass" not in patterns
        assert "json.dumps" in patterns


class TestSearchPatternInCodebase:
    """Test cases for search_pattern_in_codebase function."""

    @patch("subprocess.run")
    def test_successful_search(self, mock_run):
        """成功した検索"""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="file.py:10:json.dumps(data)\nother.py:20:json.dumps(config)",
        )

        results = similar_pattern_search.search_pattern_in_codebase("json.dumps", ["changed.py"])
        assert len(results) == 2
        assert results[0]["file"] == "file.py"
        assert results[0]["line"] == "10"

    @patch("subprocess.run")
    def test_no_results(self, mock_run):
        """結果なし"""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        results = similar_pattern_search.search_pattern_in_codebase("unique_function", [])
        assert results == []


class TestFormatInfoMessage:
    """Test cases for format_info_message function."""

    def test_formats_results(self):
        """結果をフォーマット"""
        pattern_results = {
            "json.dumps": [
                {"file": "file.py", "line": "10", "content": "json.dumps(data)"},
            ],
        }
        message = similar_pattern_search.format_info_message(pattern_results)

        assert "修正漏れの可能性" in message
        assert "json.dumps" in message
        assert "file.py:10" in message


@patch.object(similar_pattern_search, "search_pattern_in_codebase")
@patch.object(similar_pattern_search, "extract_function_patterns")
@patch.object(similar_pattern_search, "get_changed_files")
@patch.object(similar_pattern_search, "get_pr_diff")
@patch.object(similar_pattern_search, "extract_pr_number")
@patch.object(similar_pattern_search, "is_merge_success")
class TestMain:
    """Test cases for main function."""

    def test_non_bash_tool(
        self,
        mock_merge_success,
        mock_extract_pr,
        mock_diff,
        mock_files,
        mock_patterns,
        mock_search,
        monkeypatch,
    ):
        """Bash以外のツールはスキップ"""
        result = run_hook("Read", {"file_path": "/some/file"}, monkeypatch=monkeypatch)

        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_non_merge_command(
        self,
        mock_merge_success,
        mock_extract_pr,
        mock_diff,
        mock_files,
        mock_patterns,
        mock_search,
        monkeypatch,
    ):
        """gh pr merge 以外のコマンドはスキップ"""
        result = run_hook("Bash", {"command": "git status"}, monkeypatch=monkeypatch)

        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_merge_failure(
        self,
        mock_merge_success,
        mock_extract_pr,
        mock_diff,
        mock_files,
        mock_patterns,
        mock_search,
        monkeypatch,
    ):
        """マージ失敗時はスキップ"""
        mock_merge_success.return_value = False

        result = run_hook(
            "Bash",
            {"command": "gh pr merge 123"},
            tool_output="merge failed",
            exit_code=1,
            monkeypatch=monkeypatch,
        )

        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_similar_patterns_found(
        self,
        mock_merge_success,
        mock_extract_pr,
        mock_diff,
        mock_files,
        mock_patterns,
        mock_search,
        monkeypatch,
    ):
        """類似パターンがある場合は通知"""
        mock_merge_success.return_value = True
        mock_extract_pr.return_value = 100
        mock_diff.return_value = "+json.dumps(data)"
        mock_files.return_value = ["changed.py"]
        mock_patterns.return_value = {"json.dumps"}
        mock_search.return_value = [
            {"file": "other.py", "line": "20", "content": "json.dumps(old)"},
        ]

        result = run_hook(
            "Bash",
            {"command": "gh pr merge 100"},
            tool_output="Merged",
            exit_code=0,
            monkeypatch=monkeypatch,
        )

        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "修正漏れ" in result["systemMessage"]

    def test_no_similar_patterns(
        self,
        mock_merge_success,
        mock_extract_pr,
        mock_diff,
        mock_files,
        mock_patterns,
        mock_search,
        monkeypatch,
    ):
        """類似パターンがない場合は通知なし"""
        mock_merge_success.return_value = True
        mock_extract_pr.return_value = 100
        mock_diff.return_value = "+unique_function(data)"
        mock_files.return_value = ["changed.py"]
        mock_patterns.return_value = {"unique_function"}
        mock_search.return_value = []

        result = run_hook(
            "Bash",
            {"command": "gh pr merge 100"},
            tool_output="Merged",
            exit_code=0,
            monkeypatch=monkeypatch,
        )

        assert result["decision"] == "approve"
        assert "systemMessage" not in result
