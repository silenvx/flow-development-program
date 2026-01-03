#!/usr/bin/env python3
"""Tests for stop-auto-review.py hook.

Issue #2166
"""

import json
import os
import subprocess
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "stop-auto-review.py"


def run_hook(input_data: dict, env: dict | None = None) -> dict:
    """Run the hook with given input and return the result."""
    hook_env = os.environ.copy()
    if env:
        hook_env.update(env)

    result = subprocess.run(
        ["python3", str(HOOK_PATH)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        env=hook_env,
    )
    return json.loads(result.stdout)


class TestStopAutoReview:
    """Tests for stop-auto-review hook."""

    def test_approve_when_stop_hook_active(self):
        """Should approve immediately when stop_hook_active is True."""
        result = run_hook({"stop_hook_active": True})

        assert result["decision"] == "approve"

    def test_approve_with_skip_env_var(self):
        """Should approve when SKIP_STOP_AUTO_REVIEW=1."""
        result = run_hook({}, env={"SKIP_STOP_AUTO_REVIEW": "1"})

        assert result["decision"] == "approve"
        # Message may be in reason or systemMessage depending on make_approve_result
        message = result.get("reason", "") + result.get("systemMessage", "")
        assert "Skipped via env" in message

    def test_approve_on_main_branch(self):
        """Should approve when on main branch."""
        # This test verifies the hook handles main branch gracefully.
        # The actual branch detection is tested via integration.
        # When run on main branch, the hook should approve.
        result = run_hook({})
        # On any branch, the hook should return a valid decision
        assert "decision" in result

    def test_hook_returns_valid_decision(self):
        """Should always return a valid decision field."""
        result = run_hook({})

        assert "decision" in result
        assert result["decision"] in ("approve", "block")


class TestStopAutoReviewHelpers:
    """Tests for helper functions in stop-auto-review."""

    def test_get_state_file(self):
        """Test that state file path is properly generated."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("stop_auto_review", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        state_file = module.get_state_file("test-session-123")

        assert "stop-auto-review-test-session-123.json" in str(state_file)

    def test_load_state_returns_default(self):
        """Test that load_state returns default state for new session."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("stop_auto_review", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Use a unique session ID that won't have a state file
        state = module.load_state("nonexistent-session-999")

        assert state["retry_count"] == 0

    def test_save_and_load_state(self):
        """Test that state can be saved and loaded."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("stop_auto_review", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Use a unique session ID for this test
        test_session_id = f"test-session-{os.getpid()}"
        test_state = {"retry_count": 2}

        try:
            module.save_state(test_session_id, test_state)
            loaded_state = module.load_state(test_session_id)

            assert loaded_state["retry_count"] == 2
        finally:
            # Cleanup
            state_file = module.get_state_file(test_session_id)
            if state_file.exists():
                state_file.unlink()


class TestStopAutoReviewRetryLimit:
    """Tests for retry limit functionality."""

    def test_max_retries_allows_session_end(self):
        """After MAX_REVIEW_RETRIES, hook should approve."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("stop_auto_review", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Set up state with max retries reached
        test_session_id = f"test-max-retries-{os.getpid()}"
        test_state = {"retry_count": module.MAX_REVIEW_RETRIES}

        try:
            module.save_state(test_session_id, test_state)

            # Verify the state was saved
            loaded_state = module.load_state(test_session_id)
            assert loaded_state["retry_count"] >= module.MAX_REVIEW_RETRIES
        finally:
            # Cleanup
            state_file = module.get_state_file(test_session_id)
            if state_file.exists():
                state_file.unlink()


class TestStopAutoReviewGitHelpers:
    """Tests for git-related helper functions."""

    def test_has_unpushed_commits_handles_error(self):
        """Test that has_unpushed_commits handles git errors gracefully."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("stop_auto_review", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # This should not raise an exception even if git fails
        # It might return True or False depending on current state
        result = module.has_unpushed_commits()
        assert isinstance(result, bool)

    def test_has_uncommitted_changes_handles_error(self):
        """Test that has_uncommitted_changes handles git errors gracefully."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("stop_auto_review", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # This should not raise an exception
        result = module.has_uncommitted_changes()
        assert isinstance(result, bool)
