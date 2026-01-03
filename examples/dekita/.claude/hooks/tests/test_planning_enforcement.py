#!/usr/bin/env python3
"""Tests for planning-enforcement.py hook."""

import importlib.util
import io
import json
import os
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


class TestPlanningEnforcement:
    """Tests for planning-enforcement.py functions."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

        self.module = load_module_with_hyphen("planning_enforcement", "planning-enforcement.py")

    def teardown_method(self):
        """Clean up test fixtures."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_is_worktree_add_command(self):
        """Should detect git worktree add commands."""
        assert self.module.is_worktree_add_command("git worktree add .worktrees/test")
        assert self.module.is_worktree_add_command(
            "git worktree add .worktrees/issue-123 -b feat/issue-123"
        )
        assert not self.module.is_worktree_add_command("git worktree list")
        assert not self.module.is_worktree_add_command("git worktree remove .worktrees/test")

    def test_extract_issue_number_from_branch(self):
        """Should extract issue number from branch name."""
        assert (
            self.module.extract_issue_number_from_branch("git worktree add .worktrees/issue-123")
            == "123"
        )
        assert (
            self.module.extract_issue_number_from_branch(
                "git worktree add path -b feat/issue-456-feature"
            )
            == "456"
        )
        assert (
            self.module.extract_issue_number_from_branch("git worktree add path -b fix/ISSUE-789")
            == "789"
        )
        assert (
            self.module.extract_issue_number_from_branch("git worktree add .worktrees/feature")
            is None
        )

    def test_has_skip_plan_env_inline(self):
        """Should detect inline SKIP_PLAN env var."""
        assert self.module.has_skip_plan_env("SKIP_PLAN=1 git worktree add .worktrees/test")
        assert self.module.has_skip_plan_env("SKIP_PLAN=true git worktree add .worktrees/test")
        assert not self.module.has_skip_plan_env("git worktree add .worktrees/test")

    def test_has_skip_plan_env_exported(self):
        """Should detect exported SKIP_PLAN env var."""
        with patch.dict(os.environ, {"SKIP_PLAN": "1"}):
            assert self.module.has_skip_plan_env("git worktree add .worktrees/test")

    def test_has_skip_plan_env_inline_falsy_values(self):
        """Should NOT skip when SKIP_PLAN has falsy value (Issue #956)."""
        assert not self.module.has_skip_plan_env("SKIP_PLAN=0 git worktree add .worktrees/test")
        assert not self.module.has_skip_plan_env("SKIP_PLAN=false git worktree add .worktrees/test")
        assert not self.module.has_skip_plan_env("SKIP_PLAN=False git worktree add .worktrees/test")

    def test_has_skip_plan_env_exported_falsy_values(self):
        """Should NOT skip when exported SKIP_PLAN has falsy value (Issue #956)."""
        with patch.dict(os.environ, {"SKIP_PLAN": "0"}, clear=True):
            assert not self.module.has_skip_plan_env("git worktree add .worktrees/test")
        with patch.dict(os.environ, {"SKIP_PLAN": "false"}, clear=True):
            assert not self.module.has_skip_plan_env("git worktree add .worktrees/test")
        with patch.dict(os.environ, {"SKIP_PLAN": ""}, clear=True):
            assert not self.module.has_skip_plan_env("git worktree add .worktrees/test")

    def test_has_skip_plan_env_quoted_strings_ignored(self):
        """Should NOT skip when SKIP_PLAN is inside quoted strings (Issue #956)."""
        assert not self.module.has_skip_plan_env(
            "echo 'SKIP_PLAN=1' && git worktree add .worktrees/test"
        )
        assert not self.module.has_skip_plan_env(
            'echo "SKIP_PLAN=1" && git worktree add .worktrees/test'
        )

    def test_has_skip_plan_env_inline_quoted_value(self):
        """Should skip when SKIP_PLAN has quoted truthy value (Issue #956 fix)."""
        assert self.module.has_skip_plan_env('SKIP_PLAN="1" git worktree add .worktrees/test')
        assert self.module.has_skip_plan_env("SKIP_PLAN='true' git worktree add .worktrees/test")

    def test_check_plan_file_exists_exact(self):
        """Should find exact plan file match."""
        plans_dir = self.temp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "issue-123.md").write_text("# Plan")

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            assert self.module.check_plan_file_exists("123")
            assert not self.module.check_plan_file_exists("456")

    def test_check_plan_file_exists_pattern(self):
        """Should find plan file by pattern."""
        plans_dir = self.temp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "amazing-feature-issue-123.md").write_text("# Plan")

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            assert self.module.check_plan_file_exists("123")

    def test_check_plan_file_exists_user_home_filename(self):
        """Should find plan file in ~/.claude/plans/ by filename pattern (Issue #881)."""
        # Create fake home directory to avoid using real user home
        fake_home = self.temp_path / "fake_home"
        fake_home.mkdir(parents=True)
        user_plans_dir = fake_home / ".claude" / "plans"
        user_plans_dir.mkdir(parents=True)
        (user_plans_dir / "test-issue-456-temp.md").write_text("# Plan for Issue 456")

        # Mock Path.home() to return fake home
        with patch.object(Path, "home", return_value=fake_home):
            with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
                assert self.module.check_plan_file_exists("456")
                assert not self.module.check_plan_file_exists("789")

    def test_check_plan_file_exists_user_home_content(self):
        """Should find plan file in ~/.claude/plans/ by content pattern (Issue #881).

        EnterPlanMode creates files with random names like 'sparkling-honking-gem.md'
        but the content contains 'Issue #XXX' references.
        """
        # Create fake home directory to avoid using real user home
        fake_home = self.temp_path / "fake_home"
        fake_home.mkdir(parents=True)
        user_plans_dir = fake_home / ".claude" / "plans"
        user_plans_dir.mkdir(parents=True)
        (user_plans_dir / "random-name-test.md").write_text(
            "# Plan\n\n## Issue #881: Test Issue\n\nSome content here."
        )

        # Mock Path.home() to return fake home
        with patch.object(Path, "home", return_value=fake_home):
            with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
                assert self.module.check_plan_file_exists("881")

    def test_check_plan_file_project_takes_priority(self):
        """Project plan file should be found first before user home."""
        # Create project plan file
        project_plans_dir = self.temp_path / ".claude" / "plans"
        project_plans_dir.mkdir(parents=True)
        (project_plans_dir / "issue-999.md").write_text("# Project Plan")

        # Create fake home with different content (should not be reached)
        fake_home = self.temp_path / "fake_home"
        fake_home.mkdir(parents=True)

        with patch.object(Path, "home", return_value=fake_home):
            with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
                assert self.module.check_plan_file_exists("999")

    def test_check_plan_file_continues_on_read_error(self):
        """Should continue checking other files when one file read fails (OSError)."""
        # Create fake home with multiple files
        fake_home = self.temp_path / "fake_home"
        fake_home.mkdir(parents=True)
        user_plans_dir = fake_home / ".claude" / "plans"
        user_plans_dir.mkdir(parents=True)

        # Create two files - first (alphabetically) will raise OSError, second has the issue
        # "aaa-bad.md" < "zzz-good.md" alphabetically to ensure bad file is processed first
        bad_file = user_plans_dir / "aaa-bad.md"
        good_file = user_plans_dir / "zzz-good.md"
        bad_file.write_text("content")  # Will be mocked to raise OSError
        good_file.write_text("# Plan\n\nIssue #777: Test")

        # Mock read_text to raise OSError for bad_file only
        original_read_text = Path.read_text

        def mock_read_text(self, *args, **kwargs):
            if self.name == "aaa-bad.md":
                raise OSError("Permission denied")
            return original_read_text(self, *args, **kwargs)

        # glob() order is not guaranteed, so we sort to ensure deterministic test
        original_glob = Path.glob

        def sorted_glob(self, pattern):
            return sorted(original_glob(self, pattern), key=lambda p: p.name)

        with patch.object(Path, "home", return_value=fake_home):
            with patch.object(Path, "read_text", mock_read_text):
                with patch.object(Path, "glob", sorted_glob):
                    with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
                        # Should find issue 777 in good_file despite bad_file error
                        assert self.module.check_plan_file_exists("777")

    def test_check_plan_file_continues_on_unicode_error(self):
        """Should continue checking other files when one file has invalid UTF-8 (UnicodeDecodeError)."""
        # Create fake home with multiple files
        fake_home = self.temp_path / "fake_home"
        fake_home.mkdir(parents=True)
        user_plans_dir = fake_home / ".claude" / "plans"
        user_plans_dir.mkdir(parents=True)

        # Create two files - first will raise UnicodeDecodeError, second has the issue
        bad_file = user_plans_dir / "aaa-bad-unicode.md"
        good_file = user_plans_dir / "zzz-good-unicode.md"
        bad_file.write_text("content")  # Will be mocked to raise UnicodeDecodeError
        good_file.write_text("# Plan\n\nIssue #888: Test")

        # Mock read_text to raise UnicodeDecodeError for bad_file only
        original_read_text = Path.read_text

        def mock_read_text(self, *args, **kwargs):
            if self.name == "aaa-bad-unicode.md":
                raise UnicodeDecodeError("utf-8", b"\x80\x81", 0, 1, "invalid start byte")
            return original_read_text(self, *args, **kwargs)

        # glob() order is not guaranteed, so we sort to ensure deterministic test
        original_glob = Path.glob

        def sorted_glob(self, pattern):
            return sorted(original_glob(self, pattern), key=lambda p: p.name)

        with patch.object(Path, "home", return_value=fake_home):
            with patch.object(Path, "read_text", mock_read_text):
                with patch.object(Path, "glob", sorted_glob):
                    with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
                        # Should find issue 888 in good_file despite bad_file error
                        assert self.module.check_plan_file_exists("888")

    def test_has_bypass_title_prefix(self):
        """Should detect bypass title prefixes."""
        assert self.module.has_bypass_title_prefix("test: add unit tests") == "test:"
        assert self.module.has_bypass_title_prefix("chore: update dependencies") == "chore:"
        assert self.module.has_bypass_title_prefix("docs: update README") == "docs:"
        # Case-insensitive
        assert self.module.has_bypass_title_prefix("TEST: add unit tests") == "test:"
        assert self.module.has_bypass_title_prefix("Docs: update README") == "docs:"
        # Issue #2169: "fix" added to bypass types
        assert self.module.has_bypass_title_prefix("fix: bug fix") == "fix:"
        # No match
        assert self.module.has_bypass_title_prefix("feat: add new feature") is None
        # Edge cases
        assert self.module.has_bypass_title_prefix("") is None  # Empty string
        assert self.module.has_bypass_title_prefix("test:") == "test:"  # Prefix only
        assert self.module.has_bypass_title_prefix("test :") is None  # Space before colon

    def test_has_bypass_title_prefix_conventional_commits(self):
        """Should detect Conventional Commits format with scope (Issue #1224)."""
        # With scope
        assert self.module.has_bypass_title_prefix("chore(ci): update workflow") == "chore:"
        assert self.module.has_bypass_title_prefix("test(hooks): add unit tests") == "test:"
        assert self.module.has_bypass_title_prefix("docs(readme): update README") == "docs:"
        # With scope, case-insensitive
        assert self.module.has_bypass_title_prefix("CHORE(CI): update workflow") == "chore:"
        assert self.module.has_bypass_title_prefix("Test(Hooks): add tests") == "test:"
        # Issue #2169: "fix" with scope also bypasses
        assert self.module.has_bypass_title_prefix("fix(auth): fix login") == "fix:"
        # Non-bypass types with scope should not match
        assert self.module.has_bypass_title_prefix("feat(api): add endpoint") is None
        # Edge cases with scope
        assert self.module.has_bypass_title_prefix("chore():") == "chore:"  # Empty scope
        # Invalid formats that should NOT match (Codex review feedback)
        assert self.module.has_bypass_title_prefix("chore(a)(b): nested") is None  # Multiple parens
        assert (
            self.module.has_bypass_title_prefix("chore(ci) update: desc") is None
        )  # Text after scope
        assert self.module.has_bypass_title_prefix("chore (ci): desc") is None  # Space before scope


