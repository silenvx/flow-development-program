#!/usr/bin/env python3
"""Tests for tool-substitution-detector.py hook.

Issue #1887: ツール代替提案の検知
"""

import sys
from pathlib import Path

import pytest

# Add parent directory to path for imports
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Module path for patching
DETECTOR_MODULE = "tool-substitution-detector"

# We need to import the module dynamically due to the hyphenated name
import importlib.util

spec = importlib.util.spec_from_file_location(
    "tool_substitution_detector",
    Path(__file__).parent.parent / "tool-substitution-detector.py",
)
detector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(detector)


class TestExtractToolInfo:
    """Tests for extract_tool_info function."""

    def test_uvx_command(self):
        """Should extract package from uvx command."""
        manager, package = detector.extract_tool_info("uvx gitleaks")
        assert manager == "uvx"
        assert package == "gitleaks"

    def test_uvx_with_arguments(self):
        """Should extract package even with arguments."""
        manager, package = detector.extract_tool_info("uvx detect-secrets scan --baseline")
        assert manager == "uvx"
        assert package == "detect-secrets"

    def test_npm_install(self):
        """Should extract package from npm install."""
        manager, package = detector.extract_tool_info("npm install eslint")
        assert manager == "npm"
        assert package == "eslint"

    def test_npm_i_shorthand(self):
        """Should extract package from npm i shorthand."""
        manager, package = detector.extract_tool_info("npm i typescript")
        assert manager == "npm"
        assert package == "typescript"

    def test_npm_add(self):
        """Should extract package from npm add."""
        manager, package = detector.extract_tool_info("npm add lodash")
        assert manager == "npm"
        assert package == "lodash"

    def test_pip_install(self):
        """Should extract package from pip install."""
        manager, package = detector.extract_tool_info("pip install requests")
        assert manager == "pip"
        assert package == "requests"

    def test_brew_install(self):
        """Should extract package from brew install."""
        manager, package = detector.extract_tool_info("brew install gitleaks")
        assert manager == "brew"
        assert package == "gitleaks"

    def test_cargo_install(self):
        """Should extract package from cargo install."""
        manager, package = detector.extract_tool_info("cargo install ripgrep")
        assert manager == "cargo"
        assert package == "ripgrep"

    def test_cargo_add(self):
        """Should extract package from cargo add."""
        manager, package = detector.extract_tool_info("cargo add serde")
        assert manager == "cargo"
        assert package == "serde"

    def test_go_install(self):
        """Should extract package from go install."""
        manager, package = detector.extract_tool_info(
            "go install github.com/golangci/golangci-lint@latest"
        )
        assert manager == "go"
        # Version specifier should be stripped
        assert package == "github.com/golangci/golangci-lint"

    def test_uv_pip_install(self):
        """Should extract package from uv pip install."""
        manager, package = detector.extract_tool_info("uv pip install requests")
        assert manager == "uv"
        assert package == "requests"

    def test_uv_add(self):
        """Should extract package from uv add."""
        manager, package = detector.extract_tool_info("uv add httpx")
        assert manager == "uv"
        assert package == "httpx"

    def test_version_specifier_stripped(self):
        """Should strip version specifiers from package names."""
        manager, package = detector.extract_tool_info("pip install requests>=2.0.0")
        assert package == "requests"

    def test_at_version_stripped(self):
        """Should strip @version from package names."""
        manager, package = detector.extract_tool_info("npm install react@18.2.0")
        assert package == "react"

    def test_non_tool_command(self):
        """Should return None for non-tool commands."""
        manager, package = detector.extract_tool_info("git status")
        assert manager is None
        assert package is None

    def test_echo_command(self):
        """Should return None for echo commands."""
        manager, package = detector.extract_tool_info("echo 'hello world'")
        assert manager is None
        assert package is None

    def test_empty_command(self):
        """Should return None for empty command."""
        manager, package = detector.extract_tool_info("")
        assert manager is None
        assert package is None


class TestToolPatterns:
    """Tests for TOOL_PATTERNS coverage."""

    def test_all_patterns_have_valid_regex(self):
        """All patterns in TOOL_PATTERNS should be valid regex."""
        import re

        for tool, pattern in detector.TOOL_PATTERNS.items():
            try:
                re.compile(pattern)
            except re.error as e:
                pytest.fail(f"Invalid regex for {tool}: {e}")
