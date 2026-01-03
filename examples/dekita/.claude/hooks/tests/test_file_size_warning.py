#!/usr/bin/env python3
"""Tests for file-size-warning.py file size checking."""

import importlib.util
import sys
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "file-size-warning.py"


def load_module():
    """Load the hook module for testing."""
    # Temporarily add hooks directory to path for common module import
    hooks_dir = str(HOOK_PATH.parent)
    sys.path.insert(0, hooks_dir)
    try:
        spec = importlib.util.spec_from_file_location("file_size_warning", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(hooks_dir)


# Load module and get functions
module = load_module()
should_exclude = module.should_exclude
get_threshold = module.get_threshold


class TestShouldExclude:
    """Test that excluded files are correctly detected."""

    def test_test_files_typescript(self):
        """TypeScript test files should be excluded."""
        assert should_exclude("src/components/Button.test.ts")
        assert should_exclude("src/components/Button.test.tsx")
        assert should_exclude("src/components/Button.spec.ts")
        assert should_exclude("src/components/Button.spec.tsx")

    def test_test_files_python(self):
        """Python test files should be excluded."""
        assert should_exclude("tests/test_hooks.py")
        assert should_exclude("hooks/tests/test_merge_check.py")
        assert should_exclude("src/module_test.py")

    def test_type_definitions(self):
        """TypeScript type definition files should be excluded."""
        assert should_exclude("src/types/index.d.ts")
        assert should_exclude("node_modules/@types/react/index.d.ts")

    def test_generated_directories(self):
        """Generated/build directories should be excluded."""
        # 絶対パス
        assert should_exclude("/project/generated/api.ts")
        assert should_exclude("/project/dist/bundle.js")
        assert should_exclude("/project/node_modules/react/index.js")
        assert should_exclude("/project/__pycache__/module.cpython-311.pyc")
        assert should_exclude("/project/build/output.js")
        # 相対パス
        assert should_exclude("src/generated/api.ts")
        assert should_exclude("dist/bundle.js")
        assert should_exclude("node_modules/react/index.js")

    def test_config_files(self):
        """Configuration files should be excluded."""
        assert should_exclude("package.json")
        assert should_exclude("tsconfig.json")
        assert should_exclude("config.yaml")
        assert should_exclude("settings.yml")
        assert should_exclude("pyproject.toml")
        assert should_exclude("pnpm-lock.yaml")

    def test_documentation_files(self):
        """AGENTS.md and CLAUDE.md should be excluded."""
        assert should_exclude("AGENTS.md")
        assert should_exclude("CLAUDE.md")
        assert should_exclude(".claude/skills/workflow/SKILL.md")

    def test_empty_path(self):
        """Empty path should be excluded."""
        assert should_exclude("")
        assert should_exclude(None)


class TestShouldNotExclude:
    """Test that regular source files are not excluded."""

    def test_typescript_source(self):
        """TypeScript source files should not be excluded."""
        assert not should_exclude("src/components/Button.tsx")
        assert not should_exclude("src/utils/helpers.ts")

    def test_python_source(self):
        """Python source files should not be excluded."""
        assert not should_exclude("src/main.py")
        assert not should_exclude(".claude/hooks/merge-check.py")

    def test_javascript_source(self):
        """JavaScript source files should not be excluded."""
        assert not should_exclude("src/app.js")
        assert not should_exclude("scripts/build.mjs")


class TestGetThreshold:
    """Test that thresholds are correctly determined by file extension."""

    def test_typescript_threshold(self):
        """TypeScript files should use TS threshold (400)."""
        assert get_threshold("src/app.ts") == 400
        assert get_threshold("src/component.tsx") == 400

    def test_javascript_threshold(self):
        """JavaScript files should use TS threshold (400)."""
        assert get_threshold("src/app.js") == 400
        assert get_threshold("src/component.jsx") == 400
        assert get_threshold("scripts/build.mjs") == 400
        assert get_threshold("config.cjs") == 400

    def test_python_threshold(self):
        """Python files should use Python threshold (500)."""
        assert get_threshold("main.py") == 500
        assert get_threshold(".claude/hooks/hook.py") == 500

    def test_other_files_threshold(self):
        """Other files should use default threshold (500)."""
        assert get_threshold("main.go") == 500
        assert get_threshold("app.rs") == 500
        assert get_threshold("script.sh") == 500

    def test_case_insensitive(self):
        """Threshold detection should be case insensitive."""
        assert get_threshold("src/APP.TS") == 400
        assert get_threshold("src/MAIN.PY") == 500

    def test_empty_path(self):
        """Empty path should return default threshold."""
        assert get_threshold("") == 500
        assert get_threshold(None) == 500


class TestEdgeCases:
    """Test edge cases."""

    def test_windows_paths(self):
        """Windows-style paths should work correctly."""
        assert should_exclude("C:\\project\\node_modules\\react\\index.js")
        assert should_exclude("C:\\project\\dist\\bundle.js")

    def test_deeply_nested_test_files(self):
        """Deeply nested test files should be excluded."""
        assert should_exclude("src/features/auth/components/__tests__/Login.test.tsx")

    def test_file_with_test_in_name_but_not_test_file(self):
        """Files with 'test' in name but not test files should not be excluded."""
        # 'test_' pattern matches filenames starting with test_
        assert should_exclude("test_utils.py")
        # But 'test' in the middle of filename doesn't match
        assert not should_exclude("contest_manager.py")
        assert not should_exclude("testing_utils.py")
