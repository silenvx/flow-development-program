"""Tests for commit-message-template.py hook."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

# Load the hook module dynamically (it has hyphens in the name)
HOOK_PATH = Path(__file__).parent.parent / "commit-message-template.py"


@pytest.fixture
def hook_module():
    """Load the hook module."""
    spec = importlib.util.spec_from_file_location("commit_message_template", str(HOOK_PATH))
    module = importlib.util.module_from_spec(spec)
    # Add hooks directory to path for common imports
    hooks_dir = str(Path(__file__).parent.parent)
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    spec.loader.exec_module(module)
    return module


class TestShouldSkipTemplate:
    """Tests for should_skip_template function."""

    def test_skip_when_source_is_message(self, hook_module):
        """git commit -m 時はスキップ"""
        assert hook_module.should_skip_template("message")

    def test_skip_when_source_is_merge(self, hook_module):
        """マージ時はスキップ"""
        assert hook_module.should_skip_template("merge")

    def test_skip_when_source_is_squash(self, hook_module):
        """squash時はスキップ"""
        assert hook_module.should_skip_template("squash")

    def test_skip_when_source_is_commit(self, hook_module):
        """amend時（commit）はスキップ"""
        assert hook_module.should_skip_template("commit")

    def test_skip_when_source_is_template(self, hook_module):
        """template指定時はスキップ（既存テンプレートを尊重）"""
        assert hook_module.should_skip_template("template")

    def test_no_skip_when_source_is_empty(self, hook_module):
        """ソースなし（通常のコミット）はスキップしない"""
        assert not hook_module.should_skip_template("")

    def test_no_skip_when_source_is_none(self, hook_module):
        """ソースがNoneの場合もスキップしない"""
        assert not hook_module.should_skip_template(None)


class TestHasUserContent:
    """Tests for has_user_content function."""

    def test_empty_file_has_no_content(self, hook_module):
        """空のファイルはユーザーコンテンツなし"""
        assert not hook_module.has_user_content("")

    def test_only_comments_has_no_content(self, hook_module):
        """コメントのみはユーザーコンテンツなし"""
        content = """# This is a comment
# Another comment
"""
        assert not hook_module.has_user_content(content)

    def test_only_whitespace_has_no_content(self, hook_module):
        """空白のみはユーザーコンテンツなし"""
        assert not hook_module.has_user_content("   \n\n   \n")

    def test_actual_message_has_content(self, hook_module):
        """実際のメッセージがあればコンテンツあり"""
        content = """fix: something

# comment
"""
        assert hook_module.has_user_content(content)

    def test_message_with_comments_has_content(self, hook_module):
        """コメントと混在してもメッセージがあればコンテンツあり"""
        content = """# comment
feat: add feature
# another comment
"""
        assert hook_module.has_user_content(content)


class TestGetTemplate:
    """Tests for get_template function."""

    def test_template_contains_why_section(self, hook_module):
        """テンプレートに「なぜ」セクションが含まれる"""
        template = hook_module.get_template()
        assert "なぜ" in template

    def test_template_contains_what_section(self, hook_module):
        """テンプレートに「何を」セクションが含まれる"""
        template = hook_module.get_template()
        assert "何を" in template

    def test_template_contains_refs_section(self, hook_module):
        """テンプレートに「参照」セクションが含まれる"""
        template = hook_module.get_template()
        assert "参照" in template

    def test_template_lines_are_comments(self, hook_module):
        """テンプレートの行はコメント形式"""
        template = hook_module.get_template()
        for line in template.strip().split("\n"):
            if line.strip():  # 空行以外
                assert line.startswith("#"), f"Line should be comment: {line}"


class TestInsertTemplate:
    """Tests for insert_template function."""

    def test_insert_into_empty_file(self, hook_module):
        """空ファイルにテンプレートを挿入"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("")
            f.flush()
            filepath = f.name

        hook_module.insert_template(filepath)

        content = Path(filepath).read_text()
        assert "なぜ" in content
        Path(filepath).unlink()

    def test_insert_preserves_existing_comments(self, hook_module):
        """既存のGitコメントを保持"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("\n# Please enter the commit message\n# On branch main\n")
            f.flush()
            filepath = f.name

        hook_module.insert_template(filepath)

        content = Path(filepath).read_text()
        assert "なぜ" in content
        assert "Please enter the commit message" in content
        Path(filepath).unlink()

    def test_no_insert_when_content_exists(self, hook_module):
        """既にユーザーコンテンツがある場合は挿入しない"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            original = "fix: existing message\n"
            f.write(original)
            f.flush()
            filepath = f.name

        hook_module.insert_template(filepath)

        content = Path(filepath).read_text()
        # テンプレートは挿入されない
        assert "なぜ" not in content
        assert content == original
        Path(filepath).unlink()

    def test_insert_handles_nonexistent_file(self, hook_module):
        """存在しないファイルでもエラーにならない"""
        nonexistent = "/tmp/nonexistent_commit_msg_test_12345.txt"
        # Should not raise
        hook_module.insert_template(nonexistent)
        # File should be created with template
        path = Path(nonexistent)
        if path.exists():
            content = path.read_text()
            assert "なぜ" in content
            path.unlink()


class TestMain:
    """Tests for main function (integration tests)."""

    def test_main_no_args(self, hook_module, monkeypatch):
        """引数なしで正常終了"""
        monkeypatch.setattr(sys, "argv", ["commit-message-template.py"])
        result = hook_module.main()
        assert result == 0

    def test_main_with_file_arg(self, hook_module, monkeypatch):
        """ファイル引数1つで正常動作"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("")
            f.flush()
            filepath = f.name

        monkeypatch.setattr(sys, "argv", ["commit-message-template.py", filepath])
        result = hook_module.main()

        assert result == 0
        content = Path(filepath).read_text()
        assert "なぜ" in content
        Path(filepath).unlink()

    def test_main_with_message_source(self, hook_module, monkeypatch):
        """messageソースでスキップ"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("")
            f.flush()
            filepath = f.name

        monkeypatch.setattr(sys, "argv", ["commit-message-template.py", filepath, "message"])
        result = hook_module.main()

        assert result == 0
        content = Path(filepath).read_text()
        # message source -> skip template
        assert "なぜ" not in content
        Path(filepath).unlink()

    def test_main_with_template_source(self, hook_module, monkeypatch):
        """templateソースでスキップ"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("# Existing template\n")
            f.flush()
            filepath = f.name

        monkeypatch.setattr(sys, "argv", ["commit-message-template.py", filepath, "template"])
        result = hook_module.main()

        assert result == 0
        content = Path(filepath).read_text()
        # template source -> skip, preserve existing
        assert "なぜ" not in content
        assert "Existing template" in content
        Path(filepath).unlink()
