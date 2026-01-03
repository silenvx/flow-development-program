#!/usr/bin/env python3
"""Unit tests for reflection-completion-check.py

This hook:
- Detects if reflection is required (PR merge or /reflect skill invocation)
- Verifies that proper reflection (五省) was performed
- Blocks session end if reflection requirements not met
- Issue #2172: Detects merge completion from flow state
"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Import the module under test
spec = importlib.util.spec_from_file_location(
    "reflection_completion_check",
    Path(__file__).parent.parent / "reflection-completion-check.py",
)
reflection_completion_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reflection_completion_check)

# Import functions to test
check_transcript_for_reflection = reflection_completion_check.check_transcript_for_reflection
check_skill_invocation = reflection_completion_check.check_skill_invocation
check_merge_phase_completed = reflection_completion_check.check_merge_phase_completed
extract_immediate_tags = reflection_completion_check.extract_immediate_tags
check_immediate_action_executed = reflection_completion_check.check_immediate_action_executed
is_valid_immediate_action = reflection_completion_check.is_valid_immediate_action


class TestCheckTranscriptForReflection:
    """Tests for check_transcript_for_reflection function."""

    def test_detects_gosei(self):
        """Should detect 五省 keyword."""
        assert check_transcript_for_reflection("今回の五省を行います")
        assert check_transcript_for_reflection("五省")

    def test_detects_furikaeri(self):
        """Should detect 振り返り keyword."""
        assert check_transcript_for_reflection("振り返りを実施します")
        assert check_transcript_for_reflection("振り返り")

    def test_detects_hanseiten(self):
        """Should detect 反省点 keyword."""
        assert check_transcript_for_reflection("今回の反省点として")
        assert check_transcript_for_reflection("反省点: テストが不足")

    def test_detects_kaizenten(self):
        """Should detect 改善点 keyword."""
        assert check_transcript_for_reflection("改善点を洗い出す")
        assert check_transcript_for_reflection("改善点: より早く確認")

    def test_detects_kyoukun(self):
        """Should detect 教訓 keyword."""
        assert check_transcript_for_reflection("今回の教訓として")
        assert check_transcript_for_reflection("教訓: 事前確認の重要性")

    def test_detects_gosei_items(self):
        """Should detect individual 五省 items."""
        assert check_transcript_for_reflection("要件理解に悖るなかりしか")
        assert check_transcript_for_reflection("実装に恥づるなかりしか")
        assert check_transcript_for_reflection("検証に欠くるなかりしか")
        assert check_transcript_for_reflection("対応に憾みなかりしか")
        assert check_transcript_for_reflection("効率に欠くるなかりしか")

    def test_no_reflection_keywords(self):
        """Should return False when no reflection keywords present."""
        assert not check_transcript_for_reflection("通常の作業完了しました")
        assert not check_transcript_for_reflection("PRをマージしました")
        assert not check_transcript_for_reflection("")


class TestCheckSkillInvocation:
    """Tests for check_skill_invocation function (Issue #2140)."""

    def test_detects_skill_tool_invocation(self):
        """Should detect Skill: reflect pattern."""
        assert check_skill_invocation("Skill: reflect")
        assert check_skill_invocation("### Skill: reflect\nPath: projectSettings")

    def test_detects_prompt_reference(self):
        """Should detect direct prompt reference."""
        assert check_skill_invocation("@.claude/prompts/reflection/execute.md")
        assert check_skill_invocation('text": "@.claude/prompts/reflection/execute.md\\n')

    def test_detects_slash_command(self):
        """Should detect /reflect command."""
        assert check_skill_invocation("/reflect")
        assert check_skill_invocation("ユーザーが /reflect を実行")
        assert check_skill_invocation("/reflect を実行してください")

    def test_detects_skill_tool_call(self):
        """Should detect Skill tool call syntax."""
        assert check_skill_invocation('Skill(skill: "reflect")')
        assert check_skill_invocation("Skill(reflect)")

    def test_no_skill_invocation(self):
        """Should return False when no skill invocation present."""
        assert not check_skill_invocation("通常の作業完了しました")
        assert not check_skill_invocation("PRをマージしました")
        assert not check_skill_invocation("")

    def test_slash_reflect_word_boundary(self):
        """Should not match /reflection or similar."""
        # /reflect with word boundary should match
        assert check_skill_invocation("/reflect を実行")
        # /reflection should NOT match (different command, word boundary prevents it)
        assert not check_skill_invocation("/reflection")

    def test_slash_reflect_edge_cases(self):
        """Test word boundary edge cases for /reflect pattern."""
        # Should NOT match - different words
        assert not check_skill_invocation("/reflective")
        assert not check_skill_invocation("/reflections")
        # Underscore is a word character, so /reflect_ion does NOT match
        assert not check_skill_invocation("/reflect_ion")
        # Hyphen is NOT a word character, so /reflect-ion DOES match
        assert check_skill_invocation("/reflect-ion")
        # End of string should match
        assert check_skill_invocation("run /reflect")
        assert check_skill_invocation("/reflect")

    def test_case_insensitivity(self):
        """Should match regardless of case."""
        # Skill: pattern variations
        assert check_skill_invocation("SKILL: reflect")
        assert check_skill_invocation("Skill: REFLECT")
        assert check_skill_invocation("skill: Reflect")
        # Slash command - case shouldn't matter
        assert check_skill_invocation("/REFLECT")
        assert check_skill_invocation("/Reflect")


class TestSkillInvocationIntegration:
    """Integration tests for skill invocation detection in main flow."""

    def test_skill_invoked_with_reflection_done(self):
        """When skill is invoked and reflection done, should allow."""
        transcript = """
        Skill: reflect
        Path: projectSettings:reflect

        五省を実施しました。

        1. 要件理解に悖るなかりしか
        - 問題なし

        2. 実装に恥づるなかりしか
        - 問題なし
        """
        assert check_skill_invocation(transcript)
        assert check_transcript_for_reflection(transcript)

    def test_skill_invoked_without_reflection(self):
        """When skill is invoked but reflection not done, should detect.

        Note: The transcript contains skill invocation markers but no reflection
        keywords (五省, 振り返り, etc.), so check_transcript_for_reflection should
        return False for the full transcript.
        """
        transcript = """
        Skill: reflect
        Path: projectSettings:reflect

        [{"type":"text","text":"@.claude/prompts/reflection/execute.md"}]

        セッション継続の処理を行いました。
        """
        assert check_skill_invocation(transcript)
        # Full transcript should not contain reflection keywords
        assert not check_transcript_for_reflection(transcript)

    def test_no_skill_with_reflection(self):
        """When no skill invoked but reflection present, should detect both."""
        transcript = """
        作業を完了しました。

        五省を実施します。
        1. 要件理解に悖るなかりしか - 問題なし
        """
        assert not check_skill_invocation(transcript)
        assert check_transcript_for_reflection(transcript)


class TestCheckMergePhaseCompleted:
    """Tests for check_merge_phase_completed function (Issue #2172)."""

    def test_detects_completed_merge_phase(self, tmp_path):
        """Should detect when a workflow has completed merge phase."""
        from unittest.mock import MagicMock

        state = {
            "session_id": "test-session",
            "workflows": {
                "issue-123": {
                    "current_phase": "cleanup",
                    "phases": {
                        "implementation": {"status": "completed", "iterations": 1},
                        "merge": {"status": "completed", "iterations": 1},
                        "cleanup": {"status": "in_progress", "iterations": 1},
                    },
                }
            },
        }
        state_file = tmp_path / "state-test-session.json"
        state_file.write_text(json.dumps(state))

        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with patch.object(reflection_completion_check, "_ctx", mock_ctx):
            with patch.object(reflection_completion_check, "FLOW_LOG_DIR", tmp_path):
                result = check_merge_phase_completed()
                assert result == ["issue-123"]

    def test_detects_multiple_merged_workflows(self, tmp_path):
        """Should detect multiple workflows with completed merge phases."""
        from unittest.mock import MagicMock

        state = {
            "session_id": "test-session",
            "workflows": {
                "issue-123": {
                    "phases": {"merge": {"status": "completed"}},
                },
                "issue-456": {
                    "phases": {"merge": {"status": "completed"}},
                },
                "issue-789": {
                    "phases": {"merge": {"status": "in_progress"}},
                },
            },
        }
        state_file = tmp_path / "state-test-session.json"
        state_file.write_text(json.dumps(state))

        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with patch.object(reflection_completion_check, "_ctx", mock_ctx):
            with patch.object(reflection_completion_check, "FLOW_LOG_DIR", tmp_path):
                result = check_merge_phase_completed()
                assert "issue-123" in result
                assert "issue-456" in result
                assert "issue-789" not in result

    def test_returns_empty_when_no_merge_phase(self, tmp_path):
        """Should return empty list when no workflow has merge phase."""
        from unittest.mock import MagicMock

        state = {
            "session_id": "test-session",
            "workflows": {
                "issue-123": {
                    "phases": {"implementation": {"status": "completed"}},
                }
            },
        }
        state_file = tmp_path / "state-test-session.json"
        state_file.write_text(json.dumps(state))

        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with patch.object(reflection_completion_check, "_ctx", mock_ctx):
            with patch.object(reflection_completion_check, "FLOW_LOG_DIR", tmp_path):
                result = check_merge_phase_completed()
                assert result == []

    def test_returns_empty_when_merge_in_progress(self, tmp_path):
        """Should return empty list when merge phase is not completed."""
        from unittest.mock import MagicMock

        state = {
            "session_id": "test-session",
            "workflows": {
                "issue-123": {
                    "phases": {"merge": {"status": "in_progress"}},
                }
            },
        }
        state_file = tmp_path / "state-test-session.json"
        state_file.write_text(json.dumps(state))

        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with patch.object(reflection_completion_check, "_ctx", mock_ctx):
            with patch.object(reflection_completion_check, "FLOW_LOG_DIR", tmp_path):
                result = check_merge_phase_completed()
                assert result == []

    def test_returns_empty_when_no_state_file(self, tmp_path):
        """Should return empty list when state file doesn't exist."""
        from unittest.mock import MagicMock

        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with patch.object(reflection_completion_check, "_ctx", mock_ctx):
            with patch.object(reflection_completion_check, "FLOW_LOG_DIR", tmp_path):
                result = check_merge_phase_completed()
                assert result == []

    def test_returns_empty_on_invalid_json(self, tmp_path):
        """Should return empty list when state file has invalid JSON."""
        from unittest.mock import MagicMock

        state_file = tmp_path / "state-test-session.json"
        state_file.write_text("not valid json")

        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with patch.object(reflection_completion_check, "_ctx", mock_ctx):
            with patch.object(reflection_completion_check, "FLOW_LOG_DIR", tmp_path):
                result = check_merge_phase_completed()
                assert result == []

    def test_returns_empty_when_no_workflows(self, tmp_path):
        """Should return empty list when state has no workflows."""
        from unittest.mock import MagicMock

        state = {"session_id": "test-session"}
        state_file = tmp_path / "state-test-session.json"
        state_file.write_text(json.dumps(state))

        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with patch.object(reflection_completion_check, "_ctx", mock_ctx):
            with patch.object(reflection_completion_check, "FLOW_LOG_DIR", tmp_path):
                result = check_merge_phase_completed()
                assert result == []


class TestIsValidImmediateAction:
    """Tests for is_valid_immediate_action function (Issue #2193, #2209)."""

    def test_reflect_is_allowed(self):
        """Should accept /reflect (the only allowed command)."""
        assert is_valid_immediate_action("/reflect")
        assert is_valid_immediate_action("/REFLECT")  # case insensitive
        assert is_valid_immediate_action("  /reflect  ")  # with whitespace

    def test_rejects_other_slash_commands(self):
        """Should reject slash commands not in whitelist (Issue #2209)."""
        # These caused false positives from test examples
        assert not is_valid_immediate_action("/commit")
        assert not is_valid_immediate_action("/test")
        assert not is_valid_immediate_action("/code-review")
        assert not is_valid_immediate_action("/run-tests")

    def test_rejects_non_slash_commands(self):
        """Should reject actions not starting with / (Issue #2201)."""
        assert not is_valid_immediate_action("run tests")
        assert not is_valid_immediate_action("create issue")
        assert not is_valid_immediate_action("action")
        assert not is_valid_immediate_action("アクション")

    def test_rejects_code_syntax(self):
        """Should reject code-like patterns."""
        assert not is_valid_immediate_action("{action_lower}")
        assert not is_valid_immediate_action("foo(bar)")
        assert not is_valid_immediate_action("/test()")

    def test_rejects_empty_or_slash_only(self):
        """Should reject empty actions or just slash."""
        assert not is_valid_immediate_action("")
        assert not is_valid_immediate_action("/")
        assert not is_valid_immediate_action("  ")


class TestExtractImmediateTags:
    """Tests for extract_immediate_tags function (Issue #2186)."""

    def test_extracts_single_tag(self):
        """Should extract a single [IMMEDIATE] tag."""
        transcript = "Some text [IMMEDIATE: /reflect] more text"
        result = extract_immediate_tags(transcript)
        assert result == ["/reflect"]

    def test_filters_non_whitelisted_commands(self):
        """Should only extract whitelisted commands (Issue #2209)."""
        # /commit is not in whitelist, only /reflect
        transcript = "[IMMEDIATE: /reflect] and [IMMEDIATE: /commit]"
        result = extract_immediate_tags(transcript)
        assert result == ["/reflect"]
        # Non-slash commands should also be filtered out
        transcript2 = "[IMMEDIATE: /reflect] and [IMMEDIATE: run tests]"
        result2 = extract_immediate_tags(transcript2)
        assert result2 == ["/reflect"]

    def test_deduplicates_tags(self):
        """Should not include duplicate tags."""
        transcript = "[IMMEDIATE: /reflect] ... [IMMEDIATE: /reflect]"
        result = extract_immediate_tags(transcript)
        assert result == ["/reflect"]

    def test_case_insensitive(self):
        """Should match case-insensitively."""
        transcript = "[immediate: /reflect] and [IMMEDIATE: /REFLECT]"
        result = extract_immediate_tags(transcript)
        # Both should resolve to /reflect (deduplicated)
        assert result == ["/reflect"]

    def test_handles_whitespace(self):
        """Should handle various whitespace in tags."""
        transcript = "[IMMEDIATE:  /reflect  ] and [IMMEDIATE:/REFLECT]"
        result = extract_immediate_tags(transcript)
        # Whitespace should be stripped, deduplicated
        assert result == ["/reflect"]

    def test_returns_empty_for_no_tags(self):
        """Should return empty list when no tags found."""
        transcript = "No immediate tags here"
        result = extract_immediate_tags(transcript)
        assert result == []

    def test_filters_out_code_fragments(self):
        """Should filter out false positives from code (Issue #2193)."""
        # This transcript contains code examples with the pattern definition
        transcript = """
        The pattern is defined as:
        pattern = r"[IMMEDIATE:\\s*([^\\]]+)\\]"

        And used like:
        check_immediate_action_executed(action, transcript)

        But here is a real tag:
        [IMMEDIATE: /reflect]
        """
        result = extract_immediate_tags(transcript)
        # Should only extract the real tag, not code fragments
        assert result == ["/reflect"]

    def test_filters_out_test_examples(self):
        """Should filter out test string examples (Issue #2193, #2209)."""
        transcript = """
        assert check_immediate_action_executed("run tests", transcript)
        assert not check_immediate_action_executed("{action_lower}", transcript)

        # /commit is not in whitelist, should be filtered
        Real tag: [IMMEDIATE: /commit]
        # Only /reflect is allowed
        Another tag: [IMMEDIATE: /reflect]
        """
        result = extract_immediate_tags(transcript)
        assert result == ["/reflect"]


class TestCheckImmediateActionExecuted:
    """Tests for check_immediate_action_executed function (Issue #2186)."""

    def test_reflect_action_with_skill_and_reflection(self):
        """Should return True only when BOTH skill invoked AND reflection content (Issue #2489)."""
        transcript = """
        Skill: reflect
        Path: projectSettings:reflect

        [IMMEDIATE: /reflect]

        ## 振り返り

        五省を実施します。
        """
        assert check_immediate_action_executed("/reflect", transcript)

    def test_reflect_action_without_skill_invocation(self):
        """Should return False when reflection content exists but skill NOT invoked (Issue #2489).

        This is the key fix: manual 五省 summaries without skill invocation should NOT pass.
        """
        transcript = """
        [IMMEDIATE: /reflect]

        ## 五省（Issue #2487セッション）

        ### 1. 至らなかったことはなかったか
        - なし

        ### 2. 言動に誠実さを欠くことはなかったか
        - なし
        """
        # Has reflection keywords (五省) but NO skill invocation
        assert check_transcript_for_reflection(transcript)  # Keywords present
        assert not check_skill_invocation(transcript)  # No skill invocation
        assert not check_immediate_action_executed("/reflect", transcript)  # Should fail

    def test_reflect_action_with_skill_but_no_reflection(self):
        """Should return False when skill invoked but no reflection content."""
        transcript = """
        Skill: reflect
        Path: projectSettings:reflect

        [IMMEDIATE: /reflect]

        作業を続けました。
        """
        # Has skill invocation but NO reflection keywords
        assert check_skill_invocation(transcript)  # Skill invoked
        assert not check_transcript_for_reflection(transcript)  # No keywords
        assert not check_immediate_action_executed("/reflect", transcript)  # Should fail

    def test_reflect_action_without_reflection(self):
        """Should return False when /reflect action has no reflection content."""
        transcript = """
        [IMMEDIATE: /reflect]

        作業を続けました。
        """
        assert not check_immediate_action_executed("/reflect", transcript)

    def test_generic_action_returns_false(self):
        """Should return False for non-/reflect actions (not yet supported)."""
        # Currently, only /reflect is verifiable
        # Other actions return False to indicate "not verified"
        transcript = "[IMMEDIATE: run tests] lots of content here"
        assert not check_immediate_action_executed("run tests", transcript)

    def test_generic_action_not_verified(self):
        """Generic actions are not yet verifiable and should return False."""
        transcript = "[IMMEDIATE: commit changes] " + "A" * 1000
        assert not check_immediate_action_executed("commit changes", transcript)


class TestImmediateTagIntegration:
    """Integration tests for [IMMEDIATE] tag verification flow (Issue #2186)."""

    def test_immediate_reflect_with_skill_and_reflection_done(self):
        """Full flow: [IMMEDIATE: /reflect] with skill invocation AND reflection → should pass (Issue #2489)."""
        transcript = """
        Skill: reflect
        Path: projectSettings:reflect

        [IMMEDIATE: /reflect]

        ## 振り返り

        五省を実施します。

        1. 要件理解に悖るなかりしか - 問題なし
        2. 実装に恥づるなかりしか - 問題なし
        """
        # Extract tags
        actions = extract_immediate_tags(transcript)
        assert "/reflect" in actions

        # Verify all actions executed (skill invoked AND reflection content present)
        unexecuted = [
            action for action in actions if not check_immediate_action_executed(action, transcript)
        ]
        assert unexecuted == []

    def test_immediate_reflect_without_skill_invocation(self):
        """Full flow: [IMMEDIATE: /reflect] with reflection but NO skill → should block (Issue #2489).

        This is the key scenario that was broken: manual 五省 bypassed enforcement.
        """
        transcript = """
        [IMMEDIATE: /reflect]

        ## 五省（Issue #2488セッション）

        ### 1. 至らなかったことはなかったか
        - なし

        ### 2. 言動に誠実さを欠くことはなかったか
        - なし
        """
        # Extract tags
        actions = extract_immediate_tags(transcript)
        assert "/reflect" in actions

        # Has reflection keywords but NO skill invocation → should block
        unexecuted = [
            action for action in actions if not check_immediate_action_executed(action, transcript)
        ]
        assert "/reflect" in unexecuted

    def test_immediate_reflect_without_reflection(self):
        """Full flow: [IMMEDIATE: /reflect] without reflection → should block."""
        transcript = """
        [IMMEDIATE: /reflect]

        作業を完了しました。PRをマージしました。
        """
        # Extract tags
        actions = extract_immediate_tags(transcript)
        assert "/reflect" in actions

        # Verify action NOT executed
        unexecuted = [
            action for action in actions if not check_immediate_action_executed(action, transcript)
        ]
        assert "/reflect" in unexecuted

    def test_non_slash_commands_filtered_out(self):
        """Full flow: [IMMEDIATE: run tests] → filtered out (Issue #2201)."""
        transcript = """
        [IMMEDIATE: run tests]

        テストを実行しました。すべて成功。
        """
        # Non-slash commands are filtered out (Issue #2201)
        actions = extract_immediate_tags(transcript)
        assert actions == []  # "run tests" is not a slash command

    def test_multiple_immediate_tags_all_executed(self):
        """Full flow: Multiple tags, all executed → should pass (Issue #2489)."""
        transcript = """
        Skill: reflect
        Path: projectSettings:reflect

        [IMMEDIATE: /reflect]

        ## 振り返り
        五省を実施しました。

        1. 要件理解に悖るなかりしか - 問題なし
        """
        actions = extract_immediate_tags(transcript)
        assert "/reflect" in actions

        # /reflect is executed (skill invoked AND reflection content)
        unexecuted = [
            action for action in actions if not check_immediate_action_executed(action, transcript)
        ]
        assert unexecuted == []

    def test_non_whitelisted_commands_filtered(self):
        """Full flow: Non-whitelisted commands are filtered out (Issue #2209)."""
        transcript = """
        Skill: reflect
        Path: projectSettings:reflect

        [IMMEDIATE: /reflect]
        [IMMEDIATE: /commit]

        ## 振り返り
        五省を実施しました。
        """
        actions = extract_immediate_tags(transcript)
        # Only /reflect is in the whitelist
        assert actions == ["/reflect"]
        # /commit is filtered out (not in whitelist)

        # /reflect executed (skill invoked AND 五省 keyword)
        unexecuted = [
            action for action in actions if not check_immediate_action_executed(action, transcript)
        ]
        assert unexecuted == []

    def test_no_immediate_tags(self):
        """Full flow: No [IMMEDIATE] tags → no blocking."""
        transcript = """
        通常の作業を完了しました。
        PRをマージしました。
        """
        actions = extract_immediate_tags(transcript)
        assert actions == []

        # No actions to verify
        unexecuted = [
            action for action in actions if not check_immediate_action_executed(action, transcript)
        ]
        assert unexecuted == []
