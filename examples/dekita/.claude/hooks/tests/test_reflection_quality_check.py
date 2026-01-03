#!/usr/bin/env python3
"""Unit tests for reflection-quality-check.py"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Dynamic import for hyphenated module name
HOOK_PATH = Path(__file__).parent.parent / "reflection-quality-check.py"
_spec = importlib.util.spec_from_file_location("reflection_quality_check", HOOK_PATH)
reflection_quality_check = importlib.util.module_from_spec(_spec)
sys.modules["reflection_quality_check"] = reflection_quality_check
_spec.loader.exec_module(reflection_quality_check)

check_transcript_for_no_problem = reflection_quality_check.check_transcript_for_no_problem
check_high_scores = reflection_quality_check.check_high_scores
check_root_cause_analysis = reflection_quality_check.check_root_cause_analysis
check_improvements_without_issues = reflection_quality_check.check_improvements_without_issues
should_block_reflection = reflection_quality_check.should_block_reflection
get_block_count = reflection_quality_check.get_block_count


class TestCheckTranscriptForNoProblem:
    """Tests for check_transcript_for_no_problem function."""

    def test_detects_mondai_nashi(self):
        """Should detect 問題なし pattern."""
        content = """
        ## 五省
        問題なし
        """
        assert check_transcript_for_no_problem(content) is True

    def test_detects_tokuni_mondai(self):
        """Should detect 特に問題 pattern."""
        content = """
        ## 振り返り
        特に問題はありませんでした。
        """
        assert check_transcript_for_no_problem(content) is True

    def test_detects_kaizenten_nashi(self):
        """Should detect 改善点なし pattern."""
        content = """
        ## 五省
        改善点なし
        """
        assert check_transcript_for_no_problem(content) is True

    def test_detects_hanseiten_nai(self):
        """Should detect 反省点がない pattern."""
        content = """
        ## 反省
        反省点はないです。
        """
        assert check_transcript_for_no_problem(content) is True

    def test_no_problem_outside_reflection_context(self):
        """Should not detect 問題なし outside reflection context."""
        content = """
        このコードは問題なしです。
        実装完了しました。
        """
        assert check_transcript_for_no_problem(content) is False

    def test_with_reflection_context_before(self):
        """Should detect pattern after reflection context starts."""
        content = """
        ## 五省
        1. 要件理解に悖るなかりしか
           問題なし
        """
        assert check_transcript_for_no_problem(content) is True

    def test_empty_content(self):
        """Should return False for empty content."""
        assert check_transcript_for_no_problem("") is False


class TestCheckHighScores:
    """Tests for check_high_scores function."""

    def test_detects_high_scores(self):
        """Should detect 5+ high scores (4/5 or 5/5) in reflection context."""
        content = """
        ## 五省
        1. 要件理解: 5/5
        2. 実装品質: 4/5
        3. 検証: 5/5
        4. 対応: 5/5
        5. 効率: 4/5
        """
        assert check_high_scores(content) is True

    def test_detects_mixed_scores(self):
        """Should detect when high scores dominate in reflection context."""
        content = """
        ## 振り返り
        1. 要件理解: 5/5
        2. 実装品質: 5/5
        3. 検証: 4/5
        4. 対応: 5/5
        5. 効率: 5/5
        6. その他: 4/5
        """
        assert check_high_scores(content) is True

    def test_low_scores_not_flagged(self):
        """Should not flag when scores are low."""
        content = """
        ## 五省
        1. 要件理解: 3/5
        2. 実装品質: 2/5
        3. 検証: 3/5
        """
        assert check_high_scores(content) is False

    def test_few_high_scores_not_flagged(self):
        """Should not flag when only few high scores."""
        content = """
        ## 振り返り
        1. 要件理解: 5/5
        2. 実装品質: 5/5
        """
        assert check_high_scores(content) is False

    def test_empty_content(self):
        """Should return False for empty content."""
        assert check_high_scores("") is False

    def test_high_scores_outside_reflection_context(self):
        """Should not flag high scores outside reflection context."""
        content = """
        テスト結果: 5/5 5/5 5/5 5/5 5/5
        すべてのテストがパスしました。
        """
        assert check_high_scores(content) is False


class TestCheckRootCauseAnalysis:
    """Tests for check_root_cause_analysis function.

    Issue #2005: Detects if root cause analysis was performed.
    """

    def test_detects_naze_pattern(self):
        """Should detect なぜ pattern in reflection context."""
        content = """
        ## 振り返り
        なぜブロックされたかを分析すると、
        """
        assert check_root_cause_analysis(content) is True

    def test_detects_konpon_genin(self):
        """Should detect 根本原因 pattern."""
        content = """
        ## 問題分析
        根本原因はセッション再開時の状態リセット
        """
        assert check_root_cause_analysis(content) is True

    def test_detects_genin_wa(self):
        """Should detect 原因は pattern."""
        content = """
        ## 五省
        原因はCodexレビューを忘れたこと
        """
        assert check_root_cause_analysis(content) is True

    def test_detects_sankai_jimon(self):
        """Should detect 3回自問 pattern."""
        content = """
        ## 反省
        「他にないか？」を3回自問した結果
        """
        assert check_root_cause_analysis(content) is True

    def test_detects_hoka_ni_naika(self):
        """Should detect 他にないか pattern."""
        content = """
        ## 分析
        他にないかを確認したところ
        """
        assert check_root_cause_analysis(content) is True

    def test_no_detection_outside_context(self):
        """Should not detect patterns outside reflection context."""
        content = """
        コードを修正しました。
        テストがパスしました。
        """
        assert check_root_cause_analysis(content) is False

    def test_empty_content(self):
        """Should return False for empty content."""
        assert check_root_cause_analysis("") is False


class TestShouldBlockReflection:
    """Tests for should_block_reflection function.

    Issue #2005: Changed from warning to blocking.
    - Contradiction + no root cause = BLOCK
    - Contradiction + root cause = PASS
    """

    def test_blocks_high_blocks_with_no_problem_no_root_cause(self):
        """Should block for high block count with no problem and no root cause."""
        message = should_block_reflection(
            block_count=5,
            has_no_problem=True,
            has_high_scores=False,
            has_root_cause=False,
        )
        assert message is not None
        assert "5回" in message
        assert "ブロック" in message

    def test_allows_high_blocks_with_root_cause(self):
        """Should allow when root cause analysis is present."""
        message = should_block_reflection(
            block_count=5,
            has_no_problem=True,
            has_high_scores=False,
            has_root_cause=True,
        )
        assert message is None

    def test_blocks_high_scores_without_root_cause(self):
        """Should block for high scores without root cause."""
        message = should_block_reflection(
            block_count=4,
            has_no_problem=False,
            has_high_scores=True,
            has_root_cause=False,
        )
        assert message is not None
        assert "4回" in message

    def test_allows_high_scores_with_root_cause(self):
        """Should allow high scores when root cause is present."""
        message = should_block_reflection(
            block_count=4,
            has_no_problem=False,
            has_high_scores=True,
            has_root_cause=True,
        )
        assert message is None

    def test_no_block_when_low_blocks(self):
        """Should not block when block count is low."""
        message = should_block_reflection(
            block_count=2,
            has_no_problem=True,
            has_high_scores=True,
            has_root_cause=False,
        )
        assert message is None

    def test_no_block_without_no_problem(self):
        """Should not block when no 'no problem' claim."""
        message = should_block_reflection(
            block_count=10,
            has_no_problem=False,
            has_high_scores=False,
            has_root_cause=False,
        )
        assert message is None

    def test_threshold_boundary(self):
        """Should block at exactly threshold (3 blocks) without root cause."""
        message = should_block_reflection(
            block_count=3,
            has_no_problem=True,
            has_high_scores=False,
            has_root_cause=False,
        )
        assert message is not None
        assert "3回" in message

    def test_below_threshold(self):
        """Should not block below threshold (2 blocks)."""
        message = should_block_reflection(
            block_count=2,
            has_no_problem=True,
            has_high_scores=False,
            has_root_cause=False,
        )
        assert message is None


class TestCheckImprovementsWithoutIssues:
    """Tests for check_improvements_without_issues function.

    Issue #2354: Detects improvement points without Issue references.
    """

    def test_detects_kaizenten_without_issue(self):
        """Should detect 改善点 without Issue reference."""
        content = """
        ## 振り返り
        改善点: mainブランチで編集しようとした
        """
        results = check_improvements_without_issues(content)
        assert len(results) == 1
        assert "改善点" in results[0]

    def test_allows_kaizenten_with_issue(self):
        """Should allow 改善点 with Issue reference."""
        content = """
        ## 振り返り
        改善点: mainブランチで編集しようとした → Issue #2354
        """
        results = check_improvements_without_issues(content)
        assert len(results) == 0

    def test_detects_subeki_datta_without_issue(self):
        """Should detect 〜すべきだった without Issue reference."""
        content = """
        ## 問題
        worktreeを事前に作成すべきだった
        """
        results = check_improvements_without_issues(content)
        assert len(results) == 1
        assert "すべきだった" in results[0]

    def test_allows_subeki_datta_with_issue(self):
        """Should allow 〜すべきだった with Issue reference."""
        content = """
        ## 問題
        worktreeを事前に作成すべきだった #123
        """
        results = check_improvements_without_issues(content)
        assert len(results) == 0

    def test_detects_multiple_improvements(self):
        """Should detect multiple improvements without Issue references."""
        content = """
        ## 振り返り
        改善点: mainブランチで編集しようとした
        問題点: Skillを使用しなかった
        次回は気をつける必要がある
        """
        results = check_improvements_without_issues(content)
        assert len(results) == 3

    def test_no_detection_outside_context(self):
        """Should not detect outside reflection context.

        Note: 改善/問題 keywords trigger context detection,
        so this test uses content without those keywords.
        """
        content = """
        コードのクオリティを上げたい。
        次のステップとして検討する。
        """
        results = check_improvements_without_issues(content)
        assert len(results) == 0

    def test_empty_content(self):
        """Should return empty list for empty content."""
        results = check_improvements_without_issues("")
        assert len(results) == 0

    def test_allows_issue_reference_various_formats(self):
        """Should recognize various Issue reference formats."""
        content = """
        ## 改善
        改善点: Issue #123 で対応予定
        問題点について Issue#456 を作成
        すべきだった点は issue-789 で追跡
        """
        results = check_improvements_without_issues(content)
        assert len(results) == 0

    def test_no_detection_for_negation_patterns(self):
        """Should not detect negation patterns like 改善点なし (Copilot review)."""
        content = """
        ## 振り返り
        改善点なし
        問題点はありません
        改善点は特にない
        """
        results = check_improvements_without_issues(content)
        assert len(results) == 0

    def test_no_detection_for_section_headers(self):
        """Should not detect section headers themselves (Copilot review)."""
        content = """
        ## 改善点
        特になし

        ## 問題点
        なし
        """
        results = check_improvements_without_issues(content)
        assert len(results) == 0

    def test_allows_followup_issue_not_needed(self):
        """Should allow when follow-up response states 'Issue不要' (Issue #2362)."""
        content = """
        ## 振り返り
        改善点: mainブランチで編集しようとした

        ## 対応状況
        改善点1: mainブランチで編集しようとした → Issue不要（軽微）
        """
        results = check_improvements_without_issues(content)
        assert len(results) == 0

    def test_allows_followup_issue_reference(self):
        """Should allow when follow-up response adds Issue reference (Issue #2362)."""
        content = """
        ## 振り返り
        改善点: fork-sessionの問題

        ## 後の対応
        上記の改善点については Issue #2350 で対応済み
        """
        results = check_improvements_without_issues(content)
        assert len(results) == 0

    def test_allows_multiple_followup_responses(self):
        """Should allow when follow-up response addresses all improvements (Issue #2362)."""
        content = """
        ## 振り返り
        改善点1: resolve-thread-guard連続ブロック
        改善点2: Issue #2350の根本解決が未完了

        ## 対応状況
        改善点1 → Issue不要（軽微、ルール再確認で対応可能）
        改善点2 → Issue #2350 で対応済み
        """
        results = check_improvements_without_issues(content)
        assert len(results) == 0

    def test_blocks_when_followup_is_insufficient(self):
        """Should block when follow-up doesn't address all improvements (Issue #2362)."""
        content = """
        ## 振り返り
        改善点1: 問題A
        改善点2: 問題B
        改善点3: 問題C

        ## 対応
        問題Aについては Issue #123 で対応
        """
        results = check_improvements_without_issues(content)
        # 3 improvements, only 1 addressed
        assert len(results) >= 1

    def test_ignores_issue_refs_before_improvements(self):
        """Should NOT count Issue refs that appear BEFORE improvements (Codex review fix).

        This test ensures that unrelated Issue mentions outside the reflection section
        don't incorrectly mask unaddressed improvements. For example, if the transcript
        mentions "Issue #123 を作成しました" before any improvement is detected,
        that reference should not count toward addressing improvements.
        """
        content = """
        Issue #2362 を作成しました。

        ## 振り返り
        改善点: mainブランチで編集しようとした
        """
        results = check_improvements_without_issues(content)
        # The Issue #2362 mention is BEFORE the improvement, so it shouldn't count
        assert len(results) == 1
        assert "mainブランチ" in results[0]

    def test_counts_issue_refs_only_after_improvements(self):
        """Should count Issue refs only AFTER improvements are detected (Codex review fix).

        This ensures follow-up responses that address improvements are properly recognized,
        while unrelated Issue mentions before the improvements are ignored.
        """
        content = """
        Issue #1234 を作成しました。  # This should NOT count

        ## 振り返り
        改善点: mainブランチで編集しようとした

        Issue #2362 で対応します。  # This SHOULD count (after improvement)
        """
        results = check_improvements_without_issues(content)
        # The Issue #2362 mention is AFTER the improvement, so it should count
        assert len(results) == 0

    def test_inline_issue_on_improvement_does_not_count_as_followup(self):
        """Should NOT count inline Issue refs on improvements as follow-up (Codex review fix).

        When one improvement has an inline Issue ref and another doesn't,
        the inline ref should not count toward addressing the unaddressed improvement.
        """
        content = """
        ## 振り返り
        改善点A: 問題X
        改善点B: 問題Y → Issue #123 で対応済み
        """
        results = check_improvements_without_issues(content)
        # 改善点A has no Issue ref and should be flagged
        # 改善点B has inline Issue ref but that should NOT count as follow-up for A
        assert len(results) == 1
        assert "問題X" in results[0]

    def test_mixed_inline_and_followup_responses(self):
        """Should handle mix of inline Issue refs and follow-up responses correctly.

        Inline Issue refs on improvement lines should not count as follow-up,
        but separate follow-up lines should count.
        """
        content = """
        ## 振り返り
        改善点A: 問題X
        改善点B: 問題Y → Issue #123 で対応済み

        ## 対応状況
        問題Xについては Issue #456 で対応
        """
        results = check_improvements_without_issues(content)
        # 改善点A (問題X) is addressed by follow-up "Issue #456"
        # 改善点B is addressed inline
        assert len(results) == 0


class TestGetBlockCount:
    """Tests for get_block_count function."""

    def test_counts_blocks_for_session(self):
        """Should count blocks for specific session."""
        mock_entries = [
            {"session_id": "test-session", "decision": "block"},
            {"session_id": "test-session", "decision": "block"},
            {"session_id": "test-session", "decision": "approve"},
            # other-session entries are filtered by read_session_log_entries
        ]

        with patch.object(
            reflection_quality_check, "read_session_log_entries", return_value=mock_entries
        ):
            count = get_block_count("test-session")
            assert count == 2

    def test_returns_zero_for_missing_file(self):
        """Should return 0 when log file doesn't exist."""
        with patch.object(reflection_quality_check, "read_session_log_entries", return_value=[]):
            count = get_block_count("any-session")
            assert count == 0

    def test_handles_malformed_lines(self):
        """Should skip malformed log lines (handled by read_session_log_entries)."""
        # read_session_log_entries already handles malformed lines
        mock_entries = [
            {"session_id": "test", "decision": "block"},
            {"session_id": "test", "decision": "block"},
        ]

        with patch.object(
            reflection_quality_check, "read_session_log_entries", return_value=mock_entries
        ):
            count = get_block_count("test")
            assert count == 2


class TestMainIntegration:
    """Integration tests for main function."""

    def test_approves_when_no_contradiction(self, capsys):
        """Should approve when no contradiction detected."""
        input_data = {"hook_type": "Stop", "transcript_path": ""}
        mock_ctx = MagicMock()
        mock_ctx.get_session_id.return_value = "test-session"
        with patch.object(
            reflection_quality_check,
            "create_hook_context",
            return_value=mock_ctx,
        ):
            with patch.object(reflection_quality_check, "get_block_count", return_value=2):
                with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                    reflection_quality_check.main()
                    captured = capsys.readouterr()
                    result = json.loads(captured.out)
                    assert result.get("continue", True) is True

    def test_blocks_when_contradiction_without_root_cause(self, capsys):
        """Should BLOCK when contradiction detected without root cause analysis."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_file = Path(tmpdir) / "transcript.txt"
            transcript_file.write_text("## 五省\n問題なし\n全項目 5/5 です。5/5 5/5 5/5 5/5\n")

            input_data = {
                "hook_type": "Stop",
                "transcript_path": str(transcript_file),
            }
            mock_ctx = MagicMock()
            mock_ctx.get_session_id.return_value = "test-session"
            with patch.object(
                reflection_quality_check,
                "create_hook_context",
                return_value=mock_ctx,
            ):
                with patch.object(reflection_quality_check, "get_block_count", return_value=5):
                    # Mock is_safe_transcript_path to allow temp file
                    with patch.object(
                        reflection_quality_check,
                        "is_safe_transcript_path",
                        return_value=True,
                    ):
                        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                            reflection_quality_check.main()
                            captured = capsys.readouterr()
                            result = json.loads(captured.out)
                            # Should BLOCK (decision=block)
                            assert result.get("decision") == "block"
                            assert "reason" in result
                            assert "ブロック" in result["reason"]

    def test_allows_when_root_cause_present(self, capsys):
        """Should allow when root cause analysis is present.

        Note: This test uses a realistic scenario where issues were found but
        root cause analysis was performed. "問題あり" with root cause analysis
        is the correct pattern (vs "問題なし" which would be contradictory).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_file = Path(tmpdir) / "transcript.txt"
            # Realistic scenario: issues found, but root cause analyzed
            transcript_file.write_text(
                "## 五省\n問題がありました\n根本原因はセッション再開時の状態リセット\n"
            )

            input_data = {
                "hook_type": "Stop",
                "transcript_path": str(transcript_file),
            }
            mock_ctx = MagicMock()
            mock_ctx.get_session_id.return_value = "test-session"
            with patch.object(
                reflection_quality_check,
                "create_hook_context",
                return_value=mock_ctx,
            ):
                with patch.object(reflection_quality_check, "get_block_count", return_value=5):
                    with patch.object(
                        reflection_quality_check,
                        "is_safe_transcript_path",
                        return_value=True,
                    ):
                        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                            reflection_quality_check.main()
                            captured = capsys.readouterr()
                            result = json.loads(captured.out)
                            # Should allow (continue=True or no continue key)
                            assert result.get("continue", True) is True

    def test_error_handling_allows_continuation(self, capsys):
        """Should allow continuation on errors."""
        input_data = {"invalid": "data"}
        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            reflection_quality_check.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            # Should not block on errors
            assert result.get("continue", True) is True