class TestPlanningEnforcementMain:
    """Tests for main function."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

        self.module = load_module_with_hyphen("planning_enforcement", "planning-enforcement.py")

    def teardown_method(self):
        """Clean up test fixtures."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_block_without_plan_file(self):
        """Should block worktree add without plan file."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git worktree add .worktrees/issue-123 -b feat/issue-123"},
        }

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch("builtins.print") as mock_print:
                # Mock get_issue_labels to return empty set
                with patch.object(self.module, "get_issue_labels", return_value=set()):
                    self.module.main()
                    output = json.loads(mock_print.call_args[0][0])
                    assert output["decision"] == "block"
                    assert "Plan file" in output["reason"]

    def test_allow_with_plan_file(self):
        """Should allow when plan file exists."""
        plans_dir = self.temp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "issue-123.md").write_text("# Plan")

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git worktree add .worktrees/issue-123 -b feat/issue-123"},
        }

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch("builtins.print") as mock_print:
                with patch.object(self.module, "get_issue_labels", return_value=set()):
                    self.module.main()
                    output = json.loads(mock_print.call_args[0][0])
                    assert output["decision"] == "approve"

    def test_allow_with_skip_plan_env(self):
        """Should allow with SKIP_PLAN env var."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "SKIP_PLAN=1 git worktree add .worktrees/issue-123"},
        }

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch("builtins.print") as mock_print:
                self.module.main()
                output = json.loads(mock_print.call_args[0][0])
                assert output["decision"] == "approve"
                assert "SKIP_PLAN" in output.get("systemMessage", "")

    def test_allow_with_bypass_label(self):
        """Should allow when Issue has bypass label."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git worktree add .worktrees/issue-123 -b feat/issue-123"},
        }

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch("builtins.print") as mock_print:
                # Mock get_issue_labels to return bypass label
                with patch.object(self.module, "get_issue_labels", return_value={"documentation"}):
                    self.module.main()
                    output = json.loads(mock_print.call_args[0][0])
                    assert output["decision"] == "approve"

    def test_allow_with_bypass_title_prefix(self):
        """Should allow when Issue has bypass title prefix (Issue #857)."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git worktree add .worktrees/issue-123 -b test/issue-123"},
        }

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch("builtins.print") as mock_print:
                with patch.object(self.module, "check_local_branch_exists", return_value=False):
                    with patch.object(self.module, "check_already_fixed", return_value=None):
                        with patch.object(self.module, "get_issue_labels", return_value=set()):
                            with patch.object(
                                self.module, "get_issue_title", return_value="test: add unit tests"
                            ):
                                self.module.main()
                                output = json.loads(mock_print.call_args[0][0])
                                assert output["decision"] == "approve"
                                assert "test:" in output.get("systemMessage", "")

    def test_allow_with_bypass_title_prefix_chore(self):
        """Should allow when Issue title starts with chore:."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git worktree add .worktrees/issue-456 -b chore/issue-456"},
        }

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch("builtins.print") as mock_print:
                with patch.object(self.module, "check_local_branch_exists", return_value=False):
                    with patch.object(self.module, "check_already_fixed", return_value=None):
                        with patch.object(self.module, "get_issue_labels", return_value=set()):
                            with patch.object(
                                self.module,
                                "get_issue_title",
                                return_value="chore: update dependencies",
                            ):
                                self.module.main()
                                output = json.loads(mock_print.call_args[0][0])
                                assert output["decision"] == "approve"

    def test_block_without_bypass_title_prefix(self):
        """Should block when Issue title has non-bypass prefix."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git worktree add .worktrees/issue-789 -b feat/issue-789"},
        }

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch("builtins.print") as mock_print:
                with patch.object(self.module, "check_local_branch_exists", return_value=False):
                    with patch.object(self.module, "check_already_fixed", return_value=None):
                        with patch.object(self.module, "get_issue_labels", return_value=set()):
                            with patch.object(
                                self.module, "get_issue_title", return_value="feat: add new feature"
                            ):
                                self.module.main()
                                output = json.loads(mock_print.call_args[0][0])
                                assert output["decision"] == "block"

    def test_allow_non_issue_worktree(self):
        """Should allow worktree without issue number."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git worktree add .worktrees/feature-branch"},
        }

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch("builtins.print") as mock_print:
            self.module.main()
            output = json.loads(mock_print.call_args[0][0])
            assert output["decision"] == "approve"

    def test_allow_non_worktree_command(self):
        """Should allow non-worktree commands."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
        }

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch("builtins.print") as mock_print:
            self.module.main()
            output = json.loads(mock_print.call_args[0][0])
            assert output["decision"] == "approve"


