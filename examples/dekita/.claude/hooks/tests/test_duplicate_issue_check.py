"""Tests for duplicate-issue-check.py hook."""

import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

# Load hook module with hyphenated filename
HOOKS_DIR = Path(__file__).parent.parent
HOOK_PATH = HOOKS_DIR / "duplicate-issue-check.py"
spec = importlib.util.spec_from_file_location("duplicate_issue_check", HOOK_PATH)
duplicate_issue_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(duplicate_issue_check)


def run_hook(
    tool_name: str,
    tool_input: dict[str, str],
    monkeypatch,
) -> dict:
    """テスト用ヘルパー: フックを実行し結果を返す

    Args:
        tool_name: ツール名 (e.g., "Bash", "Read")
        tool_input: ツール入力 (e.g., {"command": "gh issue create ..."})
        monkeypatch: pytest monkeypatch fixture

    Returns:
        フックの出力をパースしたdict
    """
    hook_input = {"tool_name": tool_name, "tool_input": tool_input}

    monkeypatch.setattr(
        "sys.stdin", type("stdin", (), {"read": lambda self: json.dumps(hook_input)})()
    )

    captured_output = io.StringIO()
    monkeypatch.setattr("sys.stdout", captured_output)

    duplicate_issue_check.main()

    return json.loads(captured_output.getvalue())


class TestExtractTitleFromCommand:
    """Test cases for extract_title_from_command function."""

    def test_long_option(self):
        """--title オプションからタイトルを抽出"""
        command = 'gh issue create --title "feat: 新機能追加"'
        result = duplicate_issue_check.extract_title_from_command(command)
        assert result == "feat: 新機能追加"

    def test_short_option(self):
        """-t オプションからタイトルを抽出"""
        command = 'gh issue create -t "fix: バグ修正"'
        result = duplicate_issue_check.extract_title_from_command(command)
        assert result == "fix: バグ修正"

    def test_equals_syntax(self):
        """--title= 構文からタイトルを抽出"""
        command = 'gh issue create --title="docs: ドキュメント更新"'
        result = duplicate_issue_check.extract_title_from_command(command)
        assert result == "docs: ドキュメント更新"

    def test_short_equals_syntax(self):
        """-t= 構文からタイトルを抽出"""
        command = 'gh issue create -t="refactor: コード整理"'
        result = duplicate_issue_check.extract_title_from_command(command)
        assert result == "refactor: コード整理"

    def test_no_title(self):
        """タイトルがない場合は None"""
        command = "gh issue create --body 'some body'"
        result = duplicate_issue_check.extract_title_from_command(command)
        assert result is None

    def test_invalid_shlex(self):
        """不正な引用符でも None を返す"""
        command = 'gh issue create --title "unclosed quote'
        result = duplicate_issue_check.extract_title_from_command(command)
        assert result is None


class TestExtractKeywords:
    """Test cases for extract_keywords function."""

    def test_basic_extraction(self):
        """基本的なキーワード抽出"""
        # 日本語は形態素解析なしでは単語分割されないため、連続した文字列として抽出
        title = "feat: セッション再開時の警告表示"
        keywords = duplicate_issue_check.extract_keywords(title)
        assert len(keywords) > 0  # 何らかのキーワードが抽出される
        # プレフィックスは除去される
        assert "feat" not in keywords

    def test_removes_prefix(self):
        """feat:, fix: などのプレフィックスを除去"""
        title = "feat: new feature implementation"
        keywords = duplicate_issue_check.extract_keywords(title)
        assert "feat" not in keywords
        assert "new" in keywords
        assert "feature" in keywords
        assert "implementation" in keywords

    def test_removes_prefix_with_scope(self):
        """feat(scope): などのスコープ付きプレフィックスを除去"""
        title = "feat(hooks): duplicate check validation"
        keywords = duplicate_issue_check.extract_keywords(title)
        assert "feat" not in keywords
        assert "hooks" not in keywords  # スコープも除去される
        assert "duplicate" in keywords
        assert "check" in keywords
        assert "validation" in keywords

    def test_removes_action_verbs(self):
        """アクション動詞（add, update など）もストップワードとして除去"""
        title = "add new validation remove old code"
        keywords = duplicate_issue_check.extract_keywords(title)
        assert "add" not in keywords  # アクション動詞
        assert "remove" not in keywords  # アクション動詞
        assert "new" in keywords
        assert "validation" in keywords
        assert "old" in keywords
        assert "code" in keywords

    def test_removes_stop_words(self):
        """ストップワードを除去"""
        title = "the quick brown fox"
        keywords = duplicate_issue_check.extract_keywords(title)
        assert "the" not in keywords
        assert "quick" in keywords
        assert "brown" in keywords
        assert "fox" in keywords

    def test_removes_short_tokens(self):
        """短すぎるトークンを除去"""
        title = "a b cd efg"
        keywords = duplicate_issue_check.extract_keywords(title)
        assert "a" not in keywords
        assert "b" not in keywords
        assert "cd" in keywords
        assert "efg" in keywords

    def test_max_keywords(self):
        """最大5つのキーワードを返す"""
        title = "one two three four five six seven"
        keywords = duplicate_issue_check.extract_keywords(title)
        assert len(keywords) <= 5

    def test_empty_title(self):
        """空のタイトルは空リストを返す"""
        keywords = duplicate_issue_check.extract_keywords("")
        assert keywords == []

    def test_japanese_keywords(self):
        """日本語キーワードも抽出"""
        title = "重複チェック強化"
        keywords = duplicate_issue_check.extract_keywords(title)
        assert len(keywords) > 0


