#!/usr/bin/env python3
"""Tests for post-merge-observation-issue.py hook."""

from conftest import load_hook_module


class TestGenerateChecklistItems:
    """Tests for generate_checklist_items function."""

    def setup_method(self):
        """Import module for each test."""
        self.module = load_hook_module("post-merge-observation-issue")

    def test_hook_files_generate_hook_items(self):
        """Hook files should generate hook-specific checklist items."""
        files = [{"path": ".claude/hooks/my-new-hook.py"}]
        claude_items, human_items, commands = self.module.generate_checklist_items(files)

        assert "フックが正しく発火する（該当操作後にログ確認）" in claude_items
        assert "エラーハンドリングが正しく動作する" in claude_items
        assert len(human_items) == 0
        assert any("grep <フック名>" in cmd for cmd in commands)

    def test_frontend_files_generate_frontend_items(self):
        """Frontend files should generate frontend-specific checklist items."""
        files = [{"path": "frontend/src/components/Button.tsx"}]
        claude_items, human_items, commands = self.module.generate_checklist_items(files)

        assert "ビルドが成功する（`pnpm build`）" in claude_items
        assert "UI表示が崩れていない（本番URL確認）" in human_items
        assert "モバイル表示に問題がない（実機またはDevTools確認）" in human_items
        assert "pnpm build" in commands

    def test_worker_files_generate_api_items(self):
        """Worker files should generate API-specific checklist items."""
        files = [{"path": "worker/src/api/handler.ts"}]
        claude_items, human_items, commands = self.module.generate_checklist_items(files)

        assert "APIが正常にレスポンスを返す" in claude_items
        assert "レスポンス速度に問題がない（体感確認）" in human_items
        assert any("curl" in cmd for cmd in commands)

    def test_test_files_generate_test_items(self):
        """Test files should generate test-specific checklist items."""
        files = [{"path": "frontend/src/components/Button.test.tsx"}]
        claude_items, human_items, commands = self.module.generate_checklist_items(files)

        assert "テストが全てパスする（`pnpm test:ci`）" in claude_items
        assert "pnpm test:ci" in commands

    def test_shared_files_generate_typecheck_items(self):
        """Shared type files should generate typecheck-specific checklist items."""
        files = [{"path": "shared/types.ts"}]
        claude_items, human_items, commands = self.module.generate_checklist_items(files)

        assert "型定義の変更がfrontend/workerで正しく反映される" in claude_items
        assert "pnpm typecheck" in commands

    def test_workflow_files_generate_ci_items(self):
        """Workflow files should generate CI-specific checklist items."""
        files = [{"path": ".github/workflows/ci.yml"}]
        claude_items, human_items, commands = self.module.generate_checklist_items(files)

        assert "CIが正常に動作する" in claude_items
        assert any("gh run list" in cmd for cmd in commands)

    def test_multiple_file_types_combine_items(self):
        """Multiple file types should combine their checklist items."""
        files = [
            {"path": ".claude/hooks/my-hook.py"},
            {"path": "frontend/src/App.tsx"},
        ]
        claude_items, human_items, commands = self.module.generate_checklist_items(files)

        # Should have items from both hooks and frontend
        assert "フックが正しく発火する（該当操作後にログ確認）" in claude_items
        assert "ビルドが成功する（`pnpm build`）" in claude_items
        assert "UI表示が崩れていない（本番URL確認）" in human_items

    def test_duplicate_patterns_are_deduplicated(self):
        """Multiple files matching same pattern should not duplicate items."""
        files = [
            {"path": ".claude/hooks/hook1.py"},
            {"path": ".claude/hooks/hook2.py"},
            {"path": ".claude/hooks/hook3.py"},
        ]
        claude_items, human_items, commands = self.module.generate_checklist_items(files)

        # Should only have hook items once, not three times
        assert claude_items.count("フックが正しく発火する（該当操作後にログ確認）") == 1
        assert claude_items.count("エラーハンドリングが正しく動作する") == 1

    def test_empty_files_return_empty_lists(self):
        """Empty file list should return empty checklist items."""
        files = []
        claude_items, human_items, commands = self.module.generate_checklist_items(files)

        assert claude_items == []
        assert human_items == []
        assert commands == []

    def test_unknown_files_return_empty_lists(self):
        """Files not matching any pattern should return empty lists."""
        files = [{"path": "some/random/file.xyz"}]
        claude_items, human_items, commands = self.module.generate_checklist_items(files)

        assert claude_items == []
        assert human_items == []
        assert commands == []

    def test_scripts_files_generate_script_items(self):
        """Script files should generate script-specific checklist items."""
        files = [{"path": ".claude/scripts/my-script.py"}]
        claude_items, human_items, commands = self.module.generate_checklist_items(files)

        assert "スクリプトが正常に実行できる" in claude_items
        assert "ヘルプオプション（--help）が動作する" in claude_items
        assert any("--help" in cmd for cmd in commands)