class TestAlreadyFixedCheck:
    """Tests for already-fixed detection feature."""

    def setup_method(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

        self.module = load_module_with_hyphen("planning_enforcement", "planning-enforcement.py")

    def teardown_method(self):
        """Clean up test fixtures."""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_has_skip_already_fixed_env_inline(self):
        """Should detect inline SKIP_ALREADY_FIXED env var."""
        assert self.module.has_skip_already_fixed_env(
            "SKIP_ALREADY_FIXED=1 git worktree add .worktrees/test"
        )
        assert self.module.has_skip_already_fixed_env(
            "SKIP_ALREADY_FIXED=true git worktree add .worktrees/test"
        )
        assert not self.module.has_skip_already_fixed_env("git worktree add .worktrees/test")

    def test_has_skip_already_fixed_env_exported(self):
        """Should detect exported SKIP_ALREADY_FIXED env var."""
        with patch.dict(os.environ, {"SKIP_ALREADY_FIXED": "1"}):
            assert self.module.has_skip_already_fixed_env("git worktree add .worktrees/test")

    def test_search_issue_in_code(self):
        """Should find Issue references in code."""
        claude_dir = self.temp_path / ".claude" / "scripts"
        claude_dir.mkdir(parents=True)
        (claude_dir / "my-script.sh").write_text("# Issue #123: Fix something\necho hello")

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            refs = self.module.search_issue_in_code("123")
            assert len(refs) == 1
            assert "my-script.sh:1" in refs[0]

    def test_search_issue_in_code_no_match(self):
        """Should return empty for non-existent issue."""
        claude_dir = self.temp_path / ".claude" / "scripts"
        claude_dir.mkdir(parents=True)
        (claude_dir / "my-script.sh").write_text("# Issue #999: Other fix\necho hello")

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            refs = self.module.search_issue_in_code("123")
            assert refs == []

    def test_check_already_fixed_with_code_refs(self):
        """Should detect already fixed from code references."""
        claude_dir = self.temp_path / ".claude" / "scripts"
        claude_dir.mkdir(parents=True)
        (claude_dir / "fix.py").write_text("# Issue #456: Implemented fix")

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch.object(self.module, "get_merged_prs_for_issue", return_value=[]):
                result = self.module.check_already_fixed("456")
                assert result is not None
                assert "code_refs" in result

    def test_check_already_fixed_with_merged_pr(self):
        """Should detect already fixed from merged PRs."""
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch.object(
                self.module,
                "get_merged_prs_for_issue",
                return_value=[{"number": 100, "title": "Fix #789"}],
            ):
                result = self.module.check_already_fixed("789")
                assert result is not None
                assert "merged_prs" in result
                assert result["merged_prs"][0]["number"] == 100

    def test_check_already_fixed_none_when_not_fixed(self):
        """Should return None when issue is not fixed."""
        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch.object(self.module, "get_merged_prs_for_issue", return_value=[]):
                result = self.module.check_already_fixed("999")
                assert result is None

    def test_block_when_already_fixed(self):
        """Should block worktree add when issue is already fixed."""
        plans_dir = self.temp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "issue-123.md").write_text("# Plan")

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git worktree add .worktrees/issue-123 -b feat/issue-123"},
        }

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch("builtins.print") as mock_print:
                with patch.object(self.module, "get_issue_labels", return_value=set()):
                    with patch.object(
                        self.module,
                        "check_already_fixed",
                        return_value={"merged_prs": [{"number": 50, "title": "Fix"}]},
                    ):
                        self.module.main()
                        output = json.loads(mock_print.call_args[0][0])
                        assert output["decision"] == "block"
                        assert "既に解決済み" in output["reason"]

    def test_allow_when_only_code_refs(self):
        """Should allow worktree add when only code refs exist (no merged PRs).

        Issue #1768: Code refs alone should not block, only warn to stderr.
        """
        plans_dir = self.temp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "issue-123.md").write_text("# Plan")

        input_data = {
            "tool_name": "Bash",
            "tool_input": {"command": "git worktree add .worktrees/issue-123 -b feat/issue-123"},
        }

        sys.stdin = io.StringIO(json.dumps(input_data))
        stdout_output = []
        stderr_output = []

        def capture_print(*args, **kwargs):
            if kwargs.get("file") is sys.stderr:
                stderr_output.append(args[0] if args else "")
            else:
                stdout_output.append(args[0] if args else "")

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch("builtins.print", side_effect=capture_print):
                with patch.object(self.module, "get_issue_labels", return_value=set()):
                    with patch.object(
                        self.module,
                        "check_already_fixed",
                        return_value={"code_refs": [".claude/hooks/foo.py:10"]},
                    ):
                        self.module.main()

        # Verify JSON output (approve decision)
        assert len(stdout_output) > 0, "No stdout output captured"
        output = json.loads(stdout_output[-1])
        assert output["decision"] == "approve"

        # Verify warning was printed to stderr
        assert len(stderr_output) > 0, "No stderr warning output captured"
        stderr_text = stderr_output[0]
        assert "planning-enforcement" in stderr_text
        assert "Issue #123" in stderr_text
        assert "コード参照" in stderr_text

    def test_allow_with_skip_already_fixed_env(self):
        """Should allow with SKIP_ALREADY_FIXED even if already fixed."""
        plans_dir = self.temp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "issue-123.md").write_text("# Plan")

        input_data = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "SKIP_ALREADY_FIXED=1 git worktree add .worktrees/issue-123 -b feat/issue-123"
            },
        }

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch("builtins.print") as mock_print:
                with patch.object(self.module, "get_issue_labels", return_value=set()):
                    with patch.object(
                        self.module,
                        "check_already_fixed",
                        return_value={"merged_prs": [{"number": 50, "title": "Fix"}]},
                    ):
                        self.module.main()
                        output = json.loads(mock_print.call_args[0][0])
                        assert output["decision"] == "approve"

    def test_skip_plan_does_not_bypass_already_fixed(self):
        """Should block even with SKIP_PLAN if issue is already fixed."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "SKIP_PLAN=1 git worktree add .worktrees/issue-123 -b feat/issue-123"
            },
        }

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch("builtins.print") as mock_print:
                with patch.object(self.module, "get_issue_labels", return_value=set()):
                    with patch.object(
                        self.module,
                        "check_already_fixed",
                        return_value={"merged_prs": [{"number": 50, "title": "Fix"}]},
                    ):
                        self.module.main()
                        output = json.loads(mock_print.call_args[0][0])
                        # SKIP_PLAN should NOT bypass already-fixed check
                        assert output["decision"] == "block"
                        assert "既に解決済み" in output["reason"]

    # Tests for branch existence check (Issue #833)
    def test_extract_branch_name_with_b_flag(self):
        """Should extract branch name with -b flag."""
        assert (
            self.module.extract_branch_name_from_command(
                "git worktree add .worktrees/issue-123 -b feat/issue-123"
            )
            == "feat/issue-123"
        )

    def test_extract_branch_name_with_branch_flag(self):
        """Should extract branch name with --branch flag."""
        assert (
            self.module.extract_branch_name_from_command(
                "git worktree add .worktrees/issue-123 --branch fix/issue-123"
            )
            == "fix/issue-123"
        )

    def test_extract_branch_name_existing_branch(self):
        """Should extract existing branch name (no -b flag)."""
        assert (
            self.module.extract_branch_name_from_command(
                "git worktree add .worktrees/issue-123 existing-branch"
            )
            == "existing-branch"
        )

    def test_extract_branch_name_no_branch(self):
        """Should return None when no branch specified."""
        assert (
            self.module.extract_branch_name_from_command("git worktree add .worktrees/issue-123")
            is None
        )

    def test_has_skip_branch_check_env_inline(self):
        """Should detect inline SKIP_BRANCH_CHECK env var."""
        assert self.module.has_skip_branch_check_env(
            "SKIP_BRANCH_CHECK=1 git worktree add .worktrees/test -b feat/test"
        )
        assert not self.module.has_skip_branch_check_env(
            "git worktree add .worktrees/test -b feat/test"
        )

    def test_has_create_branch_flag_with_b(self):
        """Should detect -b flag correctly."""
        assert self.module.has_create_branch_flag(
            "git worktree add .worktrees/issue-123 -b feat/issue-123"
        )
        assert self.module.has_create_branch_flag(
            "git worktree add .worktrees/issue-123 --branch feat/issue-123"
        )

    def test_has_create_branch_flag_without_flag(self):
        """Should not detect -b when not a flag."""
        # Branch name contains "-b" but no -b flag
        assert not self.module.has_create_branch_flag(
            "git worktree add .worktrees/issue-123 fix/issue-123-bug"
        )
        # No branch at all
        assert not self.module.has_create_branch_flag("git worktree add .worktrees/issue-123")

    def test_has_skip_branch_check_env_exported(self):
        """Should detect exported SKIP_BRANCH_CHECK env var."""
        with patch.dict(os.environ, {"SKIP_BRANCH_CHECK": "1"}):
            assert self.module.has_skip_branch_check_env("git worktree add .worktrees/test")

    def test_block_when_branch_exists(self):
        """Should block when trying to create a branch that already exists."""
        input_data = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "git worktree add .worktrees/issue-123 -b feat/issue-123-existing"
            },
        }

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch("builtins.print") as mock_print:
                with patch.object(self.module, "check_local_branch_exists", return_value=True):
                    with patch.object(
                        self.module,
                        "get_branch_info",
                        return_value={
                            "branch": "feat/issue-123-existing",
                            "commits_ahead": 3,
                            "last_commit_time": "2 hours ago",
                        },
                    ):
                        self.module.main()
                        output = json.loads(mock_print.call_args[0][0])
                        assert output["decision"] == "block"
                        assert "既に存在します" in output["reason"]
                        assert "競合リスク" in output["reason"]

    def test_allow_when_branch_does_not_exist(self):
        """Should allow when branch does not exist."""
        plans_dir = self.temp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "issue-123.md").write_text("# Plan")

        input_data = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "git worktree add .worktrees/issue-123 -b feat/issue-123-new"
            },
        }

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch("builtins.print") as mock_print:
                with patch.object(self.module, "check_local_branch_exists", return_value=False):
                    with patch.object(self.module, "check_already_fixed", return_value=None):
                        with patch.object(self.module, "get_issue_labels", return_value=set()):
                            self.module.main()
                            output = json.loads(mock_print.call_args[0][0])
                            assert output["decision"] == "approve"

    def test_allow_existing_branch_without_b_flag(self):
        """Should allow when using existing branch without -b flag."""
        plans_dir = self.temp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "issue-123.md").write_text("# Plan")

        input_data = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "git worktree add .worktrees/issue-123 feat/issue-123-existing"
            },
        }

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch("builtins.print") as mock_print:
                # Even if branch exists, should allow without -b flag
                with patch.object(self.module, "check_local_branch_exists", return_value=True):
                    with patch.object(self.module, "check_already_fixed", return_value=None):
                        with patch.object(self.module, "get_issue_labels", return_value=set()):
                            self.module.main()
                            output = json.loads(mock_print.call_args[0][0])
                            # Should approve because -b flag is not present
                            assert output["decision"] == "approve"

    def test_allow_with_skip_branch_check_env(self):
        """Should allow when SKIP_BRANCH_CHECK env var is set."""
        plans_dir = self.temp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "issue-123.md").write_text("# Plan")

        input_data = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "SKIP_BRANCH_CHECK=1 git worktree add .worktrees/issue-123 -b feat/issue-123"
            },
        }

        sys.stdin = io.StringIO(json.dumps(input_data))

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(self.temp_path)}):
            with patch("builtins.print") as mock_print:
                # Even if branch exists, should allow with SKIP_BRANCH_CHECK
                with patch.object(self.module, "check_local_branch_exists", return_value=True):
                    with patch.object(self.module, "check_already_fixed", return_value=None):
                        with patch.object(self.module, "get_issue_labels", return_value=set()):
                            self.module.main()
                            output = json.loads(mock_print.call_args[0][0])
                            assert output["decision"] == "approve"
