#!/usr/bin/env python3
"""Tests for research-requirement-check.py hook."""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Add hooks directory to path
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


def load_module_with_hyphen(module_name: str, file_name: str):
    """Load a module with hyphen in filename."""
    module_path = Path(__file__).parent.parent / file_name
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class TestResearchRequirementCheck:
    """Tests for research-requirement-check.py functions."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

        import common as common_module

        self.common_patcher = patch.object(common_module, "SESSION_DIR", self.temp_path)
        self.common_patcher.start()

        self.module = load_module_with_hyphen(
            "research_requirement_check", "research-requirement-check.py"
        )

    def teardown_method(self):
        """Clean up test fixtures."""
        self.common_patcher.stop()
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_is_gh_issue_create(self):
        """Should detect gh issue create commands."""
        assert self.module.is_gh_issue_create("gh issue create")
        assert self.module.is_gh_issue_create("gh issue create --title test")
        assert self.module.is_gh_issue_create('gh issue create --body "test body"')
        assert not self.module.is_gh_issue_create("gh issue view 123")
        assert not self.module.is_gh_issue_create("gh pr create")

    def test_is_gh_pr_create(self):
        """Should detect gh pr create commands."""
        assert self.module.is_gh_pr_create("gh pr create")
        assert self.module.is_gh_pr_create("gh pr create --title test")
        assert not self.module.is_gh_pr_create("gh pr merge 123")
        assert not self.module.is_gh_pr_create("gh issue create")

    def test_extract_labels(self):
        """Should extract labels from command."""
        labels = self.module.extract_labels("gh issue create --label tracking")
        assert labels == {"tracking"}

        labels = self.module.extract_labels("gh issue create --label bug --label urgent")
        assert labels == {"bug", "urgent"}

        labels = self.module.extract_labels("gh issue create -l chore")
        assert labels == {"chore"}

    def test_extract_labels_comma_separated(self):
        """Should extract comma-separated labels from single --label flag."""
        labels = self.module.extract_labels('gh issue create --label "tracking,backend"')
        assert labels == {"tracking", "backend"}

        labels = self.module.extract_labels("gh issue create --label 'bug,urgent,p1'")
        assert labels == {"bug", "urgent", "p1"}

    def test_extract_labels_comma_separated_with_spaces(self):
        """Should extract comma-separated labels with spaces after commas."""
        labels = self.module.extract_labels('gh issue create --label "tracking, backend"')
        assert labels == {"tracking", "backend"}

        labels = self.module.extract_labels("gh issue create --label 'bug, urgent, p1'")
        assert labels == {"bug", "urgent", "p1"}

        # Mixed with no spaces
        labels = self.module.extract_labels('gh issue create --label "bug,urgent, p1"')
        assert labels == {"bug", "urgent", "p1"}

    def test_has_bypass_label_comma_separated(self):
        """Should detect bypass labels in comma-separated list."""
        assert self.module.has_bypass_label('gh issue create --label "documentation,backend"')
        assert self.module.has_bypass_label("gh issue create --label 'bug,trivial'")
        assert not self.module.has_bypass_label('gh issue create --label "bug,urgent"')

    def test_has_bypass_label_comma_separated_with_spaces(self):
        """Should detect bypass labels in comma-separated list with spaces."""
        assert self.module.has_bypass_label('gh issue create --label "documentation, backend"')
        assert self.module.has_bypass_label("gh issue create --label 'bug, trivial'")
        assert not self.module.has_bypass_label('gh issue create --label "bug, urgent"')

    def test_has_bypass_label(self):
        """Should detect bypass labels."""
        assert self.module.has_bypass_label("gh issue create --label documentation")
        assert self.module.has_bypass_label("gh issue create --label trivial")
        assert not self.module.has_bypass_label("gh issue create --label bug")

    def test_extract_body(self):
        """Should extract body from command."""
        body = self.module.extract_body('gh issue create --body "test body"')
        assert body == "test body"

        body = self.module.extract_body("gh issue create --body 'test body'")
        assert body == "test body"

    def test_extract_body_with_escaped_quotes(self):
        """Should extract body with escaped quotes."""
        # Escaped double quotes within double-quoted string
        body = self.module.extract_body(r'gh issue create --body "He said \"hello\""')
        assert body == 'He said "hello"'

        # Escaped single quotes within single-quoted string
        body = self.module.extract_body(r"gh issue create --body 'It\'s working'")
        assert body == "It's working"

    def test_extract_body_with_short_flag(self):
        """Should extract body with -b short flag."""
        body = self.module.extract_body('gh issue create -b "test body"')
        assert body == "test body"

    def test_has_bypass_keyword(self):
        """Should detect bypass keywords in body."""
        assert self.module.has_bypass_keyword('gh issue create --body "調査不要"')
        assert self.module.has_bypass_keyword('gh issue create --body "No research needed"')
        assert not self.module.has_bypass_keyword('gh issue create --body "normal body"')

    def test_extract_body_heredoc_simple(self):
        """Should extract heredoc body without nested quotes."""
        cmd = """gh pr create --body "$(cat <<'EOF'
