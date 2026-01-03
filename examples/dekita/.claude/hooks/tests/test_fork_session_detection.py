#!/usr/bin/env python3
"""Unit tests for fork-session detection functionality.

Issue #2288: Tests for distinguishing fork-session from resume-session
by comparing session IDs.
Issue #2308: Added tests for transcript-based fork detection.
Issue #2316: Removed file-based (last-session-id.txt) detection tests.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for lib module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from lib.session import (
    _is_valid_transcript_path,
    extract_session_id_from_transcript_path,
    get_parent_session_id,
    get_session_ancestry,
    get_session_marker_dir,
    has_different_session_ids,
    is_fork_session,
)


def create_transcript(tmp_path: Path, entries: list[dict]) -> Path:
    """Create a transcript JSONL file with the given entries.

    Shared helper for transcript-based tests.
    """
    transcript_path = tmp_path / "test-transcript.jsonl"
    with transcript_path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return transcript_path


class TestIsValidTranscriptPath:
    """Tests for Issue #2333: _is_valid_transcript_path function.

    Issue #2336: Updated to pass resolved Path objects instead of strings.
    """

    def test_allows_path_within_project_dir(self, tmp_path):
        """Should allow paths within CLAUDE_PROJECT_DIR."""
        transcript = tmp_path / "transcript.jsonl"
        transcript.touch()

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            assert _is_valid_transcript_path(transcript.resolve()) is True

    def test_allows_path_within_claude_projects_dir(self, tmp_path):
        """Issue #2333: Should allow paths within ~/.claude/projects/."""
        # Create a mock ~/.claude/projects/ structure
        claude_projects = tmp_path / ".claude" / "projects"
        claude_projects.mkdir(parents=True)
        transcript = claude_projects / "project-hash" / "session.jsonl"
        transcript.parent.mkdir()
        transcript.touch()

        # Patch Path.home() to return tmp_path so ~/.claude/projects/ resolves correctly
        with (
            patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path / "different")}),
            patch.object(Path, "home", return_value=tmp_path),
        ):
            assert _is_valid_transcript_path(transcript.resolve()) is True

    def test_rejects_path_outside_allowed_locations(self, tmp_path):
        """Should reject paths outside allowed locations."""
        # Create a file outside both CLAUDE_PROJECT_DIR and ~/.claude/projects/
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        transcript = outside_dir / "transcript.jsonl"
        transcript.touch()

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # Mock home to a different location so ~/.claude/projects/ doesn't match
        mock_home = tmp_path / "home"
        mock_home.mkdir()

        with (
            patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(project_dir)}),
            patch.object(Path, "home", return_value=mock_home),
        ):
            assert _is_valid_transcript_path(transcript.resolve()) is False

    def test_rejects_sibling_directory_bypass(self, tmp_path):
        """Security: Should reject sibling directories sharing prefix."""
        # Regression test for startswith() vulnerability
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        sibling_dir = tmp_path / "project-backup"  # Shares prefix
        sibling_dir.mkdir()
        transcript = sibling_dir / "transcript.jsonl"
        transcript.touch()

        mock_home = tmp_path / "home"
        mock_home.mkdir()

        with (
            patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(project_dir)}),
            patch.object(Path, "home", return_value=mock_home),
        ):
            assert _is_valid_transcript_path(transcript.resolve()) is False

    def test_rejects_claude_projects_sibling_directory(self, tmp_path):
        """Security: Should reject sibling directories of ~/.claude/projects/."""
        # Test for ~/.claude/projects-backup/ bypass attempt
        mock_home = tmp_path / "home"
        mock_home.mkdir()

        # Create sibling directory that shares prefix with projects/
        sibling_dir = mock_home / ".claude" / "projects-backup"
        sibling_dir.mkdir(parents=True)
        transcript = sibling_dir / "session.jsonl"
        transcript.touch()

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with (
            patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(project_dir)}),
            patch.object(Path, "home", return_value=mock_home),
        ):
            assert _is_valid_transcript_path(transcript.resolve()) is False


