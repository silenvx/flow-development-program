#!/usr/bin/env python3
"""Tests for ui-check-reminder.py pattern matching."""

import importlib.util
import sys
from pathlib import Path

# Load the module with hyphenated filename
spec = importlib.util.spec_from_file_location(
    "ui_check_reminder",
    Path(__file__).parent.parent / "ui-check-reminder.py",
)
ui_check_reminder = importlib.util.module_from_spec(spec)
sys.modules["ui_check_reminder"] = ui_check_reminder
spec.loader.exec_module(ui_check_reminder)

matches_frontend_pattern = ui_check_reminder.matches_frontend_pattern


def test_locale_files():
    """Test that locale JSON files match."""
    assert matches_frontend_pattern("frontend/src/i18n/locales/en.json")
    assert matches_frontend_pattern("frontend/src/i18n/locales/ja.json")
    assert matches_frontend_pattern("frontend/src/i18n/locales/zh.json")


def test_component_files():
    """Test that component TSX files match."""
    # Direct children of components
    assert matches_frontend_pattern("frontend/src/components/Footer.tsx")
    assert matches_frontend_pattern("frontend/src/components/StatusIndicator.tsx")
    # Nested components
    assert matches_frontend_pattern("frontend/src/components/admin/SeatMap.tsx")
    assert matches_frontend_pattern("frontend/src/components/admin/ParticipantList.tsx")
    assert matches_frontend_pattern("frontend/src/components/dialogs/ConfirmDialog.tsx")
    # Deeply nested
    assert matches_frontend_pattern("frontend/src/components/a/b/c/DeepComponent.tsx")


def test_route_files():
    """Test that route TSX files match."""
    assert matches_frontend_pattern("frontend/src/routes/index.tsx")
    assert matches_frontend_pattern("frontend/src/routes/privacy.tsx")
    assert matches_frontend_pattern("frontend/src/routes/$urlId/index.tsx")
    assert matches_frontend_pattern("frontend/src/routes/$urlId/admin.tsx")


def test_css_files():
    """Test that global CSS file matches."""
    assert matches_frontend_pattern("frontend/src/index.css")


def test_typescript_files():
    """Test that TypeScript files in frontend/src match (expanded in Issue #209)."""
    # lib files - analytics, api, etc.
    assert matches_frontend_pattern("frontend/src/lib/api.ts")
    assert matches_frontend_pattern("frontend/src/lib/analytics.ts")
    assert matches_frontend_pattern("frontend/src/lib/notifications.ts")
    # hooks
    assert matches_frontend_pattern("frontend/src/hooks/useAdminRoom.ts")
    assert matches_frontend_pattern("frontend/src/hooks/useQRDataUrl.ts")
    # workers
    assert matches_frontend_pattern("frontend/src/workers/polling.worker.ts")
    # main.tsx
    assert matches_frontend_pattern("frontend/src/main.tsx")
    # index.ts barrel files
    assert matches_frontend_pattern("frontend/src/components/index.ts")
    assert matches_frontend_pattern("frontend/src/components/admin/index.ts")


def test_non_frontend_files():
    """Test that non-frontend files don't match."""
    # Worker (backend) files
    assert not matches_frontend_pattern("worker/src/index.ts")
    assert not matches_frontend_pattern("worker/src/routes/room.ts")
    # Config files
    assert not matches_frontend_pattern("frontend/vite.config.ts")
    assert not matches_frontend_pattern("frontend/tsconfig.json")
    # Root config files
    assert not matches_frontend_pattern("tsconfig.json")
    assert not matches_frontend_pattern("package.json")
    # Shared package
    assert not matches_frontend_pattern("shared/src/types.ts")
    # Other CSS files (not index.css)
    assert not matches_frontend_pattern("frontend/src/other.css")
    # Files outside src
    assert not matches_frontend_pattern("frontend/public/manifest.json")


def test_edge_cases():
    """Test edge cases."""
    # Empty string
    assert not matches_frontend_pattern("")
    # Note: frontend/src/**/*.ts now matches ALL .ts files, including unusual locations
    # This is intentional - any frontend source file should trigger browser verification
    assert matches_frontend_pattern("frontend/src/i18n/locales/en.ts")  # .ts files match everywhere
    # Paths that look similar but aren't in frontend/src
    assert not matches_frontend_pattern("other-frontend/src/lib/api.ts")
    assert not matches_frontend_pattern("frontend/test/lib/api.ts")
    # Files with wrong extensions
    assert not matches_frontend_pattern("frontend/src/lib/api.js")  # .js not .ts
    assert not matches_frontend_pattern("frontend/src/components/Foo.jsx")  # .jsx not .tsx


if __name__ == "__main__":
    test_locale_files()
    test_component_files()
    test_route_files()
    test_css_files()
    test_typescript_files()
    test_non_frontend_files()
    test_edge_cases()
    print("All tests passed!")