class TestSearchSimilarIssues:
    """Test cases for search_similar_issues function."""

    def test_no_keywords_returns_empty(self):
        """キーワードなしは空リストを返す"""
        result = duplicate_issue_check.search_similar_issues([])
        assert result == []

    @patch("subprocess.run")
    def test_successful_search(self, mock_run):
        """成功した検索"""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                [
                    {"number": 123, "title": "feat: 類似Issue"},
                    {"number": 456, "title": "fix: 別の類似Issue"},
                ]
            ),
        )

        result = duplicate_issue_check.search_similar_issues(["類似", "Issue"])
        assert len(result) == 2
        assert result[0]["number"] == 123

    @patch("subprocess.run")
    def test_search_failure(self, mock_run):
        """検索失敗は空リストを返す"""
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        result = duplicate_issue_check.search_similar_issues(["キーワード"])
        assert result == []


class TestFormatWarningMessage:
    """Test cases for format_warning_message function."""

    def test_formats_issues(self):
        """類似Issueをフォーマット"""
        issues = [
            {"number": 123, "title": "feat: 類似Issue"},
            {"number": 456, "title": "fix: 別の類似Issue"},
        ]
        message = duplicate_issue_check.format_warning_message(issues)

        assert "類似Issueが存在する可能性" in message
        assert "#123" in message
        assert "#456" in message
        assert "重複でないことを確認" in message

    def test_truncates_long_title(self):
        """長いタイトルは切り詰め"""
        issues = [{"number": 123, "title": "a" * 100}]
        message = duplicate_issue_check.format_warning_message(issues)

        # タイトルが切り詰められていることを確認
        assert "..." in message