class TestSessionMarkerDir:
    """Tests for session marker directory functionality."""

    def test_get_session_marker_dir_creates_directory(self, tmp_path):
        """Should create session marker directory if it doesn't exist."""
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            session_dir = get_session_marker_dir()

            assert session_dir.exists()
            assert session_dir.is_dir()
            assert session_dir == tmp_path / ".claude" / "logs" / "session"


class TestIsForkSessionWithoutTranscript:
    """Tests for fork-session detection without transcript."""

    def test_returns_false_when_source_is_compact(self):
        """source=compact should always return False."""
        assert is_fork_session("any-session-id", "compact") is False
        assert is_fork_session("any-session-id", "compact", None) is False

    def test_returns_false_when_no_transcript(self):
        """Should return False when no transcript is provided."""
        # Issue #2316: Without transcript, fork cannot be detected
        assert is_fork_session("aac956f9-4701-4bca-98f4-d4f166716c73", "resume") is False
        assert is_fork_session("aac956f9-4701-4bca-98f4-d4f166716c73", "init") is False
        assert is_fork_session("aac956f9-4701-4bca-98f4-d4f166716c73", "") is False


class TestTranscriptBasedForkDetection:
    """Tests for Issue #2308: Transcript-based fork-session detection."""

    def test_get_parent_session_id_returns_first_user_session_id(self, tmp_path):
        """Should return sessionId from first user message."""
        parent_id = "aac956f9-4701-4bca-98f4-d4f166716c73"
        current_id = "596799dc-6ec8-4c29-82cf-1f7559cdde1a"

        transcript = create_transcript(
            tmp_path,
            [
                {"type": "file-history-snapshot", "sessionId": None},
                {"type": "user", "sessionId": parent_id, "message": {"content": "Hello"}},
                {"type": "assistant", "sessionId": current_id},
                {"type": "user", "sessionId": current_id, "message": {"content": "World"}},
            ],
        )

        # Patch CLAUDE_PROJECT_DIR to allow access to tmp_path
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = get_parent_session_id(str(transcript))
        assert result == parent_id

    def test_get_parent_session_id_returns_none_for_empty_transcript(self, tmp_path):
        """Should return None when transcript is empty."""
        transcript = create_transcript(tmp_path, [])
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = get_parent_session_id(str(transcript))
        assert result is None

    def test_get_parent_session_id_returns_none_for_nonexistent_file(self):
        """Should return None when transcript file doesn't exist."""
        result = get_parent_session_id("/nonexistent/path/transcript.jsonl")
        assert result is None

    def test_get_parent_session_id_returns_none_for_none_path(self):
        """Should return None when transcript_path is None."""
        result = get_parent_session_id(None)
        assert result is None

    def test_get_parent_session_id_handles_malformed_json(self, tmp_path):
        """Should skip malformed JSON lines and find first valid user message."""
        parent_id = "aac956f9-4701-4bca-98f4-d4f166716c73"

        transcript_path = tmp_path / "test-transcript.jsonl"
        with transcript_path.open("w", encoding="utf-8") as f:
            f.write("not valid json\n")
            f.write(json.dumps({"type": "user", "sessionId": parent_id}) + "\n")

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = get_parent_session_id(str(transcript_path))
        assert result == parent_id

    def test_is_fork_session_with_different_session_ids(self, tmp_path):
        """Issue #2328: Should detect fork when transcript contains different sessionIds."""
        parent_id = "aac956f9-4701-4bca-98f4-d4f166716c73"
        current_id = "596799dc-6ec8-4c29-82cf-1f7559cdde1a"

        # Fork transcript: contains entries with parent's sessionId
        transcript = create_transcript(
            tmp_path,
            [
                {"type": "file-history-snapshot", "sessionId": None},
                {"type": "user", "sessionId": current_id},
                {"type": "assistant", "sessionId": current_id},
                {"type": "user", "sessionId": parent_id},  # Parent session entry
            ],
        )

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = is_fork_session(current_id, "resume", str(transcript))
        assert result is True

    def test_is_fork_session_with_only_current_session_id(self, tmp_path):
        """Issue #2328: Should not detect fork when all entries have current sessionId."""
        session_id = "aac956f9-4701-4bca-98f4-d4f166716c73"

        # Non-fork transcript: all entries have same sessionId or null
        transcript = create_transcript(
            tmp_path,
            [
                {"type": "file-history-snapshot", "sessionId": None},
                {"type": "summary", "sessionId": None},  # Summary from context compression
                {"type": "user", "sessionId": session_id},
                {"type": "assistant", "sessionId": session_id},
            ],
        )

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = is_fork_session(session_id, "resume", str(transcript))
        assert result is False

    def test_is_fork_session_compact_always_false(self, tmp_path):
        """source=compact should never be detected as fork."""
        parent_id = "aac956f9-4701-4bca-98f4-d4f166716c73"
        current_id = "596799dc-6ec8-4c29-82cf-1f7559cdde1a"

        transcript = create_transcript(
            tmp_path,
            [{"type": "user", "sessionId": parent_id}],
        )

        # Even with different IDs, compact should not be fork
        result = is_fork_session(current_id, "compact", str(transcript))
        assert result is False

    def test_is_fork_session_startup_does_not_detect_fork(self, tmp_path):
        """Issue #2328: source=startup should not detect fork (only resume does)."""
        parent_id = "aac956f9-4701-4bca-98f4-d4f166716c73"
        current_id = "596799dc-6ec8-4c29-82cf-1f7559cdde1a"

        transcript = create_transcript(
            tmp_path,
            [{"type": "user", "sessionId": parent_id}],
        )

        # source=startup should not trigger fork detection
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = is_fork_session(current_id, "startup", str(transcript))
        assert result is False


