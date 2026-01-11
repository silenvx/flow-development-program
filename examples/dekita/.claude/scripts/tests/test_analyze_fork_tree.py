#!/usr/bin/env python3
"""Tests for analyze-fork-tree.py script (Issue #2195)."""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for imports
scripts_dir = Path(__file__).parent.parent
sys.path.insert(0, str(scripts_dir))

# Load module from file path (handles hyphenated filenames)
script_path = scripts_dir / "analyze_fork_tree.py"
spec = importlib.util.spec_from_file_location("analyze_fork_tree", script_path)
if spec is None or spec.loader is None:
    raise ImportError(
        f"Cannot load module 'analyze_fork_tree' from {script_path}: spec or loader is None"
    )
analyze_fork_tree = importlib.util.module_from_spec(spec)
sys.modules["analyze_fork_tree"] = analyze_fork_tree
spec.loader.exec_module(analyze_fork_tree)


class TestGetFirstParentUuid:
    """Tests for get_first_parent_uuid function."""

    def test_returns_none_for_nonexistent_file(self):
        """Returns None when transcript file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "nonexistent.jsonl"

            result = analyze_fork_tree.get_first_parent_uuid(transcript)

            assert result is None

    def test_returns_parent_uuid_from_first_message(self):
        """Returns parentUuid from first user/assistant message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            entries = [
                {"type": "summary", "summary": "Test"},
                {"type": "user", "parentUuid": "parent-uuid-123", "uuid": "uuid-1"},
                {"type": "assistant", "parentUuid": "uuid-1", "uuid": "uuid-2"},
            ]
            transcript.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

            result = analyze_fork_tree.get_first_parent_uuid(transcript)

            assert result == "parent-uuid-123"

    def test_returns_none_for_null_parent_uuid(self):
        """Returns None when first message has null parentUuid (root session)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            entries = [
                {"type": "user", "parentUuid": None, "uuid": "uuid-1"},
            ]
            transcript.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

            result = analyze_fork_tree.get_first_parent_uuid(transcript)

            assert result is None

    def test_skips_non_message_entries(self):
        """Skips entries that are not user or assistant type."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            entries = [
                {"type": "summary", "summary": "Test"},
                {"type": "snapshot", "data": {}},
                {"type": "user", "parentUuid": "parent-uuid", "uuid": "uuid-1"},
            ]
            transcript.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

            result = analyze_fork_tree.get_first_parent_uuid(transcript)

            assert result == "parent-uuid"


