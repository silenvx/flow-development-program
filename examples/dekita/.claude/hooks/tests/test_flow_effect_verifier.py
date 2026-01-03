#!/usr/bin/env python3
# Design reviewed: 2025-12-22
"""Tests for flow-effect-verifier.py display formatting."""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch


# Load modules with hyphenated filenames
def load_module(name: str, filename: str):
    """Load a Python module from a hyphenated filename."""
    spec = importlib.util.spec_from_file_location(
        name,
        Path(__file__).parent.parent / filename,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


flow_effect_verifier = load_module("flow_effect_verifier", "flow-effect-verifier.py")


class TestFormatWorkflowVerificationSummary:
    """Tests for format_workflow_verification_summary function."""

    def test_vertical_phase_display(self):
        """Test that phases are displayed in vertical format."""
        mock_verifier = MagicMock()
        mock_verifier.get_summary_dict.return_value = {
            "current_phase": "implementation",
            "fired_hooks": 5,
            "unfired_hooks": 10,
            "phases": [
                {"phase_id": "session_start", "phase_name": "セッション開始", "status": "complete"},
                {"phase_id": "pre_check", "phase_name": "事前確認", "status": "complete"},
                {"phase_id": "implementation", "phase_name": "実装", "status": "partial"},
                {"phase_id": "pr_create", "phase_name": "PR作成", "status": "not_started"},
            ],
            "issues": [],
        }

        result = flow_effect_verifier.format_workflow_verification_summary(mock_verifier)

        # Verify header format with current phase and hook stats
        assert "📍 implementation | 🪝 5/15" in result

        # Verify vertical format with each phase on its own line
        assert "✅ セッション開始" in result
        assert "✅ 事前確認" in result
        assert "⏳ 実装" in result
        assert "⬜ PR作成" in result

        # Verify current phase has marker
        assert "実装 ←" in result

    def test_current_phase_marker(self):
        """Test that current phase has the ← marker."""
        mock_verifier = MagicMock()
        mock_verifier.get_summary_dict.return_value = {
            "current_phase": "pr_create",
            "fired_hooks": 3,
            "unfired_hooks": 12,
            "phases": [
                {"phase_id": "implementation", "phase_name": "実装", "status": "complete"},
                {"phase_id": "pr_create", "phase_name": "PR作成", "status": "partial"},
            ],
            "issues": [],
        }

        result = flow_effect_verifier.format_workflow_verification_summary(mock_verifier)

        # Current phase should have marker
        assert "PR作成 ←" in result
        # Non-current phase should NOT have marker
        assert "実装 ←" not in result

    def test_header_includes_hook_stats(self):
        """Test that header includes fired/total hooks count."""
        mock_verifier = MagicMock()
        mock_verifier.get_summary_dict.return_value = {
            "current_phase": "ci_review",
            "fired_hooks": 8,
            "unfired_hooks": 7,
            "phases": [],
            "issues": [],
        }

        result = flow_effect_verifier.format_workflow_verification_summary(mock_verifier)

        # Header should show: fired_hooks / (fired_hooks + unfired_hooks)
        assert "🪝 8/15" in result

    def test_phase_status_icons(self):
        """Test that correct icons are used for each phase status."""
        mock_verifier = MagicMock()
        mock_verifier.get_summary_dict.return_value = {
            "current_phase": None,
            "fired_hooks": 0,
            "unfired_hooks": 15,
            "phases": [
                {"phase_id": "a", "phase_name": "Complete Phase", "status": "complete"},
                {"phase_id": "b", "phase_name": "Partial Phase", "status": "partial"},
                {"phase_id": "c", "phase_name": "Not Started", "status": "not_started"},
                {"phase_id": "d", "phase_name": "No Hooks", "status": "no_hooks"},
            ],
            "issues": [],
        }

        result = flow_effect_verifier.format_workflow_verification_summary(mock_verifier)

        # Check icons for each status
        assert "✅ Complete Phase" in result
        assert "⏳ Partial Phase" in result
        assert "⬜ Not Started" in result
        assert "➖ No Hooks" in result

    def test_issues_displayed(self):
        """Test that issues are displayed when present."""
        mock_verifier = MagicMock()
        mock_verifier.get_summary_dict.return_value = {
            "current_phase": "implementation",
            "fired_hooks": 3,
            "unfired_hooks": 12,
            "phases": [],
            "issues": [
                {"hook": "test-hook", "message": "Unexpected block"},
                {"hook": "another-hook", "message": "Missing execution"},
            ],
        }

        result = flow_effect_verifier.format_workflow_verification_summary(mock_verifier)

        # Check issues section
        assert "⚠️ 検出された問題" in result
        assert "test-hook" in result
        assert "Unexpected block" in result
        assert "another-hook" in result

    def test_issues_limited_to_five(self):
        """Test that only first 5 issues are shown with count of remaining."""
        mock_verifier = MagicMock()
        mock_verifier.get_summary_dict.return_value = {
            "current_phase": None,
            "fired_hooks": 0,
            "unfired_hooks": 15,
            "phases": [],
            "issues": [{"hook": f"hook-{i}", "message": f"Issue {i}"} for i in range(8)],
        }

        result = flow_effect_verifier.format_workflow_verification_summary(mock_verifier)

        # Should show first 5 issues
        assert "hook-0" in result
        assert "hook-4" in result
        # Should NOT show 6th and beyond
        assert "hook-5" not in result
        # Should show count of remaining
        assert "他 3 件" in result

    def test_no_phases_empty_output(self):
        """Test output when there are no phases."""
        mock_verifier = MagicMock()
        mock_verifier.get_summary_dict.return_value = {
            "current_phase": None,
            "fired_hooks": 0,
            "unfired_hooks": 0,
            "phases": [],
            "issues": [],
        }

        result = flow_effect_verifier.format_workflow_verification_summary(mock_verifier)

        # Should still have header
        assert "[workflow-verification]" in result
        assert "📍 unknown" in result

    def test_state_file_phases_used_when_session_id_provided(self):
        """Test that state file phases are used when session_id is provided (Issue #2478)."""
        mock_verifier = MagicMock()
        mock_verifier.get_summary_dict.return_value = {
            "current_phase": "session_start",  # Verifier would say session_start
            "fired_hooks": 5,
            "unfired_hooks": 10,
            "phases": [],  # Verifier has no phases
            "issues": [],
        }

        # Create a mock state file with phases matching flow_definitions.py
        # Phase IDs: session_start, pre_check, worktree_create, implementation,
        #            pre_commit_check, local_ai_review, pr_create, issue_work,
        #            ci_review, merge, cleanup, production, session_end
        state_data = {
            "session_id": "test-session-123",
            "active_workflow": "issue-123",
            "workflows": {
                "issue-123": {
                    "current_phase": "ci_review",
                    "phases": {
                        "session_start": {"status": "completed"},
                        "pre_check": {"status": "completed"},
                        "worktree_create": {"status": "completed"},
                        "implementation": {"status": "completed"},
                        "pre_commit_check": {"status": "completed"},
                        "local_ai_review": {"status": "completed"},
                        "pr_create": {"status": "completed"},
                        "issue_work": {"status": "completed"},
                        "ci_review": {"status": "in_progress"},
                    },
                },
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state-test-session-123.json"
            state_file.write_text(json.dumps(state_data))

            # Patch FLOW_LOG_DIR to use temp directory
            with patch.object(flow_effect_verifier, "FLOW_LOG_DIR", Path(tmpdir)):
                result = flow_effect_verifier.format_workflow_verification_summary(
                    mock_verifier, session_id="test-session-123"
                )

        # Should show ci_review as current phase (from state file), not session_start
        assert "📍 ci_review" in result
        # Should show workflow name
        assert "[issue-123]" in result
        # Should show completed phases from state file
        assert "✅ セッション開始" in result
        assert "✅ 事前確認" in result
        assert "✅ Worktree作成" in result
        assert "✅ 実装" in result
        assert "✅ コミット前検証" in result
        assert "✅ PR作成" in result
        # Current phase should have marker (ci_review = "CI監視+レビュー対応")
        assert "CI監視+レビュー対応 ←" in result

    def test_workflow_name_displayed_in_header(self):
        """Test that workflow name is displayed in header when available (Issue #2478)."""
        mock_verifier = MagicMock()
        mock_verifier.get_summary_dict.return_value = {
            "current_phase": None,
            "fired_hooks": 3,
            "unfired_hooks": 7,
            "phases": [],
            "issues": [],
        }

        state_data = {
            "session_id": "test-session",
            "active_workflow": "feat-new-feature",
            "workflows": {
                "feat-new-feature": {
                    "current_phase": "implementation",
                    "phases": {"implementation": {"status": "in_progress"}},
                },
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state-test-session.json"
            state_file.write_text(json.dumps(state_data))

            with patch.object(flow_effect_verifier, "FLOW_LOG_DIR", Path(tmpdir)):
                result = flow_effect_verifier.format_workflow_verification_summary(
                    mock_verifier, session_id="test-session"
                )

        # Header should include workflow name
        assert "[feat-new-feature]" in result
        assert "📍 implementation [feat-new-feature]" in result

    def test_multiple_workflows_uses_active_workflow(self):
        """Test that active_workflow is used when multiple workflows exist (Issue #2487).

        Scenario: Session completed issue-100 (session_end phase) and now working on issue-200
        (implementation phase). Should display issue-200, not issue-100.
        """
        mock_verifier = MagicMock()
        mock_verifier.get_summary_dict.return_value = {
            "current_phase": None,
            "fired_hooks": 10,
            "unfired_hooks": 5,
            "phases": [],
            "issues": [],
        }

        state_data = {
            "session_id": "multi-workflow-session",
            "active_workflow": "issue-200",  # Currently working on this
            "workflows": {
                "issue-100": {
                    "current_phase": "session_end",  # Completed - higher phase order
                    "phases": {
                        "session_start": {"status": "completed"},
                        "implementation": {"status": "completed"},
                        "pr_create": {"status": "completed"},
                        "ci_review": {"status": "completed"},
                        "merge": {"status": "completed"},
                        "cleanup": {"status": "completed"},
                        "session_end": {"status": "completed"},
                    },
                },
                "issue-200": {
                    "current_phase": "implementation",  # In progress - lower phase order
                    "phases": {
                        "session_start": {"status": "completed"},
                        "pre_check": {"status": "completed"},
                        "worktree_create": {"status": "completed"},
                        "implementation": {"status": "in_progress"},
                    },
                },
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state-multi-workflow-session.json"
            state_file.write_text(json.dumps(state_data))

            with patch.object(flow_effect_verifier, "FLOW_LOG_DIR", Path(tmpdir)):
                result = flow_effect_verifier.format_workflow_verification_summary(
                    mock_verifier, session_id="multi-workflow-session"
                )

        # Should show active_workflow (issue-200), not the completed one (issue-100)
        assert "[issue-200]" in result
        assert "[issue-100]" not in result
        # Should show implementation phase (from issue-200)
        assert "📍 implementation" in result
        # Should show issue-200's completed phases
        assert "✅ セッション開始" in result
        assert "✅ Worktree作成" in result
        # Current phase should have marker
        assert "実装 ←" in result

    def test_fallback_to_verifier_when_no_state_file(self):
        """Test that verifier phases are used when no state file exists."""
        mock_verifier = MagicMock()
        mock_verifier.get_summary_dict.return_value = {
            "current_phase": "implementation",
            "fired_hooks": 5,
            "unfired_hooks": 10,
            "phases": [
                {"phase_id": "session_start", "phase_name": "セッション開始", "status": "complete"},
                {"phase_id": "implementation", "phase_name": "実装", "status": "partial"},
            ],
            "issues": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            # No state file exists in tmpdir
            with patch.object(flow_effect_verifier, "FLOW_LOG_DIR", Path(tmpdir)):
                result = flow_effect_verifier.format_workflow_verification_summary(
                    mock_verifier, session_id="nonexistent-session"
                )

        # Should use verifier's phases
        assert "📍 implementation |" in result
        # Should NOT have workflow name in header (no state file)
        # Find the actual header line (first non-empty line)
        header_line = next((line for line in result.splitlines() if line.strip()), "")
        # Header should have "[workflow-verification]" but no other "[xxx]" (workflow name)
        prefix = "[workflow-verification]"
        if prefix in header_line:
            after_prefix = header_line[header_line.index(prefix) + len(prefix) :]
            assert "[" not in after_prefix, f"Unexpected workflow name in header: {header_line}"
        # Verifier phases should be displayed
        assert "✅ セッション開始" in result
        assert "⏳ 実装" in result


class TestAggregateWorkflowPhases:
    """Tests for aggregate_workflow_phases function (Issue #2494)."""

    def test_empty_workflows(self):
        """Test that empty workflows returns empty dict."""
        state = {"workflows": {}}
        result = flow_effect_verifier.aggregate_workflow_phases(state)
        assert result == {}

    def test_missing_workflows_key(self):
        """Test that missing workflows key returns empty dict."""
        state = {}
        result = flow_effect_verifier.aggregate_workflow_phases(state)
        assert result == {}

    def test_single_workflow(self):
        """Test aggregation with single workflow."""
        state = {
            "workflows": {
                "issue-123": {
                    "phases": {
                        "session_start": {"status": "completed", "iterations": 1},
                        "implementation": {"status": "in_progress", "iterations": 2},
                    }
                }
            }
        }
        result = flow_effect_verifier.aggregate_workflow_phases(state)

        assert result["session_start"]["status"] == "completed"
        assert result["session_start"]["iterations"] == 1
        assert result["implementation"]["status"] == "in_progress"
        assert result["implementation"]["iterations"] == 2

    def test_multiple_workflows_best_status_wins(self):
        """Test that best status is kept when same phase exists in multiple workflows."""
        state = {
            "workflows": {
                "issue-100": {
                    "phases": {
                        "session_start": {"status": "completed", "iterations": 1},
                        "implementation": {"status": "pending", "iterations": 1},
                    }
                },
                "issue-200": {
                    "phases": {
                        "session_start": {"status": "in_progress", "iterations": 1},
                        "implementation": {"status": "completed", "iterations": 2},
                    }
                },
            }
        }
        result = flow_effect_verifier.aggregate_workflow_phases(state)

        # session_start: completed (3) > in_progress (2)
        assert result["session_start"]["status"] == "completed"
        # implementation: completed (3) > pending (1)
        assert result["implementation"]["status"] == "completed"

    def test_iterations_summed(self):
        """Test that iterations are summed across workflows."""
        state = {
            "workflows": {
                "issue-100": {
                    "phases": {
                        "session_start": {"status": "completed", "iterations": 3},
                    }
                },
                "issue-200": {
                    "phases": {
                        "session_start": {"status": "completed", "iterations": 2},
                    }
                },
            }
        }
        result = flow_effect_verifier.aggregate_workflow_phases(state)

        # Iterations should be summed: 3 + 2 = 5
        assert result["session_start"]["iterations"] == 5

    def test_unknown_status_uses_default_priority(self):
        """Test that unknown status gets priority 0 (lowest)."""
        state = {
            "workflows": {
                "issue-100": {
                    "phases": {
                        "session_start": {"status": "unknown_status", "iterations": 1},
                    }
                },
                "issue-200": {
                    "phases": {
                        "session_start": {"status": "pending", "iterations": 1},
                    }
                },
            }
        }
        result = flow_effect_verifier.aggregate_workflow_phases(state)

        # pending (1) > unknown_status (0)
        assert result["session_start"]["status"] == "pending"

    def test_different_phases_from_different_workflows(self):
        """Test that phases unique to each workflow are all included."""
        state = {
            "workflows": {
                "issue-100": {
                    "phases": {
                        "session_start": {"status": "completed", "iterations": 1},
                        "pr_create": {"status": "completed", "iterations": 1},
                    }
                },
                "issue-200": {
                    "phases": {
                        "implementation": {"status": "in_progress", "iterations": 1},
                    }
                },
            }
        }
        result = flow_effect_verifier.aggregate_workflow_phases(state)

        # All phases from all workflows should be included
        assert "session_start" in result
        assert "pr_create" in result
        assert "implementation" in result
        assert len(result) == 3

    def test_workflow_with_empty_phases(self):
        """Test handling of workflow with empty phases dict."""
        state = {
            "workflows": {
                "issue-100": {"phases": {}},
                "issue-200": {
                    "phases": {
                        "session_start": {"status": "completed", "iterations": 1},
                    }
                },
            }
        }
        result = flow_effect_verifier.aggregate_workflow_phases(state)

        # Only session_start from issue-200 should be present
        assert len(result) == 1
        assert result["session_start"]["status"] == "completed"

    def test_workflow_missing_phases_key(self):
        """Test handling of workflow without phases key."""
        state = {
            "workflows": {
                "issue-100": {},  # No phases key
                "issue-200": {
                    "phases": {
                        "session_start": {"status": "completed", "iterations": 1},
                    }
                },
            }
        }
        result = flow_effect_verifier.aggregate_workflow_phases(state)

        # Only session_start from issue-200 should be present
        assert len(result) == 1
        assert result["session_start"]["status"] == "completed"

    def test_current_workflow_phases_only_in_format_summary(self):
        """Integration test: verify only current workflow's phases are displayed (Issue #2600).

        Scenario:
        - Multiple workflows exist (issue-100 and issue-200)
        - issue-100 has pr_create, ci_review, merge, cleanup completed
        - issue-200 (active_workflow) has pr_create in_progress
        - Display should show ONLY issue-200's phases, NOT aggregated from all workflows
        - This fixes misleading display where phases from other workflows appeared as completed

        Background (Issue #2600):
        Previously (Issue #2494), phases were aggregated from all workflows, which caused
        misleading display where the current phase was "pre_check" but later phases like
        "implementation", "merge" appeared as completed because they were completed in
        OTHER workflows.
        """
        mock_verifier = MagicMock()
        mock_verifier.get_summary_dict.return_value = {
            "current_phase": None,
            "fired_hooks": 10,
            "unfired_hooks": 5,
            "phases": [],
            "issues": [],
        }

        state_data = {
            "session_id": "multi-workflow-session",
            "active_workflow": "issue-200",
            "workflows": {
                "issue-100": {
                    "current_phase": "cleanup",
                    "phases": {
                        "session_start": {"status": "completed", "iterations": 1},
                        "pr_create": {"status": "completed", "iterations": 1},
                        "ci_review": {"status": "completed", "iterations": 1},
                        "merge": {"status": "completed", "iterations": 1},
                        "cleanup": {"status": "completed", "iterations": 1},
                    },
                },
                "issue-200": {
                    "current_phase": "ci_review",
                    "phases": {
                        "session_start": {"status": "completed", "iterations": 1},
                        "pre_check": {"status": "completed", "iterations": 1},
                        "implementation": {"status": "completed", "iterations": 1},
                        "pr_create": {"status": "in_progress", "iterations": 1},
                    },
                },
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state-multi-workflow-session.json"
            state_file.write_text(json.dumps(state_data))

            with patch.object(flow_effect_verifier, "FLOW_LOG_DIR", Path(tmpdir)):
                result = flow_effect_verifier.format_workflow_verification_summary(
                    mock_verifier, session_id="multi-workflow-session"
                )

        # Issue #2600: Only current workflow's phases should be shown
        # pr_create: issue-200 has "in_progress" -> Should show ⏳ (not ✅ from issue-100)
        assert "⏳ PR作成" in result

        # ci_review: issue-200 doesn't have this phase -> Should show ⬜ (not ✅ from issue-100)
        assert "⬜ CI監視+レビュー対応" in result

        # merge: issue-200 doesn't have this phase -> Should show ⬜ (not ✅ from issue-100)
        assert "⬜ マージ" in result

        # cleanup: issue-200 doesn't have this phase -> Should show ⬜ (not ✅ from issue-100)
        assert "⬜ クリーンアップ" in result