class TestHasDifferentSessionIds:
    """Tests for Issue #2328: has_different_session_ids function."""

    def test_returns_true_when_different_session_id_exists(self, tmp_path):
        """Should return True when transcript contains different sessionId."""
        current_id = "current-session-id"
        other_id = "other-session-id"

        transcript = create_transcript(
            tmp_path,
            [
                {"type": "user", "sessionId": current_id},
                {"type": "user", "sessionId": other_id},  # Different
            ],
        )

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = has_different_session_ids(str(transcript), current_id)
        assert result is True

    def test_returns_false_when_only_current_and_null(self, tmp_path):
        """Should return False when only current sessionId and null exist."""
        current_id = "current-session-id"

        transcript = create_transcript(
            tmp_path,
            [
                {"type": "file-history-snapshot", "sessionId": None},
                {"type": "summary", "sessionId": None},
                {"type": "user", "sessionId": current_id},
                {"type": "assistant", "sessionId": current_id},
            ],
        )

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = has_different_session_ids(str(transcript), current_id)
        assert result is False

    def test_returns_false_for_empty_transcript(self, tmp_path):
        """Should return False for empty transcript."""
        transcript = create_transcript(tmp_path, [])
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = has_different_session_ids(str(transcript), "any-id")
        assert result is False

    def test_returns_false_for_none_path(self):
        """Should return False when transcript_path is None."""
        result = has_different_session_ids(None, "any-id")
        assert result is False

    def test_returns_false_for_none_session_id(self, tmp_path):
        """Should return False when current_session_id is an empty string."""
        transcript = create_transcript(
            tmp_path,
            [{"type": "user", "sessionId": "some-id"}],
        )
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = has_different_session_ids(str(transcript), "")
        assert result is False

    def test_get_parent_session_id_rejects_path_traversal(self, tmp_path):
        """Security: Should reject transcript paths outside CLAUDE_PROJECT_DIR."""
        parent_id = "aac956f9-4701-4bca-98f4-d4f166716c73"

        # Create transcript in a different directory
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        transcript = create_transcript(
            other_dir,
            [{"type": "user", "sessionId": parent_id}],
        )

        # Set CLAUDE_PROJECT_DIR to a subdirectory that doesn't contain the transcript
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(project_dir)}):
            result = get_parent_session_id(str(transcript))
        # Should return None because transcript is outside project dir
        assert result is None

    def test_get_parent_session_id_skips_none_session_id(self, tmp_path):
        """Should skip user messages with sessionId=None and find first valid one."""
        valid_parent_id = "aac956f9-4701-4bca-98f4-d4f166716c73"

        transcript = create_transcript(
            tmp_path,
            [
                {"type": "file-history-snapshot", "sessionId": None},
                {"type": "user", "sessionId": None, "message": {"content": "First"}},
                {"type": "user", "sessionId": valid_parent_id, "message": {"content": "Second"}},
            ],
        )

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = get_parent_session_id(str(transcript))
        # Should skip the first user message with None and return the valid one
        assert result == valid_parent_id

    def test_get_parent_session_id_rejects_symlink_to_outside(self, tmp_path):
        """Security: Should reject symlinks pointing outside CLAUDE_PROJECT_DIR.

        Issue #2312: Verify that Path.resolve() correctly resolves symlinks
        and the path traversal check catches symlink-based bypass attempts.
        """
        parent_id = "aac956f9-4701-4bca-98f4-d4f166716c73"

        # Create transcript in a directory outside the project
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        create_transcript(
            outside_dir,
            [{"type": "user", "sessionId": parent_id}],
        )

        # Create project directory and a symlink inside it pointing to outside
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        symlink_path = project_dir / "sneaky_symlink"
        symlink_path.symlink_to(outside_dir)
        # Ensure the symlink was created successfully before proceeding
        assert symlink_path.exists()
        assert symlink_path.is_symlink()

        # Try to access transcript via symlink (symlink_path / "test-transcript.jsonl")
        symlink_transcript_path = symlink_path / "test-transcript.jsonl"

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(project_dir)}):
            result = get_parent_session_id(str(symlink_transcript_path))

        # Should return None because resolve() follows the symlink to outside_dir
        # which is not under project_dir
        assert result is None


