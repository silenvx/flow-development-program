#!/usr/bin/env python3
"""Tests for production-url-warning.py URL matching."""

import importlib.util
import sys
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "production-url-warning.py"


def load_module():
    """Load the hook module for testing."""
    # Temporarily add hooks directory to path for common module import
    hooks_dir = str(HOOK_PATH.parent)
    sys.path.insert(0, hooks_dir)
    try:
        spec = importlib.util.spec_from_file_location("production_url_warning", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(hooks_dir)


# Load module and get functions
module = load_module()
is_production_url = module.is_production_url
is_wrong_url = module.is_wrong_url


class TestProductionUrls:
    """Test that production URLs are correctly detected."""

    def test_frontend_urls(self):
        """Frontend production URLs should be detected."""
        assert is_production_url("https://dekita.app")
        assert is_production_url("https://dekita.app/")
        assert is_production_url("https://dekita.app/room/abc123")

    def test_api_urls(self):
        """API production URLs should be detected."""
        assert is_production_url("https://api.dekita.app")
        assert is_production_url("https://api.dekita.app/")
        assert is_production_url("https://api.dekita.app/v1/rooms")

    def test_case_insensitive(self):
        """URL detection should be case insensitive."""
        assert is_production_url("https://Dekita.App")
        assert is_production_url("https://API.DEKITA.APP")


class TestNonProductionUrls:
    """Test that non-production URLs are not flagged."""

    def test_local_development(self):
        """Local development URLs should not be flagged."""
        assert not is_production_url("http://localhost:5173")
        assert not is_production_url("http://127.0.0.1:8787")

    def test_other_domains(self):
        """Other domains should not be flagged."""
        assert not is_production_url("https://google.com")
        assert not is_production_url("https://github.com")

    def test_wrong_urls_handled_separately(self):
        """Wrong URLs are handled separately and should not be flagged as production."""
        assert not is_production_url("https://dekita.pages.dev")


class TestFalsePositivePrevention:
    """Test that similar but different domains don't trigger false positives."""

    def test_substring_not_matched(self):
        """URLs containing dekita.app as substring should NOT match."""
        assert not is_production_url("https://mydekita.app.example.com")
        assert not is_production_url("https://example.com/dekita.app")
        assert not is_production_url("https://notdekita.app")
        assert not is_production_url("https://dekita.app.fake.com")

    def test_different_tlds(self):
        """Different TLDs should not match."""
        assert not is_production_url("https://dekita.com")
        assert not is_production_url("https://dekita.io")


class TestWrongUrls:
    """Test that wrong URLs are correctly detected."""

    def test_dekita_pages_dev(self):
        """dekita.pages.dev should be detected as wrong URL."""
        assert is_wrong_url("https://dekita.pages.dev") == "https://dekita.app"
        assert is_wrong_url("https://dekita.pages.dev/") == "https://dekita.app"
        assert is_wrong_url("https://dekita.pages.dev/room/abc") == "https://dekita.app"

    def test_case_insensitive(self):
        """Wrong URL detection should be case insensitive."""
        assert is_wrong_url("https://DEKITA.PAGES.DEV") == "https://dekita.app"


class TestWrongUrlFalsePositives:
    """Test that wrong URL detection doesn't have false positives."""

    def test_other_pages_dev(self):
        """Other pages.dev URLs should not match."""
        assert is_wrong_url("https://other.pages.dev") is None

    def test_production_url_not_wrong(self):
        """Production URL should not be flagged as wrong."""
        assert is_wrong_url("https://dekita.app") is None

    def test_substring_not_matched(self):
        """URLs containing dekita.pages.dev as substring should NOT match."""
        assert is_wrong_url("https://mydekita.pages.dev.example.com") is None


class TestEdgeCases:
    """Test edge cases."""

    def test_empty_string(self):
        """Empty string should not match."""
        assert not is_production_url("")
        assert is_wrong_url("") is None

    def test_none(self):
        """None should not match."""
        assert not is_production_url(None)
        assert is_wrong_url(None) is None

    def test_invalid_urls(self):
        """Invalid URLs should not match."""
        assert not is_production_url("not-a-url")
        assert is_wrong_url("not-a-url") is None

    def test_url_without_scheme(self):
        """URLs without scheme should not match (urlparse needs scheme for hostname)."""
        assert not is_production_url("dekita.app")
