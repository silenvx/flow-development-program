#!/usr/bin/env python3
"""Unit tests for e2e-test-recorder.py"""

import importlib.util
import sys
from pathlib import Path

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# e2e-test-recorder.py has hyphens, so we need dynamic import
HOOK_PATH = Path(__file__).parent.parent / "e2e-test-recorder.py"
_spec = importlib.util.spec_from_file_location("e2e_test_recorder", HOOK_PATH)
e2e_test_recorder = importlib.util.module_from_spec(_spec)
sys.modules["e2e_test_recorder"] = e2e_test_recorder
_spec.loader.exec_module(e2e_test_recorder)

is_e2e_test_command = e2e_test_recorder.is_e2e_test_command


class TestIsE2eTestCommand:
    """Tests for is_e2e_test_command function."""

    def test_detects_npm_test_e2e(self):
        """Should detect npm run test:e2e commands."""
        assert is_e2e_test_command("npm run test:e2e")
        assert is_e2e_test_command("npm run test:e2e:chromium")
        assert is_e2e_test_command("npm run test:e2e:chromium -- tests/stories/")

    def test_detects_pnpm_test_e2e(self):
        """Should detect pnpm test:e2e commands."""
        assert is_e2e_test_command("pnpm test:e2e")
        assert is_e2e_test_command("pnpm test:e2e:chromium")
        assert is_e2e_test_command("pnpm run test:e2e")
        assert is_e2e_test_command("pnpm run test:e2e:chromium -- tests/stories/")

    def test_detects_npx_playwright(self):
        """Should detect npx playwright test commands."""
        assert is_e2e_test_command("npx playwright test")
        assert is_e2e_test_command("npx playwright test tests/")

    def test_ignores_other_commands(self):
        """Should not flag other commands."""
        assert not is_e2e_test_command("npm run build")
        assert not is_e2e_test_command("npm test")
        assert not is_e2e_test_command("git status")

    def test_ignores_empty(self):
        """Should not flag empty commands."""
        assert not is_e2e_test_command("")
        assert not is_e2e_test_command("   ")