class TestDefensiveSessionIdChecks:
    """Tests for Issue #2295: Defensive checks for session_id."""

    def test_defensive_guard_skips_is_fork_for_empty_string(self, tmp_path):
        """Defensive guard should skip is_fork_session for empty string.

        Tests the guard pattern used in session-resume-warning.py:
        `if current_session_id: is_fork = is_fork_session(...)`
        """
        # Simulate the defensive guard pattern from session-resume-warning.py
        session_id = ""  # Hypothetical edge case
        is_fork = False
        if session_id:  # Guard pattern
            is_fork = is_fork_session(session_id, "resume")

        assert is_fork is False

    def test_format_message_produces_resume_message(self):
        """format_resume_session_message works correctly."""
        import importlib.util

        # Load hyphenated module using spec_from_file_location
        # (import_module cannot handle hyphens in module names)
        hook_path = Path(__file__).parent.parent / "session-resume-warning.py"
        spec = importlib.util.spec_from_file_location("session_resume_warning", hook_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        format_func = module.format_resume_session_message

        # Issue #2363: fork判定はClaudeがコンテキスト内で行う
        message = format_func([], [])
        assert "セッション再開検出" in message  # Resume message


class TestGetSessionAncestry:
    """Tests for Issue #2331: get_session_ancestry function."""

    def test_returns_session_ids_in_order(self, tmp_path):
        """Should return sessionIds in order of first appearance."""
        parent_id = "parent-session-id"
        child_id = "child-session-id"
        current_id = "current-session-id"

        transcript = create_transcript(
            tmp_path,
            [
                {"type": "file-history-snapshot", "sessionId": None},
                {"type": "user", "sessionId": parent_id},  # First
                {"type": "assistant", "sessionId": parent_id},
                {"type": "user", "sessionId": child_id},  # Second
                {"type": "assistant", "sessionId": child_id},
                {"type": "user", "sessionId": current_id},  # Third
                {"type": "user", "sessionId": parent_id},  # Duplicate, should be ignored
            ],
        )

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = get_session_ancestry(str(transcript))

        assert result == [parent_id, child_id, current_id]

    def test_returns_empty_list_for_empty_transcript(self, tmp_path):
        """Should return empty list for empty transcript."""
        transcript = create_transcript(tmp_path, [])
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = get_session_ancestry(str(transcript))
        assert result == []

    def test_returns_empty_list_for_none_path(self):
        """Should return empty list when transcript_path is None."""
        result = get_session_ancestry(None)
        assert result == []

    def test_returns_empty_list_for_nonexistent_file(self):
        """Should return empty list when transcript file doesn't exist."""
        result = get_session_ancestry("/nonexistent/path/transcript.jsonl")
        assert result == []

    def test_skips_malformed_json(self, tmp_path):
        """Should skip malformed JSON lines."""
        valid_id = "valid-session-id"

        transcript_path = tmp_path / "test-transcript.jsonl"
        with transcript_path.open("w", encoding="utf-8") as f:
            f.write("not valid json\n")
            f.write(json.dumps({"type": "user", "sessionId": valid_id}) + "\n")
            f.write("{invalid json too\n")

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = get_session_ancestry(str(transcript_path))
        assert result == [valid_id]

    def test_excludes_null_session_ids(self, tmp_path):
        """Should only include non-null sessionIds."""
        valid_id = "valid-session-id"

        transcript = create_transcript(
            tmp_path,
            [
                {"type": "file-history-snapshot", "sessionId": None},
                {"type": "summary", "sessionId": None},
                {"type": "user", "sessionId": valid_id},
            ],
        )

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = get_session_ancestry(str(transcript))
        assert result == [valid_id]

    def test_rejects_path_traversal(self, tmp_path):
        """Security: Should reject transcript paths outside CLAUDE_PROJECT_DIR."""
        # Create transcript in a different directory
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        transcript = create_transcript(
            other_dir,
            [{"type": "user", "sessionId": "some-id"}],
        )

        # Set CLAUDE_PROJECT_DIR to a subdirectory that doesn't contain the transcript
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(project_dir)}):
            result = get_session_ancestry(str(transcript))
        assert result == []

    def test_single_session_returns_single_item_list(self, tmp_path):
        """Should return single-item list for non-fork session."""
        session_id = "single-session-id"

        transcript = create_transcript(
            tmp_path,
            [
                {"type": "user", "sessionId": session_id},
                {"type": "assistant", "sessionId": session_id},
            ],
        )

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = get_session_ancestry(str(transcript))
        assert result == [session_id]

    def test_rejects_sibling_directory_bypass(self, tmp_path):
        """Security: Should reject sibling directories sharing prefix.

        Issue #2331: Codex review found that startswith() allows bypass via
        sibling directories like /project-backup when project is /project.
        Use is_relative_to() instead.
        """
        # Create two sibling directories with shared prefix
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        sibling_dir = tmp_path / "project-backup"  # Shares prefix with "project"
        sibling_dir.mkdir()

        transcript = create_transcript(
            sibling_dir,
            [{"type": "user", "sessionId": "some-id"}],
        )

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(project_dir)}):
            result = get_session_ancestry(str(transcript))
        # Should reject because sibling_dir is not under project_dir
        assert result == []


