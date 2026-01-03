"""issue-scope-check.py のテスト"""

import json
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from unittest.mock import MagicMock, patch


def run_hook(command: str) -> tuple[int, str, str]:
    """フックを実行して結果を返す"""
    hook_path = Path(__file__).parent.parent / "issue-scope-check.py"
    stdin_data = json.dumps({"tool_input": {"command": command}})
    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=stdin_data,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


class TestIssueScopeCheck:
    """Issue編集時のスコープ確認フックのテスト"""

    def test_blocks_on_issue_edit_with_body(self):
        """gh issue edit --body でブロックする (Issue #2240)"""
        returncode, stdout, _ = run_hook('gh issue edit 123 --body "new content"')
        assert returncode == 2
        output = json.loads(stdout)
        assert output["decision"] == "block"
        assert "スコープ確認" in output["reason"]

    def test_ignores_issue_edit_without_body(self):
        """--body なしの gh issue edit は無視する"""
        _, stdout, _ = run_hook("gh issue edit 123 --add-label bug")
        assert stdout == ""

    def test_ignores_other_commands(self):
        """他のコマンドは無視する"""
        _, stdout, _ = run_hook("gh pr edit 123 --body test")
        assert stdout == ""

    def test_ignores_issue_create(self):
        """gh issue create は無視する（別フックで処理）"""
        _, stdout, _ = run_hook('gh issue create --body "test"')
        assert stdout == ""