## なぜ
背景説明

調査不要
EOF
)" """
        body = self.module.extract_body(cmd)
        assert "なぜ" in body
        assert "調査不要" in body

    def test_extract_body_heredoc_with_nested_quotes(self):
        """Issue #2578: Should extract heredoc body with nested quotes correctly.

        When heredoc content contains nested quotes (e.g., code examples),
        the body extraction should capture the full content, not stop at
        the first nested quote.
        """
        cmd = '''gh pr create --title "Test" --body "$(cat <<'EOF'
## なぜ

説明

## 正しいワークフロー

```bash
gh pr edit {PR} --body "$(cat <<'BODY'
## なぜ
背景
BODY
)"
```

調査不要: 内部ポリシー変更
EOF
)"'''
        body = self.module.extract_body(cmd)
        # Key assertion: "調査不要" should be found even after nested quotes
        assert "調査不要" in body
        # Also verify other content is captured
        assert "なぜ" in body
        assert "gh pr edit" in body

    def test_has_bypass_keyword_heredoc_with_nested_quotes(self):
        """Issue #2578: Should detect bypass keyword after nested quotes in heredoc.

        This is the actual bug case: when heredoc contains code examples with
        nested quotes, the keyword "調査不要" appearing after those quotes
        was not being detected because extract_body was truncating at the
        first nested quote.
        """
        cmd = '''gh pr create --body "$(cat <<'EOF'
## なぜ

説明

## 例

```bash
gh pr edit {PR} --body "example content"
```

調査不要: 内部ポリシー変更
EOF
)"'''
        # This was failing before the fix
        assert self.module.has_bypass_keyword(cmd) is True

    def test_quoted_strings_not_detected(self):
        """Should not detect commands in quoted strings."""
        # This shouldn't be detected as gh issue create
        assert not self.module.is_gh_issue_create("echo 'gh issue create is cool'")


class TestResearchRequirementCheckMain:
    """Tests for main function with research state."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)
        self.research_file = self.temp_path / "research-activity-test.json"
        self.exploration_file = self.temp_path / "exploration-depth-test.json"

        import common as common_module
        import lib.research as research_module

        self.common_patcher = patch.object(common_module, "SESSION_DIR", self.temp_path)
        self.common_patcher.start()
        # Patch get_research_activity_file and get_exploration_file functions in lib.research
        self.research_file_patcher = patch.object(
            research_module, "get_research_activity_file", return_value=self.research_file
        )
        self.research_file_patcher.start()
        self.exploration_file_patcher = patch.object(
            research_module, "get_exploration_file", return_value=self.exploration_file
        )
        self.exploration_file_patcher.start()
        self.common_module = common_module

        self.module = load_module_with_hyphen(
            "research_requirement_check", "research-requirement-check.py"
        )

    def teardown_method(self):
        """Clean up test fixtures."""
        self.exploration_file_patcher.stop()
        self.research_file_patcher.stop()
        self.common_patcher.stop()
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_block_without_research(self):
        """Should block gh issue create without research."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --title test"},
        }

        import io

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch("builtins.print") as mock_print:
            self.module.main()
            output = json.loads(mock_print.call_args[0][0])
            assert output["decision"] == "block"
            assert "Web検索" in output["reason"]

    def test_allow_with_bypass_label(self):
        """Should allow with documentation label."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --title test --label documentation"},
        }

        import io

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch("builtins.print") as mock_print:
            self.module.main()
            output = json.loads(mock_print.call_args[0][0])
            assert output["decision"] == "approve"

    def test_allow_with_research(self):
        """Should allow when research was done."""
        # Create research activity file using patched path
        self.temp_path.mkdir(parents=True, exist_ok=True)
        self.research_file.write_text(json.dumps({"activities": [{"tool": "WebSearch"}]}))

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --title test"},
        }

        import io

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch("builtins.print") as mock_print:
            self.module.main()
            output = json.loads(mock_print.call_args[0][0])
            assert output["decision"] == "approve"

    def test_allow_with_sufficient_exploration(self):
        """Should allow when exploration depth is sufficient."""
        # Create exploration file with sufficient depth using patched path
        self.temp_path.mkdir(parents=True, exist_ok=True)
        self.exploration_file.write_text(json.dumps({"counts": {"Read": 3, "Glob": 2, "Grep": 1}}))

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "gh issue create --title test"},
        }

        import io

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch("builtins.print") as mock_print:
            self.module.main()
            output = json.loads(mock_print.call_args[0][0])
            assert output["decision"] == "approve"

    def test_allow_non_bash(self):
        """Should allow non-Bash tools."""
        input_data = {
            "tool_name": "Read",
            "tool_input": {"file_path": "/some/file"},
        }

        import io

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch("builtins.print") as mock_print:
            self.module.main()
            output = json.loads(mock_print.call_args[0][0])
            assert output["decision"] == "approve"
