"""Tests for commit-message-why-check.py hook."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

# Load the hook module dynamically (it has hyphens in the name)
HOOK_PATH = Path(__file__).parent.parent / "commit-message-why-check.py"


@pytest.fixture
def hook_module():
    """Load the hook module."""
    spec = importlib.util.spec_from_file_location("commit_message_why_check", str(HOOK_PATH))
    module = importlib.util.module_from_spec(spec)
    # Add hooks directory to path for common imports
    hooks_dir = str(Path(__file__).parent.parent)
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    spec.loader.exec_module(module)
    return module


class TestGetSubjectLine:
    """Tests for get_subject_line function."""

    def test_simple_subject(self, hook_module):
        """単純なsubject行を取得"""
        content = "fix: something\n\nbody here"
        assert hook_module.get_subject_line(content) == "fix: something"

    def test_subject_with_comments(self, hook_module):
        """コメント付きでsubject行を取得"""
        content = "# comment\nfix: something\n# more comments"
        assert hook_module.get_subject_line(content) == "fix: something"

    def test_empty_content(self, hook_module):
        """空のコンテンツ"""
        assert hook_module.get_subject_line("") == ""

    def test_only_comments(self, hook_module):
        """コメントのみ"""
        assert hook_module.get_subject_line("# comment\n# another") == ""


class TestShouldSkipCheck:
    """Tests for should_skip_check function."""

    def test_skip_merge_commit(self, hook_module):
        """Mergeコミットはスキップ"""
        content = "Merge branch 'feature' into main"
        should_skip, reason = hook_module.should_skip_check(content)
        assert should_skip
        assert "merge" in reason.lower()

    def test_skip_revert_commit(self, hook_module):
        """Revertコミットはスキップ"""
        content = 'Revert "fix: something"'
        should_skip, reason = hook_module.should_skip_check(content)
        assert should_skip
        assert "revert" in reason.lower()

    def test_skip_wip_commit(self, hook_module):
        """WIPコミットはスキップ"""
        content = "WIP: work in progress"
        should_skip, reason = hook_module.should_skip_check(content)
        assert should_skip
        assert "wip" in reason.lower()

    def test_skip_wip_lowercase(self, hook_module):
        """小文字WIPもスキップ"""
        content = "wip save state"
        should_skip, reason = hook_module.should_skip_check(content)
        assert should_skip

    def test_skip_fixup_commit(self, hook_module):
        """fixup!コミットはスキップ"""
        content = "fixup! fix: something"
        should_skip, reason = hook_module.should_skip_check(content)
        assert should_skip
        assert "fixup" in reason.lower()

    def test_skip_squash_commit(self, hook_module):
        """squash!コミットはスキップ"""
        content = "squash! fix: something"
        should_skip, reason = hook_module.should_skip_check(content)
        assert should_skip

    def test_skip_short_subject(self, hook_module):
        """短いsubjectはスキップ"""
        content = "fix"
        should_skip, reason = hook_module.should_skip_check(content)
        assert should_skip
        assert "short" in reason.lower()

    def test_no_skip_normal_commit(self, hook_module):
        """通常のコミットはスキップしない"""
        content = "fix: update the authentication flow"
        should_skip, _ = hook_module.should_skip_check(content)
        assert not should_skip


class TestHasIssueReference:
    """Tests for has_issue_reference function."""

    def test_closes_reference(self, hook_module):
        """Closes #123 を検出"""
        assert hook_module.has_issue_reference("Closes #123")

    def test_fixes_reference(self, hook_module):
        """Fixes #456 を検出"""
        assert hook_module.has_issue_reference("Fixes #456")

    def test_resolves_reference(self, hook_module):
        """Resolves #789 を検出"""
        assert hook_module.has_issue_reference("Resolves #789")

    def test_plain_issue_reference(self, hook_module):
        """単なる #123 を検出"""
        assert hook_module.has_issue_reference("Related to #123")

    def test_case_insensitive(self, hook_module):
        """大文字小文字を区別しない"""
        assert hook_module.has_issue_reference("closes #123")
        assert hook_module.has_issue_reference("FIXES #456")

    def test_no_reference(self, hook_module):
        """Issue参照なし"""
        assert not hook_module.has_issue_reference("Just a message without issue")