class TestIsCheckboxOnlyChange:
    """is_checkbox_only_change 関数のテスト (Issue #2423)."""

    @staticmethod
    def _get_function():
        """関数をインポート."""
        import importlib.util

        hooks_dir = Path(__file__).parent.parent
        spec = importlib.util.spec_from_file_location(
            "issue_scope_check", hooks_dir / "issue-scope-check.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.is_checkbox_only_change

    def test_identical_bodies_returns_true(self):
        """同一内容の場合もTrue（変更なしのため許可）."""
        func = self._get_function()
        body = "- [ ] Task 1\n- [x] Task 2"
        assert func(body, body) is True

    def test_checkbox_unchecked_to_checked(self):
        """未チェック→チェックはTrue."""
        func = self._get_function()
        old = "- [ ] Task 1"
        new = "- [x] Task 1"
        assert func(old, new) is True

    def test_checkbox_checked_to_unchecked(self):
        """チェック→未チェックはTrue."""
        func = self._get_function()
        old = "- [x] Task 1"
        new = "- [ ] Task 1"
        assert func(old, new) is True

    def test_checkbox_uppercase_x(self):
        """大文字Xもサポート."""
        func = self._get_function()
        old = "- [ ] Task 1"
        new = "- [X] Task 1"
        assert func(old, new) is True

    def test_multiple_checkbox_changes(self):
        """複数チェックボックスの変更はTrue."""
        func = self._get_function()
        old = "- [ ] Task 1\n- [x] Task 2\n- [ ] Task 3"
        new = "- [x] Task 1\n- [ ] Task 2\n- [x] Task 3"
        assert func(old, new) is True

    def test_text_content_change_returns_false(self):
        """テキスト内容変更はFalse."""
        func = self._get_function()
        old = "- [ ] Task 1"
        new = "- [ ] Task 2"
        assert func(old, new) is False

    def test_line_added_returns_false(self):
        """行追加はFalse."""
        func = self._get_function()
        old = "- [ ] Task 1"
        new = "- [ ] Task 1\n- [ ] Task 2"
        assert func(old, new) is False

    def test_line_removed_returns_false(self):
        """行削除はFalse."""
        func = self._get_function()
        old = "- [ ] Task 1\n- [ ] Task 2"
        new = "- [ ] Task 1"
        assert func(old, new) is False

    def test_none_old_body_returns_false(self):
        """old_bodyがNoneはFalse."""
        func = self._get_function()
        assert func(None, "- [ ] Task") is False

    def test_none_new_body_returns_false(self):
        """new_bodyがNoneはFalse."""
        func = self._get_function()
        assert func("- [ ] Task", None) is False

    def test_mixed_content_with_checkbox_change_only(self):
        """混合コンテンツでチェックボックスのみ変更はTrue."""
        func = self._get_function()
        old = "## Title\n\n- [ ] Task 1\n- [x] Task 2\n\nSome text"
        new = "## Title\n\n- [x] Task 1\n- [ ] Task 2\n\nSome text"
        assert func(old, new) is True

    def test_mixed_content_with_text_change(self):
        """混合コンテンツでテキスト変更はFalse."""
        func = self._get_function()
        old = "## Title\n\n- [ ] Task 1"
        new = "## Title Changed\n\n- [ ] Task 1"
        assert func(old, new) is False

    def test_indented_checkbox(self):
        """インデント付きチェックボックス."""
        func = self._get_function()
        old = "  - [ ] Subtask"
        new = "  - [x] Subtask"
        assert func(old, new) is True

    def test_different_indent_returns_false(self):
        """インデント変更はFalse."""
        func = self._get_function()
        old = "- [ ] Task"
        new = "  - [ ] Task"
        assert func(old, new) is False

    def test_empty_old_body_returns_false(self):
        """old_bodyが空文字列はFalse."""
        func = self._get_function()
        assert func("", "- [ ] Task") is False

    def test_empty_new_body_returns_false(self):
        """new_bodyが空文字列はFalse."""
        func = self._get_function()
        assert func("- [ ] Task", "") is False

    def test_both_empty_bodies_returns_false(self):
        """両方が空文字列はFalse."""
        func = self._get_function()
        assert func("", "") is False

    def test_trailing_newlines_handled_correctly(self):
        """末尾の改行の扱いを確認."""
        func = self._get_function()
        old = "- [ ] Task 1\n"
        new = "- [x] Task 1\n"
        assert func(old, new) is True

    def test_asterisk_list_marker(self):
        """* リストマーカーをサポート."""
        func = self._get_function()
        old = "* [ ] Task 1"
        new = "* [x] Task 1"
        assert func(old, new) is True

    def test_plus_list_marker(self):
        """+ リストマーカーをサポート."""
        func = self._get_function()
        old = "+ [ ] Task 1"
        new = "+ [x] Task 1"
        assert func(old, new) is True

    def test_mixed_list_markers_returns_false(self):
        """異なるリストマーカーは変更とみなす."""
        func = self._get_function()
        old = "- [ ] Task 1"
        new = "* [ ] Task 1"
        assert func(old, new) is False


class TestSkipEnvSupport:
    """SKIP_ISSUE_SCOPE_CHECK環境変数のテスト (Issue #2431)."""

    def test_skip_env_exported(self):
        """エクスポートされたSKIP環境変数で許可される."""
        hook_path = Path(__file__).parent.parent / "issue-scope-check.py"
        stdin_data = json.dumps(
            {"tool_input": {"command": 'gh issue edit 123 --body "new content"'}}
        )
        env = os.environ.copy()
        env["SKIP_ISSUE_SCOPE_CHECK"] = "1"
        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input=stdin_data,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["decision"] == "approve"

    def test_skip_env_inline(self):
        """インラインSKIP環境変数で許可される."""
        hook_path = Path(__file__).parent.parent / "issue-scope-check.py"
        stdin_data = json.dumps(
            {
                "tool_input": {
                    "command": 'SKIP_ISSUE_SCOPE_CHECK=1 gh issue edit 123 --body "new content"'
                }
            }
        )
        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input=stdin_data,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["decision"] == "approve"

    def test_skip_env_false_does_not_skip(self):
        """SKIP=0 ではスキップされない."""
        hook_path = Path(__file__).parent.parent / "issue-scope-check.py"
        stdin_data = json.dumps(
            {"tool_input": {"command": 'gh issue edit 123 --body "new content"'}}
        )
        env = os.environ.copy()
        env["SKIP_ISSUE_SCOPE_CHECK"] = "0"
        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input=stdin_data,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 2
        output = json.loads(result.stdout)
        assert output["decision"] == "block"


class TestBlockMessage:
    """改善されたブロックメッセージのテスト (Issue #2431)."""

    def test_block_message_includes_skip_instruction(self):
        """ブロックメッセージにスキップ方法が含まれる."""
        returncode, stdout, _ = run_hook('gh issue edit 456 --body "new content"')
        assert returncode == 2
        output = json.loads(stdout)
        assert "SKIP_ISSUE_SCOPE_CHECK=1" in output["reason"]
        assert "456" in output["reason"]

    def test_block_message_mentions_checkbox_auto_allow(self):
        """ブロックメッセージにチェックボックス自動許可の説明が含まれる."""
        returncode, stdout, _ = run_hook('gh issue edit 789 --body "new content"')
        assert returncode == 2
        output = json.loads(stdout)
        assert "チェックボックス" in output["reason"]


@lru_cache(maxsize=1)
def _import_functions():
    """テスト対象の関数をインポート.

    lru_cacheにより、モジュールは一度だけインポートされキャッシュされる。
    """
    import importlib.util

    hooks_dir = Path(__file__).parent.parent
    spec = importlib.util.spec_from_file_location(
        "issue_scope_check", hooks_dir / "issue-scope-check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestExtractIssueNumber:
    """extract_issue_number 関数のテスト (Issue #2428)."""

    @staticmethod
    def _get_function():
        """関数をインポート."""
        return _import_functions().extract_issue_number

    def test_basic_issue_number(self):
        """基本的なIssue番号の抽出."""
        func = self._get_function()
        assert func('gh issue edit 123 --body "content"') == "123"

    def test_issue_number_with_hash(self):
        """#付きIssue番号の抽出."""
        func = self._get_function()
        assert func('gh issue edit #456 --body "content"') == "456"

    def test_issue_number_with_short_body_option(self):
        """-b オプションでもIssue番号を抽出."""
        func = self._get_function()
        assert func('gh issue edit 789 -b "content"') == "789"

    def test_no_issue_number_returns_none(self):
        """Issue番号がない場合はNone."""
        func = self._get_function()
        assert func("gh pr edit 123 --body content") is None

    def test_issue_view_command(self):
        """gh issue view は対象外."""
        func = self._get_function()
        assert func("gh issue view 123") is None

    def test_issue_number_multiple_digits(self):
        """複数桁のIssue番号."""
        func = self._get_function()
        assert func('gh issue edit 12345 --body "content"') == "12345"

    def test_whitespace_variations(self):
        """空白のバリエーション."""
        func = self._get_function()
        assert func("gh  issue  edit  999  --body  'content'") == "999"


class TestExtractBodyFromCommand:
    """extract_body_from_command 関数のテスト (Issue #2428)."""

    @staticmethod
    def _get_function():
        """関数をインポート."""
        return _import_functions().extract_body_from_command

    def test_double_quoted_body(self):
        """ダブルクォートで囲まれたbody."""
        func = self._get_function()
        assert func('gh issue edit 123 --body "simple content"') == "simple content"

    def test_single_quoted_body(self):
        """シングルクォートで囲まれたbody."""
        func = self._get_function()
        assert func("gh issue edit 123 --body 'simple content'") == "simple content"

    def test_multiline_body(self):
        """複数行のbody."""
        func = self._get_function()
        command = 'gh issue edit 123 --body "line1\nline2\nline3"'
        result = func(command)
        assert result == "line1\nline2\nline3"

    def test_heredoc_simple(self):
        """シンプルなheredocパターン."""
        func = self._get_function()
        command = """gh issue edit 123 --body "$(cat <<'EOF'
content here
EOF
)"""
        result = func(command)
        assert result == "content here"

    def test_heredoc_multiline(self):
        """複数行heredocパターン."""
        func = self._get_function()
        command = """gh issue edit 123 --body "$(cat <<'EOF'
line1
line2
line3
EOF
)"""
        result = func(command)
        assert result == "line1\nline2\nline3"

    def test_heredoc_with_quotes(self):
        """クォートを含むheredocパターン."""
        func = self._get_function()
        command = """gh issue edit 123 --body "$(cat <<'EOF'
content with "quotes" inside
EOF
)"""
        result = func(command)
        assert result == 'content with "quotes" inside'

    def test_no_body_option_returns_none(self):
        """--body オプションがない場合はNone."""
        func = self._get_function()
        assert func("gh issue edit 123 --add-label bug") is None

    def test_empty_body(self):
        """空のbody."""
        func = self._get_function()
        assert func('gh issue edit 123 --body ""') == ""

    def test_body_with_special_characters(self):
        """特殊文字を含むbody."""
        func = self._get_function()
        result = func('gh issue edit 123 --body "content with $variable and `command`"')
        assert result == "content with $variable and `command`"

    def test_heredoc_without_quotes_around_eof(self):
        """EOFにクォートなしのheredoc."""
        func = self._get_function()
        command = """gh issue edit 123 --body "$(cat <<EOF
content here
EOF
)"""
        result = func(command)
        assert result == "content here"

    def test_heredoc_eof_with_double_quotes(self):
        """EOFにダブルクォートのheredoc."""
        func = self._get_function()
        command = '''gh issue edit 123 --body "$(cat <<"EOF"
content here
EOF
)"'''
        result = func(command)
        assert result == "content here"


class TestGetCurrentIssueBody:
    """get_current_issue_body 関数のテスト (Issue #2428)."""

    @staticmethod
    def _get_function():
        """関数をインポート."""
        return _import_functions().get_current_issue_body

    def test_successful_api_call(self):
        """成功するAPIコール."""
        func = self._get_function()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Issue body content\n",
                stderr="",
            )
            result = func("123")
            assert result == "Issue body content"
            mock_run.assert_called_once_with(
                ["gh", "issue", "view", "123", "--json", "body", "--jq", ".body"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

    def test_failed_api_call_returns_none(self):
        """失敗するAPIコールはNoneを返す."""
        func = self._get_function()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="error",
            )
            result = func("999")
            assert result is None

    def test_timeout_returns_none(self):
        """タイムアウトはNoneを返す."""
        func = self._get_function()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="gh", timeout=10)
            result = func("123")
            assert result is None

    def test_file_not_found_returns_none(self):
        """ghコマンドが見つからない場合はNoneを返す."""
        func = self._get_function()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            result = func("123")
            assert result is None

    def test_empty_body_returned(self):
        """空のbodyが返された場合."""
        func = self._get_function()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="\n",
                stderr="",
            )
            result = func("123")
            assert result == ""

    def test_multiline_body(self):
        """複数行のbody."""
        func = self._get_function()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="line1\nline2\nline3\n",
                stderr="",
            )
            result = func("123")
            assert result == "line1\nline2\nline3"


