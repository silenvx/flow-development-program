#!/usr/bin/env python3
"""
Test for flow_constants module.

Issue #1352: Verify the shared OPTIONAL_PHASES constant.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from flow_constants import OPTIONAL_PHASES


class TestOptionalPhases:
    """Tests for OPTIONAL_PHASES constant."""

    def test_optional_phases_is_set(self) -> None:
        """OPTIONAL_PHASES should be a set for O(1) lookup."""
        assert isinstance(OPTIONAL_PHASES, set)

    def test_optional_phases_not_empty(self) -> None:
        """OPTIONAL_PHASES should have at least one phase."""
        assert len(OPTIONAL_PHASES) > 0

    def test_optional_phases_contains_expected_values(self) -> None:
        """OPTIONAL_PHASES should contain the known optional phases."""
        expected = {"worktree_create", "local_ai_review", "issue_work", "production_check"}
        assert OPTIONAL_PHASES == expected

    def test_optional_phases_are_strings(self) -> None:
        """All elements in OPTIONAL_PHASES should be strings."""
        for phase in OPTIONAL_PHASES:
            assert isinstance(phase, str)

    def test_optional_phases_are_lowercase(self) -> None:
        """All phase names should be lowercase with underscores."""
        for phase in OPTIONAL_PHASES:
            assert phase == phase.lower()
            assert " " not in phase