class TestHasWhyContext:
    """Tests for has_why_context function."""

    def test_japanese_naze(self, hook_module):
        """「なぜ」を検出"""
        assert hook_module.has_why_context("なぜこの変更が必要か")

    def test_japanese_riyu(self, hook_module):
        """「理由」を検出"""
        assert hook_module.has_why_context("変更の理由")

    def test_japanese_genin(self, hook_module):
        """「原因」を検出"""
        assert hook_module.has_why_context("原因: バグがあった")

    def test_japanese_haikei(self, hook_module):
        """「背景」を検出"""
        assert hook_module.has_why_context("背景として...")

    def test_japanese_tame(self, hook_module):
        """「ため」を検出"""
        assert hook_module.has_why_context("パフォーマンス改善のため")

    def test_japanese_hitsuyou(self, hook_module):
        """「必要」を検出"""
        assert hook_module.has_why_context("この対応が必要だった")

    def test_english_why(self, hook_module):
        """'why' を検出"""
        assert hook_module.has_why_context("This is why we need this")

    def test_english_because(self, hook_module):
        """'because' を検出"""
        assert hook_module.has_why_context("Changed because the old method was slow")

    def test_english_reason(self, hook_module):
        """'reason' を検出"""
        assert hook_module.has_why_context("The reason for this change")

    def test_english_to_fix(self, hook_module):
        """'to fix' を検出"""
        assert hook_module.has_why_context("Updated to fix the bug")

    def test_english_to_prevent(self, hook_module):
        """'to prevent' を検出"""
        assert hook_module.has_why_context("Added check to prevent errors")

    def test_section_header_background(self, hook_module):
        """## Background セクションを検出"""
        assert hook_module.has_why_context("## Background\nSome context here")

    def test_section_header_haikei(self, hook_module):
        """## 背景 セクションを検出"""
        assert hook_module.has_why_context("## 背景\nコンテキスト")

    def test_section_header_summary(self, hook_module):
        """## Summary セクションを検出"""
        assert hook_module.has_why_context("## Summary\nOverview")

    def test_no_context(self, hook_module):
        """コンテキストなし"""
        assert not hook_module.has_why_context("fix: update something\n\nChanged the code")


class TestIsGitComment:
    """Tests for is_git_comment function."""

    def test_empty_hash_is_comment(self, hook_module):
        """単独の#はコメント"""
        assert hook_module.is_git_comment("#")

    def test_hash_space_is_comment(self, hook_module):
        """# スペースで始まる行はコメント"""
        assert hook_module.is_git_comment("# This is a comment")

    def test_double_hash_not_comment(self, hook_module):
        """##で始まる行はコメントではない（Markdownヘッダー）"""
        assert not hook_module.is_git_comment("## Summary")

    def test_triple_hash_not_comment(self, hook_module):
        """###で始まる行はコメントではない"""
        assert not hook_module.is_git_comment("### Details")

    def test_hashtag_not_comment(self, hook_module):
        """#wordはコメントではない（ハッシュタグ）"""
        assert not hook_module.is_git_comment("#tag")

    def test_normal_line_not_comment(self, hook_module):
        """通常の行はコメントではない"""
        assert not hook_module.is_git_comment("normal line")

    def test_empty_line_not_comment(self, hook_module):
        """空行はコメントではない"""
        assert not hook_module.is_git_comment("")