class TestForkSessionSkipBlock:
    """forkセッションでSKIP_ISSUE_SCOPE_CHECKがブロックされるテスト (Issue #2458).

    forkセッションの検出には一時的なトランスクリプトファイルを使用する。
    is_fork_sessionはsubprocessで実行されるため、patchが適用されないため。
    """

    @staticmethod
    def _create_fork_transcript(
        temp_dir: Path, parent_session_id: str, current_session_id: str
    ) -> Path:
        """forkセッション用のトランスクリプトファイルを作成する.

        forkセッションは、トランスクリプトファイル名（current_session_id）と
        フック入力のsession_id（parent_session_id）が異なる場合に検出される。
        または、トランスクリプト内に親セッションのsessionIdが含まれる場合。
        """
        # トランスクリプトファイル名は現在のsession_idを使用
        transcript_file = temp_dir / f"{current_session_id}.jsonl"
        # トランスクリプト内に親セッションのsessionIdを含む
        content = (
            f'{{"type": "user", "sessionId": "{parent_session_id}", "message": "test"}}\n'
            f'{{"type": "assistant", "sessionId": "{current_session_id}", "message": "ok"}}\n'
        )
        transcript_file.write_text(content)
        return transcript_file

    def test_fork_session_blocks_skip_env_exported(self, tmp_path):
        """forkセッションではエクスポートされたSKIP環境変数がブロックされる."""
        hook_path = Path(__file__).parent.parent / "issue-scope-check.py"

        # 親セッションIDと現在のセッションID（UUIDフォーマット）
        parent_session_id = "11111111-1111-1111-1111-111111111111"
        current_session_id = "22222222-2222-2222-2222-222222222222"

        # forkセッション用のトランスクリプトファイルを作成
        transcript_file = self._create_fork_transcript(
            tmp_path, parent_session_id, current_session_id
        )

        stdin_data = json.dumps(
            {
                "tool_input": {"command": 'gh issue edit 123 --body "new content"'},
                "session_id": parent_session_id,  # フックには親のsession_idが渡される
                "source": "resume",
                "transcript_path": str(transcript_file),
            }
        )
        env = os.environ.copy()
        env["SKIP_ISSUE_SCOPE_CHECK"] = "1"
        # tmp_pathがプロジェクトディレクトリ外のため、許可パスに追加
        env["CLAUDE_PROJECT_DIR"] = str(tmp_path)

        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input=stdin_data,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
            check=False,
        )

        # forkセッションではSKIPがブロックされ、exit 0で終了
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["decision"] == "block", f"stdout: {result.stdout}, stderr: {result.stderr}"
        assert "forkセッション" in output["reason"]
        assert "SKIP不可" in output["reason"]

    def test_fork_session_blocks_skip_env_inline(self, tmp_path):
        """forkセッションではインラインSKIP環境変数がブロックされる."""
        hook_path = Path(__file__).parent.parent / "issue-scope-check.py"

        parent_session_id = "33333333-3333-3333-3333-333333333333"
        current_session_id = "44444444-4444-4444-4444-444444444444"

        transcript_file = self._create_fork_transcript(
            tmp_path, parent_session_id, current_session_id
        )

        stdin_data = json.dumps(
            {
                "tool_input": {
                    "command": 'SKIP_ISSUE_SCOPE_CHECK=1 gh issue edit 123 --body "new content"'
                },
                "session_id": parent_session_id,
                "source": "resume",
                "transcript_path": str(transcript_file),
            }
        )
        env = os.environ.copy()
        env["CLAUDE_PROJECT_DIR"] = str(tmp_path)

        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input=stdin_data,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
            check=False,
        )

        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["decision"] == "block", f"stdout: {result.stdout}, stderr: {result.stderr}"
        assert "forkセッション" in output["reason"]

    def test_non_fork_session_allows_skip_env(self, tmp_path):
        """非forkセッションではSKIP環境変数で許可される."""
        hook_path = Path(__file__).parent.parent / "issue-scope-check.py"

        # 非forkセッション: トランスクリプトファイル名とsession_idが一致
        session_id = "55555555-5555-5555-5555-555555555555"
        transcript_file = tmp_path / f"{session_id}.jsonl"
        content = f'{{"type": "user", "sessionId": "{session_id}", "message": "test"}}\n'
        transcript_file.write_text(content)

        stdin_data = json.dumps(
            {
                "tool_input": {"command": 'gh issue edit 123 --body "new content"'},
                "session_id": session_id,
                "source": "resume",
                "transcript_path": str(transcript_file),
            }
        )
        env = os.environ.copy()
        env["SKIP_ISSUE_SCOPE_CHECK"] = "1"
        env["CLAUDE_PROJECT_DIR"] = str(tmp_path)

        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input=stdin_data,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
            check=False,
        )

        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["decision"] == "approve", f"stdout: {result.stdout}, stderr: {result.stderr}"

    def test_fork_session_block_message_suggests_new_issue(self, tmp_path):
        """forkセッションブロック時のメッセージに新Issue作成の案内がある."""
        hook_path = Path(__file__).parent.parent / "issue-scope-check.py"

        parent_session_id = "66666666-6666-6666-6666-666666666666"
        current_session_id = "77777777-7777-7777-7777-777777777777"

        transcript_file = self._create_fork_transcript(
            tmp_path, parent_session_id, current_session_id
        )

        stdin_data = json.dumps(
            {
                "tool_input": {"command": 'gh issue edit 456 --body "content"'},
                "session_id": parent_session_id,
                "source": "resume",
                "transcript_path": str(transcript_file),
            }
        )
        env = os.environ.copy()
        env["SKIP_ISSUE_SCOPE_CHECK"] = "1"
        env["CLAUDE_PROJECT_DIR"] = str(tmp_path)

        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input=stdin_data,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
            check=False,
        )

        output = json.loads(result.stdout)
        assert output["decision"] == "block", f"stdout: {result.stdout}, stderr: {result.stderr}"
        assert "gh issue create" in output["reason"]
        assert "新しいIssue" in output["reason"]

    def test_non_fork_session_allows_skip_env_inline(self, tmp_path):
        """非forkセッションではインラインSKIP環境変数で許可される."""
        hook_path = Path(__file__).parent.parent / "issue-scope-check.py"

        # 非forkセッション: トランスクリプトファイル名とsession_idが一致
        session_id = "88888888-8888-8888-8888-888888888888"
        transcript_file = tmp_path / f"{session_id}.jsonl"
        content = f'{{"type": "user", "sessionId": "{session_id}", "message": "test"}}\n'
        transcript_file.write_text(content)

        stdin_data = json.dumps(
            {
                "tool_input": {
                    "command": 'SKIP_ISSUE_SCOPE_CHECK=1 gh issue edit 123 --body "new content"'
                },
                "session_id": session_id,
                "source": "resume",
                "transcript_path": str(transcript_file),
            }
        )
        env = os.environ.copy()
        env["CLAUDE_PROJECT_DIR"] = str(tmp_path)

        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input=stdin_data,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
            check=False,
        )

        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["decision"] == "approve", f"stdout: {result.stdout}, stderr: {result.stderr}"