class TestMatchesPattern:
    """Tests for _matches_pattern helper function."""

    def setup_method(self):
        """Import module for each test."""
        self.module = load_hook_module("post-merge-observation-issue")

    def test_directory_pattern_matches_subdirectory(self):
        """Directory patterns should match files in subdirectories."""
        assert self.module._matches_pattern(".claude/hooks/my-hook.py", ".claude/hooks/")
        assert self.module._matches_pattern(".claude/hooks/lib/utils.py", ".claude/hooks/")

    def test_directory_pattern_not_matches_other_dirs(self):
        """Directory patterns should not match other directories."""
        assert not self.module._matches_pattern("src/hooks/handler.py", ".claude/hooks/")

    def test_extension_pattern_matches_in_filename(self):
        """Extension patterns should match in filename."""
        assert self.module._matches_pattern("src/Button.test.tsx", ".test.")
        assert self.module._matches_pattern("components/Modal.test.ts", ".test.")

    def test_extension_pattern_not_matches_in_directory(self):
        """Extension patterns should not match directory names."""
        # ".test." in directory name should not match
        assert not self.module._matches_pattern("src/.test.dir/file.py", ".test.")

    def test_prefix_pattern_matches_at_start(self):
        """Prefix patterns should match at start of filename."""
        assert self.module._matches_pattern("tests/test_handler.py", "test_")
        assert self.module._matches_pattern(".claude/hooks/tests/test_utils.py", "test_")

    def test_prefix_pattern_not_matches_in_middle(self):
        """Prefix patterns should not match in middle of filename."""
        # "test_" in middle should not match (e.g., latest_version.py does not
        # contain "test_" anyway, but ensure prefix matching works)
        assert not self.module._matches_pattern("src/latest_handler.py", "test_")
        assert not self.module._matches_pattern("utils/contest_runner.py", "test_")

    def test_exact_filename_matches_exact(self):
        """Exact filename patterns should match exact filename only."""
        assert self.module._matches_pattern("config/settings.json", "settings.json")
        assert self.module._matches_pattern(".claude/settings.json", "settings.json")

    def test_exact_filename_not_matches_partial(self):
        """Exact filename patterns should not match partial filenames."""
        assert not self.module._matches_pattern("config/user_settings.json", "settings.json")
        assert not self.module._matches_pattern("config/settings.json.bak", "settings.json")


class TestGenerateChecklistItemsFalsePositives:
    """Tests to ensure pattern matching doesn't produce false positives."""

    def setup_method(self):
        """Import module for each test."""
        self.module = load_hook_module("post-merge-observation-issue")

    def test_user_settings_not_matches_settings_pattern(self):
        """Files like 'user_settings.json' should not match 'settings.json' pattern."""
        files = [{"path": "config/user_settings.json"}]
        claude_items, _, _ = self.module.generate_checklist_items(files)
        assert "設定変更が反映される" not in claude_items

    def test_atest_b_not_matches_test_extension_pattern(self):
        """Files like 'atest.b' should not match '.test.' pattern in directory."""
        files = [{"path": "src/.test.dir/file.py"}]
        claude_items, _, _ = self.module.generate_checklist_items(files)
        assert "テストが全てパスする（`pnpm test:ci`）" not in claude_items


class TestIsDocsOnly:
    """Tests for is_docs_only function."""

    def setup_method(self):
        """Import module for each test."""
        self.module = load_hook_module("post-merge-observation-issue")

    def test_md_files_are_docs_only(self):
        """Markdown files should be considered docs-only."""
        files = [{"path": "README.md"}, {"path": "docs/guide.md"}]
        assert self.module.is_docs_only(files) is True

    def test_code_files_are_not_docs_only(self):
        """Code files should not be considered docs-only."""
        files = [{"path": "src/main.py"}]
        assert self.module.is_docs_only(files) is False

    def test_mixed_files_are_not_docs_only(self):
        """Mixed docs and code files should not be considered docs-only."""
        files = [{"path": "README.md"}, {"path": "src/main.py"}]
        assert self.module.is_docs_only(files) is False

    def test_claude_prompts_are_docs_only(self):
        """Files in .claude/prompts/ should be considered docs-only."""
        files = [{"path": ".claude/prompts/task.md"}]
        assert self.module.is_docs_only(files) is True

    def test_claude_skills_are_docs_only(self):
        """Files in .claude/skills/ should be considered docs-only."""
        files = [{"path": ".claude/skills/review/SKILL.md"}]
        assert self.module.is_docs_only(files) is True

    def test_empty_files_are_not_docs_only(self):
        """Empty file list should not be considered docs-only."""
        files = []
        assert self.module.is_docs_only(files) is False