class TestFindParentSession:
    """Tests for find_parent_session function."""

    def test_returns_none_for_root_session(self):
        """Returns None for root session (no parent)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # Create root session (parentUuid is None)
            root_transcript = project_dir / "root-session.jsonl"
            root_transcript.write_text(
                json.dumps({"type": "user", "parentUuid": None, "uuid": "uuid-root"})
            )

            uuid_to_session: dict[str, str] = {}
            result = analyze_fork_tree.find_parent_session(
                "root-session", project_dir, uuid_to_session
            )

            assert result is None

    def test_finds_parent_session(self):
        """Finds parent session from parentUuid."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # Create parent session
            parent_transcript = project_dir / "parent-session.jsonl"
            parent_transcript.write_text(
                json.dumps({"type": "user", "parentUuid": None, "uuid": "parent-msg-uuid"})
            )

            # Create child session that references parent
            child_transcript = project_dir / "child-session.jsonl"
            child_transcript.write_text(
                json.dumps(
                    {
                        "type": "user",
                        "parentUuid": "parent-msg-uuid",
                        "uuid": "child-uuid",
                    }
                )
            )

            uuid_to_session: dict[str, str] = {}
            result = analyze_fork_tree.find_parent_session(
                "child-session", project_dir, uuid_to_session
            )

            assert result == "parent-session"

    def test_uses_cache(self):
        """Uses uuid_to_session cache for faster lookup."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # Create child session
            child_transcript = project_dir / "child-session.jsonl"
            child_transcript.write_text(
                json.dumps(
                    {
                        "type": "user",
                        "parentUuid": "cached-parent-uuid",
                        "uuid": "child-uuid",
                    }
                )
            )

            # Pre-populate cache
            uuid_to_session = {"cached-parent-uuid": "cached-parent-session"}

            result = analyze_fork_tree.find_parent_session(
                "child-session", project_dir, uuid_to_session
            )

            assert result == "cached-parent-session"


class TestBuildForkTree:
    """Tests for build_fork_tree function."""

    def test_returns_empty_for_no_forks(self):
        """Returns empty dict when no fork relationships exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # Create root sessions only
            for i in range(3):
                transcript = project_dir / f"session-{i}.jsonl"
                transcript.write_text(
                    json.dumps({"type": "user", "parentUuid": None, "uuid": f"uuid-{i}"})
                )

            result = analyze_fork_tree.build_fork_tree(project_dir)

            assert result == {}

    def test_builds_fork_relationships(self):
        """Builds parent -> children mapping correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # Parent session
            parent = project_dir / "parent.jsonl"
            parent.write_text(
                json.dumps({"type": "user", "parentUuid": None, "uuid": "parent-uuid"})
            )

            # Child 1
            child1 = project_dir / "child1.jsonl"
            child1.write_text(
                json.dumps({"type": "user", "parentUuid": "parent-uuid", "uuid": "child1-uuid"})
            )

            # Child 2
            child2 = project_dir / "child2.jsonl"
            child2.write_text(
                json.dumps({"type": "user", "parentUuid": "parent-uuid", "uuid": "child2-uuid"})
            )

            result = analyze_fork_tree.build_fork_tree(project_dir)

            assert "parent" in result
            assert set(result["parent"]) == {"child1", "child2"}


class TestGetRootSessions:
    """Tests for get_root_sessions function."""

    def test_identifies_root_sessions(self):
        """Identifies sessions that are not children of any other session."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # Create sessions
            for name in ["root1", "root2", "child1"]:
                transcript = project_dir / f"{name}.jsonl"
                transcript.write_text(
                    json.dumps({"type": "user", "parentUuid": None, "uuid": f"{name}"})
                )

            parent_to_children = {"root1": ["child1"]}

            result = analyze_fork_tree.get_root_sessions(project_dir, parent_to_children)

            # root1 and root2 are not children; child1 is a child
            assert "root1" in result
            assert "root2" in result
            assert "child1" not in result


class TestFormatTree:
    """Tests for format_tree function."""

    def test_returns_message_for_no_forks(self):
        """Returns appropriate message when no fork relationships exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # Create a root session without children
            transcript = project_dir / "only-root.jsonl"
            transcript.write_text(json.dumps({"type": "user", "parentUuid": None, "uuid": "uuid"}))

            parent_to_children: dict[str, list[str]] = {}

            result = analyze_fork_tree.format_tree(project_dir, parent_to_children)

            assert result == "No fork relationships found."

    def test_marks_current_session(self):
        """Marks the current session in the output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)

            # Parent session
            parent = project_dir / "parent-session.jsonl"
            parent.write_text(
                json.dumps(
                    {
                        "type": "user",
                        "parentUuid": None,
                        "uuid": "p-uuid",
                        "timestamp": "2025-01-01T12:00:00Z",
                    }
                )
            )

            # Child session
            child = project_dir / "child-session.jsonl"
            child.write_text(
                json.dumps(
                    {
                        "type": "user",
                        "parentUuid": "p-uuid",
                        "uuid": "c-uuid",
                        "timestamp": "2025-01-01T13:00:00Z",
                    }
                )
            )

            parent_to_children = {"parent-session": ["child-session"]}

            result = analyze_fork_tree.format_tree(
                project_dir, parent_to_children, current_session_id="child-session"
            )

            assert "<- current" in result


class TestMain:
    """Tests for main function."""

    def test_exits_with_error_when_no_project_id(self):
        """Exits with error when project ID cannot be determined."""
        with patch.dict("os.environ", {}, clear=True):
            with patch.object(analyze_fork_tree, "get_project_id", return_value=None):
                with patch("sys.argv", ["analyze_fork_tree.py"]):
                    result = analyze_fork_tree.main()

                    assert result == 1

    def test_json_output(self):
        """Outputs JSON format when --json flag is provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "test-project"
            project_dir.mkdir()

            # Create sessions
            root = project_dir / "root.jsonl"
            root.write_text(json.dumps({"type": "user", "parentUuid": None, "uuid": "root-uuid"}))

            with patch.object(analyze_fork_tree, "CLAUDE_PROJECTS_DIR", Path(tmpdir)):
                with patch.object(analyze_fork_tree, "get_project_id", return_value="test-project"):
                    with patch("sys.argv", ["analyze_fork_tree.py", "--json"]):
                        result = analyze_fork_tree.main()

                        assert result == 0