class TestStripComments:
    """Tests for strip_comments function."""

    def test_removes_git_comments(self, hook_module):
        """Gitコメント行を削除"""
        content = "message\n# comment\nmore"
        result = hook_module.strip_comments(content)
        assert "# comment" not in result
        assert "message" in result
        assert "more" in result

    def test_preserves_markdown_headers(self, hook_module):
        """Markdownヘッダーを保持"""
        content = "subject\n\n## Summary\n- item"
        result = hook_module.strip_comments(content)
        assert "## Summary" in result

    def test_preserves_non_comments(self, hook_module):
        """非コメント行を保持"""
        content = "line1\nline2\nline3"
        result = hook_module.strip_comments(content)
        assert "line1" in result
        assert "line2" in result
        assert "line3" in result

    def test_empty_content(self, hook_module):
        """空のコンテンツ"""
        assert hook_module.strip_comments("") == ""

    def test_preserves_hashtags(self, hook_module):
        """ハッシュタグを保持"""
        content = "message with #tag"
        result = hook_module.strip_comments(content)
        assert "#tag" in result


class TestCheckCommitMessage:
    """Tests for check_commit_message function (integration)."""

    def test_valid_with_why_keyword(self, hook_module):
        """「なぜ」キーワードがあれば有効"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("fix: update auth flow\n\nパフォーマンス改善のため変更")
            f.flush()
            filepath = f.name

        is_valid, _ = hook_module.check_commit_message(filepath)
        assert is_valid
        Path(filepath).unlink()

    def test_valid_with_issue_reference(self, hook_module):
        """Issue参照があれば有効"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("fix: update auth flow\n\nCloses #123")
            f.flush()
            filepath = f.name

        is_valid, _ = hook_module.check_commit_message(filepath)
        assert is_valid
        Path(filepath).unlink()

    def test_valid_merge_commit(self, hook_module):
        """Mergeコミットは常に有効"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Merge branch 'feature'")
            f.flush()
            filepath = f.name

        is_valid, _ = hook_module.check_commit_message(filepath)
        assert is_valid
        Path(filepath).unlink()

    def test_invalid_no_context(self, hook_module):
        """コンテキストなしは無効"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("fix: update the code\n\nChanged some files")
            f.flush()
            filepath = f.name

        is_valid, _ = hook_module.check_commit_message(filepath)
        assert not is_valid
        Path(filepath).unlink()

    def test_nonexistent_file_is_valid(self, hook_module):
        """存在しないファイルは有効（fail-open）"""
        is_valid, _ = hook_module.check_commit_message("/nonexistent/path/file.txt")
        assert is_valid

    def test_valid_with_section_header(self, hook_module):
        """セクションヘッダーがあれば有効"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("feat: add feature\n\n## Summary\n- Added new feature")
            f.flush()
            filepath = f.name

        is_valid, _ = hook_module.check_commit_message(filepath)
        assert is_valid
        Path(filepath).unlink()


class TestMain:
    """Tests for main function."""

    def test_main_no_args(self, hook_module, monkeypatch):
        """引数なしで正常終了"""
        monkeypatch.setattr(sys, "argv", ["commit-message-why-check.py"])
        result = hook_module.main()
        assert result == 0

    def test_main_valid_message(self, hook_module, monkeypatch):
        """有効なメッセージで正常終了"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("fix: update auth\n\nこの変更が必要だった理由")
            f.flush()
            filepath = f.name

        monkeypatch.setattr(sys, "argv", ["commit-message-why-check.py", filepath])
        result = hook_module.main()
        assert result == 0
        Path(filepath).unlink()

    def test_main_invalid_message(self, hook_module, monkeypatch, capsys):
        """無効なメッセージでエラー終了"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("fix: update the code\n\nJust changed stuff")
            f.flush()
            filepath = f.name

        monkeypatch.setattr(sys, "argv", ["commit-message-why-check.py", filepath])
        result = hook_module.main()
        assert result == 1

        # Check error message was printed
        captured = capsys.readouterr()
        assert "なぜ" in captured.err
        Path(filepath).unlink()

    def test_main_merge_commit(self, hook_module, monkeypatch):
        """Mergeコミットで正常終了"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Merge branch 'feature' into main")
            f.flush()
            filepath = f.name

        monkeypatch.setattr(sys, "argv", ["commit-message-why-check.py", filepath])
        result = hook_module.main()
        assert result == 0
        Path(filepath).unlink()
