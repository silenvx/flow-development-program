#!/usr/bin/env python3
"""Unit tests for reflection-self-check.py"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for lib module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Dynamic import for hyphenated module name
HOOK_PATH = Path(__file__).parent.parent / "reflection-self-check.py"
_spec = importlib.util.spec_from_file_location("reflection_self_check", HOOK_PATH)
reflection_self_check = importlib.util.module_from_spec(_spec)
sys.modules["reflection_self_check"] = reflection_self_check
_spec.loader.exec_module(reflection_self_check)

has_reflection = reflection_self_check.has_reflection
check_perspective = reflection_self_check.check_perspective
get_missing_perspectives = reflection_self_check.get_missing_perspectives
build_checklist_message = reflection_self_check.build_checklist_message
get_session_block_patterns = reflection_self_check.get_session_block_patterns
analyze_session_reflection_hints = reflection_self_check.analyze_session_reflection_hints
build_session_hints_message = reflection_self_check.build_session_hints_message

PERSPECTIVES = reflection_self_check.PERSPECTIVES
MIN_REPEAT_COUNT = reflection_self_check.MIN_REPEAT_COUNT


class TestHasReflection:
    """Tests for has_reflection function."""

    def test_detects_gosei(self):
        """Should detect 五省 keyword."""
        content = "## 五省\n1. 要件理解に悖るなかりしか"
        assert has_reflection(content) is True

    def test_detects_furikaeri(self):
        """Should detect 振り返り keyword."""
        content = "## 振り返り\n今日の作業を振り返ります"
        assert has_reflection(content) is True

    def test_detects_hansei(self):
        """Should detect 反省 keyword."""
        content = "反省点として..."
        assert has_reflection(content) is True

    def test_detects_kyoukun(self):
        """Should detect 教訓 keyword."""
        content = "教訓として学んだこと"
        assert has_reflection(content) is True

    def test_detects_kaizenten(self):
        """Should detect 改善点 keyword."""
        content = "改善点を洗い出します"
        assert has_reflection(content) is True

    def test_no_reflection_keywords(self):
        """Should return False when no reflection keywords present."""
        content = "コードを実装しました。テストがパスしました。"
        assert has_reflection(content) is False

    def test_empty_content(self):
        """Should return False for empty content."""
        assert has_reflection("") is False


class TestCheckPerspective:
    """Tests for check_perspective function."""

    def test_detects_keyword(self):
        """Should detect when keyword is present."""
        content = "ログを確認しました"
        keywords = [r"ログ", r"確認"]
        assert check_perspective(content, keywords) is True

    def test_no_keyword(self):
        """Should return False when no keyword present."""
        content = "作業しました"
        keywords = [r"ログ", r"確認"]
        assert check_perspective(content, keywords) is False

    def test_regex_pattern(self):
        """Should support regex patterns."""
        content = "十分に検討しました"
        keywords = [r"十分.*検討"]
        assert check_perspective(content, keywords) is True

    def test_empty_content(self):
        """Should return False for empty content."""
        assert check_perspective("", [r"test"]) is False


class TestGetMissingPerspectives:
    """Tests for get_missing_perspectives function."""

    def test_all_perspectives_missing(self):
        """Should return all perspectives when none addressed."""
        content = "今日の作業を終えました。"
        missing = get_missing_perspectives(content)
        assert len(missing) == len(PERSPECTIVES)

    def test_all_perspectives_addressed(self):
        """Should return empty list when all perspectives addressed."""
        content = """
        ## 振り返り
        ログを確認し、事実を調査しました。
        異常パターンとして繰り返しのタイムアウトがありました。
        根本原因を分析し、なぜこうなったか調べました。
        「他にないか？」を3回自問しました。
        十分に検討したうえで判断しました。
        Issue #123を作成しました。
        「対応済み」判断なし。
        振り返り自体の品質も確認しました。
        動作確認を実施し、Dogfoodingで実データを確認しました。
        """
        missing = get_missing_perspectives(content)
        assert len(missing) == 0

    def test_partial_perspectives_addressed(self):
        """Should return only missing perspectives."""
        content = """
        ログを確認しました。
        根本原因を分析しました。
        """
        missing = get_missing_perspectives(content)
        # session_facts and root_cause are addressed
        # anomaly_patterns, oversight_check, hasty_judgment, issue_creation are missing
        addressed_ids = {"session_facts", "root_cause"}
        missing_ids = {p["id"] for p in missing}
        expected_missing = {p["id"] for p in PERSPECTIVES if p["id"] not in addressed_ids}
        assert missing_ids == expected_missing

    def test_empty_content(self):
        """Should return all perspectives for empty content."""
        missing = get_missing_perspectives("")
        assert len(missing) == len(PERSPECTIVES)


class TestBuildChecklistMessage:
    """Tests for build_checklist_message function."""

    def test_includes_header(self):
        """Should include checklist header."""
        missing = [PERSPECTIVES[0]]
        message = build_checklist_message(missing)
        assert "振り返り観点チェック" in message

    def test_includes_perspective_name(self):
        """Should include perspective name."""
        missing = [PERSPECTIVES[0]]
        message = build_checklist_message(missing)
        assert PERSPECTIVES[0]["name"] in message

    def test_includes_perspective_description(self):
        """Should include perspective description."""
        missing = [PERSPECTIVES[0]]
        message = build_checklist_message(missing)
        assert PERSPECTIVES[0]["description"] in message

    def test_multiple_perspectives(self):
        """Should include all missing perspectives."""
        missing = PERSPECTIVES[:3]
        message = build_checklist_message(missing)
        for p in missing:
            assert p["name"] in message

    def test_empty_list(self):
        """Should still include header for empty list."""
        message = build_checklist_message([])
        assert "振り返り観点チェック" in message


class TestMainIntegration:
    """Integration tests for main function."""

    def test_skips_when_no_reflection(self, capsys):
        """Should skip check when no reflection detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_file = Path(tmpdir) / "transcript.txt"
            transcript_file.write_text("コードを実装しました。")

            input_data = {
                "hook_type": "Stop",
                "transcript_path": str(transcript_file),
            }
            with patch.object(
                reflection_self_check,
                "is_safe_transcript_path",
                return_value=True,
            ):
                with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                    reflection_self_check.main()
                    captured = capsys.readouterr()
                    result = json.loads(captured.out)
                    # Should approve (decision=approve)
                    assert result.get("decision") == "approve"
                    # Should NOT contain perspective checklist warning
                    if "systemMessage" in result:
                        assert "振り返り観点チェック" not in result["systemMessage"]

    def test_blocks_for_missing_perspectives(self, capsys):
        """Should block when perspectives are missing (Issue #2251)."""
        import pytest

        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_file = Path(tmpdir) / "transcript.txt"
            # Only reflection keywords, no perspective keywords
            transcript_file.write_text("## 五省\n今日の振り返りです。")

            input_data = {
                "hook_type": "Stop",
                "transcript_path": str(transcript_file),
            }
            with patch.object(
                reflection_self_check,
                "is_safe_transcript_path",
                return_value=True,
            ):
                with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                    with pytest.raises(SystemExit) as exc_info:
                        reflection_self_check.main()
                    assert exc_info.value.code == 2
                    captured = capsys.readouterr()
                    result = json.loads(captured.out)
                    # Should block with reason containing checklist
                    assert result.get("decision") == "block"
                    assert "reason" in result
                    assert "振り返り観点チェック" in result["reason"]

    def test_no_warning_when_all_perspectives_addressed(self, capsys):
        """Should not show warning when all perspectives addressed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_file = Path(tmpdir) / "transcript.txt"
            content = """
            ## 振り返り
            ログを確認し、事実を調査しました。
            異常パターンとしてタイムアウトの連続がありました。
            なぜこうなったか根本原因を分析しました。
            「他にないか？」を3回自問しました。
            十分に検討したうえで判断しました。
            Issue #123を作成しました。
            「対応済み」判断なし。
            振り返り自体の品質も確認しました。
            動作確認を実施し、Dogfoodingで正常系を確認しました。
            """
            transcript_file.write_text(content)

            input_data = {
                "hook_type": "Stop",
                "transcript_path": str(transcript_file),
            }
            with patch.object(
                reflection_self_check,
                "is_safe_transcript_path",
                return_value=True,
            ):
                with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                    reflection_self_check.main()
                    captured = capsys.readouterr()
                    result = json.loads(captured.out)
                    # Should approve (decision=approve)
                    assert result.get("decision") == "approve"
                    # Should NOT contain perspective checklist warning
                    if "systemMessage" in result:
                        assert "振り返り観点チェック" not in result["systemMessage"]

    def test_error_handling_allows_continuation(self, capsys):
        """Should allow continuation on errors."""
        input_data = {"invalid": "data"}
        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            reflection_self_check.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            # Should not block on errors (decision should be approve)
            assert result.get("decision") == "approve"


class TestGetSessionBlockPatterns:
    """Tests for get_session_block_patterns function (Issue #2278)."""

    def test_returns_empty_dict_when_log_file_missing(self):
        """Should return empty dict when session log file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            with patch.object(
                reflection_self_check,
                "__file__",
                str(tmpdir_path / "hooks" / "reflection-self-check.py"),
            ):
                result = get_session_block_patterns("nonexistent-session-id")
                assert result == {}

    def test_parses_valid_block_patterns(self):
        """Should parse valid block pattern entries for current session."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            logs_dir = tmpdir_path / "logs" / "metrics"
            logs_dir.mkdir(parents=True)
            session_id = "test-session-123"
            log_file = logs_dir / f"block-patterns-{session_id}.jsonl"
            log_file.write_text(
                '{"type": "block", "hook": "worktree-warning"}\n'
                '{"type": "block", "hook": "worktree-warning"}\n'
                '{"type": "block", "hook": "merge-check"}\n'
            )
            with patch.object(
                reflection_self_check,
                "__file__",
                str(tmpdir_path / "hooks" / "reflection-self-check.py"),
            ):
                result = get_session_block_patterns(session_id)
                assert isinstance(result, dict)
                assert result.get("worktree-warning") == 2
                assert result.get("merge-check") == 1

    def test_ignores_non_block_entries(self):
        """Should ignore entries that are not type=block."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            logs_dir = tmpdir_path / "logs" / "metrics"
            logs_dir.mkdir(parents=True)
            session_id = "test-session-456"
            log_file = logs_dir / f"block-patterns-{session_id}.jsonl"
            log_file.write_text(
                '{"type": "block", "hook": "test-hook"}\n'
                '{"type": "block_recovery", "hook": "test-hook"}\n'
            )
            with patch.object(
                reflection_self_check,
                "__file__",
                str(tmpdir_path / "hooks" / "reflection-self-check.py"),
            ):
                result = get_session_block_patterns(session_id)
                assert result.get("test-hook") == 1  # Only the block, not recovery

    def test_handles_malformed_json_gracefully(self):
        """Should skip malformed JSON lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            logs_dir = tmpdir_path / "logs" / "metrics"
            logs_dir.mkdir(parents=True)
            session_id = "test-session-789"
            log_file = logs_dir / f"block-patterns-{session_id}.jsonl"
            log_file.write_text("not valid json\n")
            with patch.object(
                reflection_self_check,
                "__file__",
                str(tmpdir_path / "hooks" / "reflection-self-check.py"),
            ):
                result = get_session_block_patterns(session_id)
            assert isinstance(result, dict)
            assert result == {}

    def test_rejects_path_traversal_attack(self):
        """Should return empty dict for path traversal attempts (security)."""
        # No need to patch __file__ - validation happens before file access
        assert get_session_block_patterns("../../../etc/passwd") == {}
        assert get_session_block_patterns("session/../../../etc") == {}
        assert get_session_block_patterns("") == {}


class TestAnalyzeSessionReflectionHints:
    """Tests for analyze_session_reflection_hints function (Issue #2278)."""

    def test_returns_empty_list_for_no_patterns(self):
        """Should return empty list when no block patterns."""
        result = analyze_session_reflection_hints({})
        assert result == []

    def test_returns_empty_list_for_single_blocks(self):
        """Should return empty list when all hooks blocked only once."""
        block_patterns = {"hook-a": 1, "hook-b": 1}
        result = analyze_session_reflection_hints(block_patterns)
        assert result == []

    def test_detects_repeated_blocks(self):
        """Should detect hooks that blocked multiple times."""
        block_patterns = {"hook-a": 3, "hook-b": 1}
        result = analyze_session_reflection_hints(block_patterns)
        assert len(result) == 1
        assert result[0]["hook"] == "hook-a"
        assert result[0]["count"] == 3

    def test_sorts_by_count_descending(self):
        """Should sort hints by block count descending."""
        block_patterns = {"hook-a": 2, "hook-b": 5, "hook-c": 3}
        result = analyze_session_reflection_hints(block_patterns)
        assert len(result) == 3
        assert result[0]["hook"] == "hook-b"
        assert result[1]["hook"] == "hook-c"
        assert result[2]["hook"] == "hook-a"

    def test_limits_to_top_3(self):
        """Should limit hints to top 3 repeated patterns."""
        block_patterns = {f"hook-{i}": i + 2 for i in range(5)}  # 5 hooks, all >= 2
        result = analyze_session_reflection_hints(block_patterns)
        assert len(result) == 3

    def test_includes_hint_message(self):
        """Should include actionable hint message."""
        block_patterns = {"merge-check": 4}
        result = analyze_session_reflection_hints(block_patterns)
        assert len(result) == 1
        assert "merge-check" in result[0]["hint"]
        assert "4回" in result[0]["hint"]
        assert "振り返る" in result[0]["hint"]


class TestBuildSessionHintsMessage:
    """Tests for build_session_hints_message function (Issue #2278)."""

    def test_returns_empty_string_for_no_hints(self):
        """Should return empty string when no hints."""
        result = build_session_hints_message([])
        assert result == ""

    def test_includes_header(self):
        """Should include header when there are hints."""
        hints = [{"hook": "test-hook", "count": 3, "hint": "test hint"}]
        result = build_session_hints_message(hints)
        assert "このセッションの振り返りポイント" in result

    def test_includes_hint_details(self):
        """Should include hint message."""
        hints = [{"hook": "test-hook", "count": 3, "hint": "test hint message"}]
        result = build_session_hints_message(hints)
        assert "test hint message" in result

    def test_includes_multiple_hints(self):
        """Should include all hints."""
        hints = [
            {"hook": "hook-a", "count": 5, "hint": "hint for hook-a"},
            {"hook": "hook-b", "count": 3, "hint": "hint for hook-b"},
        ]
        result = build_session_hints_message(hints)
        assert "hint for hook-a" in result
        assert "hint for hook-b" in result

    def test_uses_correct_icon(self):
        """Should use repeat icon for hints."""
        hints = [{"hook": "test-hook", "count": 3, "hint": "test hint"}]
        result = build_session_hints_message(hints)
        assert "🔄" in result


class TestImplementationVerificationPerspective:
    """Tests for implementation_verification perspective (Issue #2582)."""

    def test_detects_dogfooding_keyword(self):
        """Should detect Dogfooding keyword."""
        content = "Dogfoodingで実際に使ってみました"
        keywords = [
            p["keywords"] for p in PERSPECTIVES if p["id"] == "implementation_verification"
        ][0]
        assert check_perspective(content, keywords) is True

    def test_detects_dousa_kakunin_keyword(self):
        """Should detect 動作確認 keyword."""
        content = "動作確認を実施しました"
        keywords = [
            p["keywords"] for p in PERSPECTIVES if p["id"] == "implementation_verification"
        ][0]
        assert check_perspective(content, keywords) is True

    def test_detects_seijokei_kakunin_keyword(self):
        """Should detect 正常系確認 keyword."""
        content = "正常系のシナリオを確認しました"
        keywords = [
            p["keywords"] for p in PERSPECTIVES if p["id"] == "implementation_verification"
        ][0]
        assert check_perspective(content, keywords) is True

    def test_detects_ijoukei_kakunin_keyword(self):
        """Should detect 異常系確認 keyword."""
        content = "異常系のエラー処理を確認しました"
        keywords = [
            p["keywords"] for p in PERSPECTIVES if p["id"] == "implementation_verification"
        ][0]
        assert check_perspective(content, keywords) is True

    def test_detects_jibun_de_tsukau_keyword(self):
        """Should detect 自分で使 keyword."""
        content = "自分で使ってみて問題ないことを確認"
        keywords = [
            p["keywords"] for p in PERSPECTIVES if p["id"] == "implementation_verification"
        ][0]
        assert check_perspective(content, keywords) is True

    def test_detects_jissai_test_keyword(self):
        """Should detect 実際テスト keyword."""
        content = "実際のデータでテストしました"
        keywords = [
            p["keywords"] for p in PERSPECTIVES if p["id"] == "implementation_verification"
        ][0]
        assert check_perspective(content, keywords) is True

    def test_detects_jitsudata_kakunin_keyword(self):
        """Should detect 実データ確認 keyword."""
        content = "実データで動作を確認しました"
        keywords = [
            p["keywords"] for p in PERSPECTIVES if p["id"] == "implementation_verification"
        ][0]
        assert check_perspective(content, keywords) is True

    def test_detects_dousa_kakunin_fuyou_keyword(self):
        """Should detect 動作確認不要 keyword for doc-only changes."""
        content = "ドキュメント変更のため動作確認不要"
        keywords = [
            p["keywords"] for p in PERSPECTIVES if p["id"] == "implementation_verification"
        ][0]
        assert check_perspective(content, keywords) is True

    def test_missing_when_no_verification_keywords(self):
        """Should be missing when no verification keywords present."""
        content = "コードを修正しました。PRをマージしました。"
        keywords = [
            p["keywords"] for p in PERSPECTIVES if p["id"] == "implementation_verification"
        ][0]
        assert check_perspective(content, keywords) is False