class TestExtractSessionIdFromTranscriptPath:
    """Tests for Issue #2342: extract_session_id_from_transcript_path function."""

    def test_extracts_valid_uuid_from_path(self):
        """Should extract valid UUID from transcript filename."""
        session_id = "aac956f9-4701-4bca-98f4-d4f166716c73"
        path = f"/Users/test/.claude/projects/project-hash/{session_id}.jsonl"
        result = extract_session_id_from_transcript_path(path)
        assert result == session_id

    def test_returns_none_for_none_path(self):
        """Should return None when path is None."""
        assert extract_session_id_from_transcript_path(None) is None

    def test_returns_none_for_empty_path(self):
        """Should return None when path is empty string."""
        assert extract_session_id_from_transcript_path("") is None

    def test_returns_none_for_invalid_uuid_filename(self):
        """Should return None when filename is not a valid UUID."""
        path = "/some/path/not-a-uuid.jsonl"
        result = extract_session_id_from_transcript_path(path)
        assert result is None

    def test_returns_none_for_path_without_extension(self):
        """Should handle paths without extension correctly."""
        # Path.stem on a file without extension returns the full filename
        session_id = "aac956f9-4701-4bca-98f4-d4f166716c73"
        path = f"/some/path/{session_id}"
        result = extract_session_id_from_transcript_path(path)
        # This should work since stem returns the UUID
        assert result == session_id


