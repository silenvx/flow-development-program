#!/usr/bin/env python3
"""Tests for block-evaluator.py"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Load block_evaluator module directly from file
_script_path = Path(__file__).parents[1] / "block-evaluator.py"
_spec = importlib.util.spec_from_file_location("block_evaluator", _script_path)
be = importlib.util.module_from_spec(_spec)
sys.modules["block_evaluator"] = be
_spec.loader.exec_module(be)


def test_get_block_id_deterministic():
    """Block ID should be deterministic for same input."""
    entry = {
        "timestamp": "2025-12-16T14:35:21.841078+00:00",
        "hook": "worktree-warning",
        "branch": "main",
    }
    id1 = be.get_block_id(entry)
    id2 = be.get_block_id(entry)
    assert id1 == id2
    assert len(id1) == 12


def test_get_block_id_different_for_different_input():
    """Block ID should differ for different inputs."""
    entry1 = {
        "timestamp": "2025-12-16T14:35:21.841078+00:00",
        "hook": "worktree-warning",
        "branch": "main",
    }
    entry2 = {
        "timestamp": "2025-12-16T14:35:22.841078+00:00",  # Different timestamp
        "hook": "worktree-warning",
        "branch": "main",
    }
    assert be.get_block_id(entry1) != be.get_block_id(entry2)


def test_load_blocks_empty_file():
    """Should return empty list for non-existent file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(be, "HOOK_LOG", Path(tmpdir) / "nonexistent.log"):
            blocks = be.load_blocks()
            assert blocks == []


def test_load_blocks_filters_approves():
    """Should only return block decisions, not approves."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "hook-execution.log"
        log_file.write_text(
            json.dumps({"timestamp": "2025-01-01T00:00:00", "hook": "test", "decision": "approve"})
            + "\n"
            + json.dumps(
                {
                    "timestamp": "2025-01-01T00:00:01",
                    "hook": "test",
                    "decision": "block",
                    "reason": "test",
                }
            )
            + "\n"
        )
        with patch.object(be, "HOOK_LOG", log_file):
            blocks = be.load_blocks()
            assert len(blocks) == 1
            assert blocks[0]["decision"] == "block"


def test_load_evaluations_empty_file():
    """Should return empty dict for non-existent file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(be, "EVALUATION_LOG", Path(tmpdir) / "nonexistent.log"):
            evals = be.load_evaluations()
            assert evals == {}


def test_save_and_load_evaluation():
    """Should save and load evaluations correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_dir = Path(tmpdir)
        eval_log = log_dir / "block-evaluations.log"
        with patch.object(be, "LOG_DIR", log_dir), patch.object(be, "EVALUATION_LOG", eval_log):
            evaluation = {
                "block_id": "test123",
                "hook": "test-hook",
                "evaluation": "valid",
                "timestamp": "2025-01-01T00:00:00",
            }
            be.save_evaluation(evaluation)

            loaded = be.load_evaluations()
            assert "test123" in loaded
            assert loaded["test123"]["evaluation"] == "valid"


def test_format_block_unevaluated():
    """Should format unevaluated block correctly."""
    block = {
        "block_id": "abc123",
        "timestamp": "2025-12-16T14:35:21.841078+00:00",
        "hook": "worktree-warning",
        "branch": "main",
        "reason": "Test reason",
    }
    formatted = be.format_block(block, {})
    assert "[abc123]" in formatted
    assert "worktree-warning" in formatted
    assert "main" in formatted
    assert "Test reason" in formatted
    # Should not have evaluation status
    assert "valid" not in formatted
    assert "false_positive" not in formatted


def test_format_block_evaluated():
    """Should show evaluation status for evaluated blocks."""
    block = {
        "block_id": "abc123",
        "timestamp": "2025-12-16T14:35:21.841078+00:00",
        "hook": "worktree-warning",
        "branch": "main",
        "reason": "Test reason",
    }
    evaluations = {"abc123": {"evaluation": "valid"}}
    formatted = be.format_block(block, evaluations)
    assert "✅ valid" in formatted


def test_format_block_false_positive():
    """Should show false positive status."""
    block = {
        "block_id": "abc123",
        "timestamp": "2025-12-16T14:35:21.841078+00:00",
        "hook": "ci-wait-check",
        "branch": "feat/test",
        "reason": "Test block",
    }
    evaluations = {"abc123": {"evaluation": "false_positive"}}
    formatted = be.format_block(block, evaluations)
    assert "❌ false_positive" in formatted


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