@patch.object(duplicate_issue_check, "search_similar_issues")
class TestMain:
    """Test cases for main function."""

    def test_non_bash_tool(self, mock_search, monkeypatch):
        """Bash以外のツールはスキップ"""
        result = run_hook("Read", {"file_path": "/some/file"}, monkeypatch)

        assert result["decision"] == "approve"
        assert "systemMessage" not in result
        mock_search.assert_not_called()

    def test_non_issue_create_command(self, mock_search, monkeypatch):
        """gh issue create 以外のコマンドはスキップ"""
        result = run_hook("Bash", {"command": "git status"}, monkeypatch)

        assert result["decision"] == "approve"
        assert "systemMessage" not in result
        mock_search.assert_not_called()

    def test_false_positive_in_comment(self, mock_search, monkeypatch):
        """コメント内の gh issue create は誤検知しない"""
        result = run_hook("Bash", {"command": "echo 'gh issue create is useful'"}, monkeypatch)

        assert result["decision"] == "approve"
        assert "systemMessage" not in result
        mock_search.assert_not_called()

    def test_chained_command(self, mock_search, monkeypatch):
        """チェーンコマンドでも検出される"""
        mock_search.return_value = [{"number": 999, "title": "類似Issue"}]

        result = run_hook(
            "Bash",
            {"command": 'cd /repo && gh issue create --title "feat: チェーンコマンド"'},
            monkeypatch,
        )

        assert result["decision"] == "approve"
        assert "systemMessage" in result  # 類似Issue警告が表示される

    def test_issue_create_no_title(self, mock_search, monkeypatch):
        """タイトルなしのコマンドはスキップ"""
        result = run_hook("Bash", {"command": "gh issue create --body 'some body'"}, monkeypatch)

        assert result["decision"] == "approve"
        assert "systemMessage" not in result
        mock_search.assert_not_called()

    def test_issue_create_with_similar_issues(self, mock_search, monkeypatch):
        """類似Issueがある場合は警告を表示"""
        mock_search.return_value = [{"number": 123, "title": "feat: 類似Issue"}]

        result = run_hook(
            "Bash",
            {"command": 'gh issue create --title "feat: 新しい類似Issue"'},
            monkeypatch,
        )

        assert result["decision"] == "approve"
        assert "systemMessage" in result
        assert "類似Issue" in result["systemMessage"]

    def test_issue_create_no_similar_issues(self, mock_search, monkeypatch):
        """類似Issueがない場合は警告なし"""
        mock_search.return_value = []

        result = run_hook(
            "Bash",
            {"command": 'gh issue create --title "feat: ユニークなタイトル"'},
            monkeypatch,
        )

        assert result["decision"] == "approve"
        assert "systemMessage" not in result

    def test_pipe_chain_command(self, mock_search, monkeypatch):
        """パイプチェーンコマンドでも検出される（Issue #2008）"""
        mock_search.return_value = [{"number": 999, "title": "類似Issue"}]

        result = run_hook(
            "Bash",
            {"command": 'echo "test" | gh issue create --title "feat: パイプチェーン"'},
            monkeypatch,
        )

        assert result["decision"] == "approve"
        assert "systemMessage" in result  # 類似Issue警告が表示される

    def test_multiple_chain_command(self, mock_search, monkeypatch):
        """複数チェーンコマンドでも検出される（Issue #2008）"""
        mock_search.return_value = [{"number": 999, "title": "類似Issue"}]

        result = run_hook(
            "Bash",
            {"command": 'cd /a && cd /b && gh issue create --title "feat: 複数チェーン"'},
            monkeypatch,
        )

        assert result["decision"] == "approve"
        assert "systemMessage" in result  # 類似Issue警告が表示される

    def test_subshell_command_not_detected(self, mock_search, monkeypatch):
        """サブシェルコマンドは検出しない - shlex制限のため（Issue #2008）

        shlex.split('(gh issue create ...)') は ['(gh', 'issue', 'create', ...] を返す。
        最初のトークンが '(gh' となり、'gh' との照合に失敗するため検出されない。
        これは許容される制限。
        """
        result = run_hook(
            "Bash",
            {"command": '(gh issue create --title "feat: サブシェル")'},
            monkeypatch,
        )

        assert result["decision"] == "approve"
        # サブシェルは検出しない（'(gh' != 'gh' のため）
        assert "systemMessage" not in result
        # search_similar_issuesは呼ばれない（gh issue createとして認識されないため）
        mock_search.assert_not_called()

    def test_variable_expansion_not_detected(self, mock_search, monkeypatch):
        """変数展開は検出しない - シェル依存のため（Issue #2008）

        shlex.splitは変数を展開しないため、$CMDはリテラル文字列'$CMD'として扱われる。
        '$CMD' != 'gh' なので、gh issue createのパターンに一致しない。
        """
        result = run_hook(
            "Bash",
            {"command": 'CMD="gh"; $CMD issue create --title "feat: 変数展開"'},
            monkeypatch,
        )

        # shlex.splitは変数を展開しないため、'$CMD' != 'gh' で検出しない
        assert result["decision"] == "approve"
        assert "systemMessage" not in result  # 警告なし（'$CMD'は'gh'と認識されない）
        # search_similar_issuesは呼ばれない
        mock_search.assert_not_called()

    def test_semicolon_chain_command(self, mock_search, monkeypatch):
        """セミコロンチェーンコマンドでも検出される（Issue #2008）"""
        mock_search.return_value = [{"number": 999, "title": "類似Issue"}]

        result = run_hook(
            "Bash",
            {"command": 'cd /repo; gh issue create --title "feat: セミコロンチェーン"'},
            monkeypatch,
        )

        assert result["decision"] == "approve"
        assert "systemMessage" in result  # 類似Issue警告が表示される