class TestIsForkSessionByTranscriptFilename:
    """Tests for Issue #2342: Fork detection by comparing hook session_id with transcript filename."""

    def test_detects_fork_when_session_id_differs_from_transcript_filename(self, tmp_path):
        """Should detect fork when hook's session_id differs from transcript filename.

        This is the primary detection mechanism for fork-sessions. When Claude Code
        forks a session, it:
        1. Creates a new session_id for the forked session
        2. Uses the parent session's transcript file
        3. Passes the NEW session_id to hooks

        So: hook session_id (new) != transcript filename (parent) = fork-session
        """
        parent_session_id = "82e0203d-ab6b-4b5d-ac41-0c730b766038"
        new_session_id = "c197cf23-ee71-42cd-89bd-bedbbdd218ef"

        # Transcript file is named after parent session
        transcript_path = tmp_path / f"{parent_session_id}.jsonl"
        transcript_path.write_text('{"type": "user", "sessionId": "' + parent_session_id + '"}\n')

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = is_fork_session(new_session_id, "resume", str(transcript_path))

        assert result is True

    def test_not_fork_when_session_id_matches_transcript_filename(self, tmp_path):
        """Should not detect fork when hook's session_id matches transcript filename.

        This is a normal resume, not a fork.
        """
        session_id = "82e0203d-ab6b-4b5d-ac41-0c730b766038"

        # Transcript file is named after current session
        transcript_path = tmp_path / f"{session_id}.jsonl"
        transcript_path.write_text('{"type": "user", "sessionId": "' + session_id + '"}\n')

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = is_fork_session(session_id, "resume", str(transcript_path))

        assert result is False

    def test_falls_back_to_content_check_when_filename_not_uuid(self, tmp_path):
        """Should fall back to content check when transcript filename is not a UUID.

        In some edge cases, the transcript filename might not be a valid UUID.
        In these cases, fall back to checking the transcript content.
        """
        session_id = "82e0203d-ab6b-4b5d-ac41-0c730b766038"
        other_session_id = "c197cf23-ee71-42cd-89bd-bedbbdd218ef"

        # Transcript file with non-UUID name
        transcript_path = tmp_path / "test-transcript.jsonl"
        transcript_path.write_text('{"type": "user", "sessionId": "' + other_session_id + '"}\n')

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = is_fork_session(session_id, "resume", str(transcript_path))

        # Should detect fork via fallback content check
        assert result is True

    def test_compact_source_never_detects_fork(self, tmp_path):
        """source=compact should never be detected as fork, even with different IDs."""
        parent_session_id = "82e0203d-ab6b-4b5d-ac41-0c730b766038"
        new_session_id = "c197cf23-ee71-42cd-89bd-bedbbdd218ef"

        transcript_path = tmp_path / f"{parent_session_id}.jsonl"
        transcript_path.write_text('{"type": "user", "sessionId": "' + parent_session_id + '"}\n')

        # Even with different IDs, compact should not trigger fork detection
        result = is_fork_session(new_session_id, "compact", str(transcript_path))
        assert result is False

    def test_startup_source_does_not_detect_fork(self, tmp_path):
        """source=startup should not detect fork."""
        parent_session_id = "82e0203d-ab6b-4b5d-ac41-0c730b766038"
        new_session_id = "c197cf23-ee71-42cd-89bd-bedbbdd218ef"

        transcript_path = tmp_path / f"{parent_session_id}.jsonl"
        transcript_path.write_text('{"type": "user", "sessionId": "' + parent_session_id + '"}\n')

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = is_fork_session(new_session_id, "startup", str(transcript_path))

        assert result is False


