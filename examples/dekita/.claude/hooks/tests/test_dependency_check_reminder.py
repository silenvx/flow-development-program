#!/usr/bin/env python3
"""Unit tests for dependency-check-reminder.py"""

import importlib.util
import sys
from pathlib import Path

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Import with dynamic loading due to hyphens in filename
HOOK_PATH = Path(__file__).parent.parent / "dependency-check-reminder.py"
_spec = importlib.util.spec_from_file_location("dependency_check_reminder", HOOK_PATH)
dependency_check_reminder = importlib.util.module_from_spec(_spec)
sys.modules["dependency_check_reminder"] = dependency_check_reminder
_spec.loader.exec_module(dependency_check_reminder)


class TestDetectDependencyCommand:
    """Tests for detect_dependency_command function."""

    def test_pnpm_add(self):
        """Should detect pnpm add commands."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command("pnpm add lodash")
        assert cmd_type == "pnpm add"
        assert package == "lodash"

    def test_pnpm_add_with_flags(self):
        """Should detect pnpm add with flags before package."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command(
            "pnpm add -D typescript"
        )
        assert cmd_type == "pnpm add"
        # Package extraction works with flags before package name
        assert package == "typescript"

    def test_npm_install_with_save_dev(self):
        """Should detect npm install with --save-dev flag."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command(
            "npm install --save-dev react"
        )
        assert cmd_type == "npm install"
        assert package == "react"

    def test_npm_i_with_D_flag(self):
        """Should detect npm i with -D flag."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command("npm i -D lodash")
        assert cmd_type == "npm i"
        assert package == "lodash"

    def test_npm_install(self):
        """Should detect npm install commands."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command(
            "npm install express"
        )
        assert cmd_type == "npm install"
        assert package == "express"

    def test_npm_i_shorthand(self):
        """Should detect npm i shorthand."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command("npm i react")
        assert cmd_type == "npm i"
        assert package == "react"

    def test_pip_install(self):
        """Should detect pip install commands."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command(
            "pip install requests"
        )
        assert cmd_type == "pip install"
        assert package == "requests"

    def test_cargo_add(self):
        """Should detect cargo add commands."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command("cargo add serde")
        assert cmd_type == "cargo add"
        assert package == "serde"

    def test_yarn_add(self):
        """Should detect yarn add commands."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command("yarn add axios")
        assert cmd_type == "yarn add"
        assert package == "axios"

    def test_uv_add(self):
        """Should detect uv add commands."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command("uv add flask")
        assert cmd_type == "uv add"
        assert package == "flask"

    def test_poetry_add(self):
        """Should detect poetry add commands."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command("poetry add django")
        assert cmd_type == "poetry add"
        assert package == "django"

    def test_non_dependency_command(self):
        """Should return None for non-dependency commands."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command("git status")
        assert cmd_type is None
        assert package is None

    def test_npm_install_without_package(self):
        """Should not match npm install without package (installs from package.json)."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command("npm install")
        # This should not match because it's just installing existing deps
        # The regex excludes npm install without args
        assert cmd_type is None

    def test_pip_install_requirements(self):
        """Should not match pip install -r requirements.txt."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command(
            "pip install -r requirements.txt"
        )
        # This should not match because it's installing from requirements file
        assert cmd_type is None

    def test_pip_install_with_flags_and_requirements(self):
        """Should not match pip install -U -r requirements.txt."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command(
            "pip install -U -r requirements.txt"
        )
        # This should not match because it's installing from requirements file
        assert cmd_type is None

    def test_pip_install_long_requirement_flag(self):
        """Should not match pip install --requirement requirements.txt."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command(
            "pip install --requirement requirements.txt"
        )
        # This should not match because it's installing from requirements file
        assert cmd_type is None

    def test_package_with_version(self):
        """Should extract package name without version."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command(
            "pnpm add lodash@4.17.21"
        )
        assert cmd_type == "pnpm add"
        assert package == "lodash"

    def test_package_with_caret_version(self):
        """Should extract package name without caret version."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command(
            "npm install react@^18.0.0"
        )
        assert cmd_type == "npm install"
        assert package == "react"

    def test_scoped_package(self):
        """Should handle scoped packages."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command(
            "pnpm add @types/node"
        )
        assert cmd_type == "pnpm add"
        assert package == "@types/node"

    def test_scoped_package_with_version(self):
        """Should handle scoped packages with version specifier."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command(
            "pnpm add @types/node@18.0.0"
        )
        assert cmd_type == "pnpm add"
        assert package == "@types/node"

    def test_scoped_package_with_caret_version(self):
        """Should handle scoped packages with caret version specifier."""
        cmd_type, package = dependency_check_reminder.detect_dependency_command(
            "npm install @tanstack/react-query@^5.0.0"
        )
        assert cmd_type == "npm install"
        assert package == "@tanstack/react-query"


class TestFormatReminderMessage:
    """Tests for format_reminder_message function."""

    def test_message_with_package(self):
        """Should include package name in message."""
        message = dependency_check_reminder.format_reminder_message("pnpm add", "lodash")
        assert "lodash" in message
        assert "Context7" in message
        assert "Web検索" in message

    def test_message_without_package(self):
        """Should work without package name."""
        message = dependency_check_reminder.format_reminder_message("pnpm add", None)
        assert "依存関係" in message
        assert "Context7" in message

    def test_message_contains_emoji(self):
        """Should contain emoji icons."""
        message = dependency_check_reminder.format_reminder_message("pnpm add", "lodash")
        assert "📦" in message
        assert "💡" in message


class TestRemindedPackagesFile:
    """Tests for reminded packages file path."""

    def test_file_path_is_in_session_dir(self):
        """File should be in SESSION_DIR."""
        test_session_id = "test-session-123"
        file_path = dependency_check_reminder.get_reminded_packages_file(test_session_id)
        from common import SESSION_DIR

        assert file_path.parent == SESSION_DIR

    def test_file_has_json_extension(self):
        """File should have .json extension."""
        test_session_id = "test-session-123"
        file_path = dependency_check_reminder.get_reminded_packages_file(test_session_id)
        assert file_path.name.endswith(".json")

    def test_file_includes_session_id(self):
        """File path should include session ID to avoid conflicts."""
        session_a = "session-a"
        session_b = "session-b"
        path_a = dependency_check_reminder.get_reminded_packages_file(session_a)
        path_b = dependency_check_reminder.get_reminded_packages_file(session_b)

        # Paths should be different for different sessions
        assert path_a != path_b
        assert session_a in str(path_a)
        assert session_b in str(path_b)