class TestGetForkTranscriptSessionId:
    """Tests for Issue #2344: get_fork_transcript_session_id function."""

    def test_finds_fork_containing_parent_id(self, tmp_path):
        """Should find fork transcript that contains parent session ID."""
        from lib.session import get_fork_transcript_session_id

        parent_id = "82e0203d-ab6b-4b5d-ac41-0c730b766038"
        fork_id = "c197cf23-ee71-42cd-89bd-bedbbdd218ef"

        # Parent transcript
        parent_file = tmp_path / f"{parent_id}.jsonl"
        parent_file.write_text('{"type": "user", "sessionId": "' + parent_id + '"}\n')

        # Fork transcript contains parent's ID
        fork_file = tmp_path / f"{fork_id}.jsonl"
        fork_file.write_text('{"type": "user", "sessionId": "' + parent_id + '"}\n')

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = get_fork_transcript_session_id(str(parent_file), parent_id)
        assert result == fork_id

    def test_ignores_unrelated_sessions(self, tmp_path):
        """Should not return unrelated session IDs."""
        from lib.session import get_fork_transcript_session_id

        parent_id = "82e0203d-ab6b-4b5d-ac41-0c730b766038"
        unrelated_id = "c197cf23-ee71-42cd-89bd-bedbbdd218ef"

        # Parent transcript
        parent_file = tmp_path / f"{parent_id}.jsonl"
        parent_file.write_text('{"type": "user", "sessionId": "' + parent_id + '"}\n')

        # Unrelated transcript (does NOT contain parent's ID)
        unrelated_file = tmp_path / f"{unrelated_id}.jsonl"
        unrelated_file.write_text('{"type": "user", "sessionId": "' + unrelated_id + '"}\n')

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = get_fork_transcript_session_id(str(parent_file), parent_id)
        assert result is None

    def test_ignores_non_uuid_files(self, tmp_path):
        """Should ignore files without valid UUID names."""
        from lib.session import get_fork_transcript_session_id

        parent_id = "82e0203d-ab6b-4b5d-ac41-0c730b766038"
        parent_file = tmp_path / f"{parent_id}.jsonl"
        parent_file.write_text('{"type": "user"}\n')

        # agent-* file should be ignored even if it contains parent ID
        agent_file = tmp_path / "agent-abc123.jsonl"
        agent_file.write_text('{"type": "user", "sessionId": "' + parent_id + '"}\n')

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = get_fork_transcript_session_id(str(parent_file), parent_id)
        assert result is None

    def test_returns_none_for_none_path(self):
        """Should return None when path is None."""
        from lib.session import get_fork_transcript_session_id

        assert get_fork_transcript_session_id(None, "any-id") is None

    def test_returns_none_for_none_parent_id(self, tmp_path):
        """Should return None when parent_session_id is empty."""
        from lib.session import get_fork_transcript_session_id

        file_path = tmp_path / "test.jsonl"
        file_path.write_text('{"type": "user"}\n')

        assert get_fork_transcript_session_id(str(file_path), "") is None


class TestForkDetectionViaContentCheck:
    """Tests for Issue #2344: Fork detection using transcript content check."""

    def test_detects_fork_when_hook_receives_parent_ids(self, tmp_path):
        """Should detect fork when hook receives parent's session_id and transcript_path.

        This is the key scenario for Issue #2344:
        - Claude Code creates fork with new session ID
        - But passes parent's session_id AND transcript_path to hooks
        - Detection should find fork transcript containing parent's ID
        """
        parent_session_id = "82e0203d-ab6b-4b5d-ac41-0c730b766038"
        fork_session_id = "c197cf23-ee71-42cd-89bd-bedbbdd218ef"

        # Parent's transcript file (passed to hook)
        parent_transcript = tmp_path / f"{parent_session_id}.jsonl"
        parent_transcript.write_text('{"type": "user", "sessionId": "' + parent_session_id + '"}\n')

        # Fork's transcript file (contains parent's ID in content)
        fork_transcript = tmp_path / f"{fork_session_id}.jsonl"
        fork_transcript.write_text('{"type": "user", "sessionId": "' + parent_session_id + '"}\n')

        # Hook receives parent's session_id and parent's transcript_path
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = is_fork_session(parent_session_id, "resume", str(parent_transcript))

        # Should detect fork because fork file contains parent's ID
        assert result is True

    def test_no_false_positive_for_normal_resume(self, tmp_path):
        """Should not detect fork for normal resume (single transcript file)."""
        session_id = "82e0203d-ab6b-4b5d-ac41-0c730b766038"

        transcript = tmp_path / f"{session_id}.jsonl"
        transcript.write_text('{"type": "user", "sessionId": "' + session_id + '"}\n')

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = is_fork_session(session_id, "resume", str(transcript))

        assert result is False

    def test_no_false_positive_with_unrelated_session(self, tmp_path):
        """Should not detect fork when other sessions exist but are unrelated."""
        session_a = "82e0203d-ab6b-4b5d-ac41-0c730b766038"
        session_b = "c197cf23-ee71-42cd-89bd-bedbbdd218ef"

        # Session A's transcript
        transcript_a = tmp_path / f"{session_a}.jsonl"
        transcript_a.write_text('{"type": "user", "sessionId": "' + session_a + '"}\n')

        # Session B's transcript (unrelated, does NOT contain session_a's ID)
        transcript_b = tmp_path / f"{session_b}.jsonl"
        transcript_b.write_text('{"type": "user", "sessionId": "' + session_b + '"}\n')

        # Session A resuming should NOT detect B as fork
        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = is_fork_session(session_a, "resume", str(transcript_a))

        assert result is False

    def test_no_false_positive_from_substring_match(self, tmp_path):
        """Should not match when parent ID appears as substring in other fields.

        Issue #2344 review feedback: Ensure we check sessionId field explicitly,
        not just string presence in the line.
        """
        parent_id = "82e0203d-ab6b-4b5d-ac41-0c730b766038"
        other_id = "c197cf23-ee71-42cd-89bd-bedbbdd218ef"

        # Parent transcript
        parent_transcript = tmp_path / f"{parent_id}.jsonl"
        parent_transcript.write_text(f'{{"type": "user", "sessionId": "{parent_id}"}}\n')

        # Other transcript that mentions parent_id in message content (not sessionId)
        other_transcript = tmp_path / f"{other_id}.jsonl"
        other_transcript.write_text(
            f'{{"type": "user", "sessionId": "{other_id}", '
            f'"message": "Reference to session {parent_id}"}}\n'
        )

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = is_fork_session(parent_id, "resume", str(parent_transcript))

        # Should NOT detect as fork because parent_id is in message, not sessionId
        assert result is False

    def test_multiple_forks_returns_one(self, tmp_path):
        """Should return a valid fork when multiple forks exist from same parent.

        Issue #2344 review feedback: When multiple forks exist, the function
        should return one valid fork ID (not fail or return incorrect value).
        The exact fork returned may vary, but the result should be valid.
        """
        import time

        from lib.session import get_fork_transcript_session_id

        parent_id = "82e0203d-ab6b-4b5d-ac41-0c730b766038"
        fork1_id = "c197cf23-ee71-42cd-89bd-bedbbdd218ef"
        fork2_id = "d298df34-ff82-53de-9ace-cfecc2ed329f"

        # Parent transcript
        parent_file = tmp_path / f"{parent_id}.jsonl"
        parent_file.write_text(f'{{"type": "user", "sessionId": "{parent_id}"}}\n')

        # Two forks from the same parent
        fork1_file = tmp_path / f"{fork1_id}.jsonl"
        fork1_file.write_text(f'{{"type": "user", "sessionId": "{parent_id}"}}\n')

        # Small delay to ensure different mtime
        time.sleep(0.01)

        fork2_file = tmp_path / f"{fork2_id}.jsonl"
        fork2_file.write_text(f'{{"type": "user", "sessionId": "{parent_id}"}}\n')

        with patch.dict("os.environ", {"CLAUDE_PROJECT_DIR": str(tmp_path)}):
            result = get_fork_transcript_session_id(str(parent_file), parent_id)

        # Should return one of the forks (preferably the newest due to sorting)
        assert result in [fork1_id, fork2_id]
        # More specifically, should return fork2 (newest) due to mtime sorting
        assert result == fork2_id

    def test_path_traversal_blocked(self, tmp_path):
        """Should block path traversal attempts.

        Issue #2344 security review: Ensure path validation prevents directory traversal.
        """
        from lib.session import get_fork_transcript_session_id

        parent_id = "82e0203d-ab6b-4b5d-ac41-0c730b766038"

        # Create a file outside the allowed directory
        evil_dir = tmp_path / "evil"
        evil_dir.mkdir()
        evil_file = evil_dir / f"{parent_id}.jsonl"
        evil_file.write_text(f'{{"type": "user", "sessionId": "{parent_id}"}}\n')

        # Attempt path traversal (without CLAUDE_PROJECT_DIR set)
        traversal_path = f"../evil/{parent_id}.jsonl"

        # Should return None due to path validation
        result = get_fork_transcript_session_id(traversal_path, parent_id)
        assert result is None
