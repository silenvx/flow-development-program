"""Tests for merit-demerit-check.py hook."""

from __future__ import annotations

import importlib.util
import json
import sys
from io import StringIO
from pathlib import Path

import pytest

# Load the hook module dynamically (it has hyphens in the name)
HOOK_PATH = Path(__file__).parent.parent / "merit-demerit-check.py"


@pytest.fixture
def hook_module():
    """Load the hook module."""
    spec = importlib.util.spec_from_file_location("merit_demerit_check", str(HOOK_PATH))
    module = importlib.util.module_from_spec(spec)
    # Add hooks directory to path for common imports
    hooks_dir = str(Path(__file__).parent.parent)
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    spec.loader.exec_module(module)
    return module


class TestHasMeritContext:
    """Tests for has_merit_context function."""

    def test_japanese_merit(self, hook_module):
        """日本語の「メリット」を検出"""
        assert hook_module.has_merit_context("この選択肢のメリットは高速処理")

    def test_japanese_riten(self, hook_module):
        """日本語の「利点」を検出"""
        assert hook_module.has_merit_context("利点: シンプルな実装")

    def test_japanese_chosho(self, hook_module):
        """日本語の「長所」を検出"""
        assert hook_module.has_merit_context("長所として保守性が高い")

    def test_english_merit(self, hook_module):
        """英語の 'merit' を検出"""
        assert hook_module.has_merit_context("The main merit is simplicity")

    def test_english_advantage(self, hook_module):
        """英語の 'advantage' を検出"""
        assert hook_module.has_merit_context("This has the advantage of speed")

    def test_english_pros(self, hook_module):
        """英語の 'pros' を検出"""
        assert hook_module.has_merit_context("Pros: easy to maintain")

    def test_no_merit_context(self, hook_module):
        """メリットコンテキストなし"""
        assert not hook_module.has_merit_context("Just a plain description")

    def test_no_false_positive_prospective(self, hook_module):
        """'pros' が 'prospective' にマッチしない"""
        assert not hook_module.has_merit_context("A prospective solution")

    def test_no_false_positive_prose(self, hook_module):
        """'pros' が 'prose' にマッチしない"""
        assert not hook_module.has_merit_context("Written in prose format")

    def test_no_false_positive_prosper(self, hook_module):
        """'pros' が 'prosper' にマッチしない"""
        assert not hook_module.has_merit_context("The business will prosper")


class TestMatchAnyWordBoundary:
    """Tests for _match_any_word_boundary helper function."""

    def test_empty_keywords_returns_false(self, hook_module):
        """空のキーワードリストはFalseを返す"""
        assert not hook_module._match_any_word_boundary([], "any text")

    def test_single_keyword_match(self, hook_module):
        """単一キーワードのマッチ"""
        assert hook_module._match_any_word_boundary(["pros"], "Pros: easy")

    def test_multiple_keywords_first_match(self, hook_module):
        """複数キーワードの最初がマッチ"""
        assert hook_module._match_any_word_boundary(["pros", "merit"], "Pros here")

    def test_multiple_keywords_second_match(self, hook_module):
        """複数キーワードの2番目がマッチ"""
        assert hook_module._match_any_word_boundary(["pros", "merit"], "merit here")

    def test_case_insensitive(self, hook_module):
        """大文字小文字を区別しない"""
        assert hook_module._match_any_word_boundary(["pros"], "PROS")
        assert hook_module._match_any_word_boundary(["PROS"], "pros")

    def test_word_boundary_prevents_partial_match(self, hook_module):
        """単語境界により部分マッチを防ぐ"""
        assert not hook_module._match_any_word_boundary(["pros"], "prospective")
        assert not hook_module._match_any_word_boundary(["cons"], "consider")

    def test_special_characters_escaped(self, hook_module):
        """正規表現特殊文字がエスケープされる"""
        # Without escaping, "pros." would be a regex pattern matching "pros" + any char
        # With escaping, it only matches literal "pros."
        assert not hook_module._match_any_word_boundary(["pros."], "prose")
        # The function handles special chars without crashing
        assert not hook_module._match_any_word_boundary(["(test)"], "test")


class TestHasDemeritContext:
    """Tests for has_demerit_context function."""

    def test_japanese_demerit(self, hook_module):
        """日本語の「デメリット」を検出"""
        assert hook_module.has_demerit_context("デメリットは複雑さ")

    def test_japanese_ketten(self, hook_module):
        """日本語の「欠点」を検出"""
        assert hook_module.has_demerit_context("欠点: 学習コストが高い")

    def test_japanese_risk(self, hook_module):
        """日本語の「リスク」を検出"""
        assert hook_module.has_demerit_context("リスクとして互換性問題がある")

    def test_english_demerit(self, hook_module):
        """英語の 'demerit' を検出"""
        assert hook_module.has_demerit_context("The demerit is complexity")

    def test_english_disadvantage(self, hook_module):
        """英語の 'disadvantage' を検出"""
        assert hook_module.has_demerit_context("One disadvantage is slower startup")

    def test_english_cons(self, hook_module):
        """英語の 'cons' を検出"""
        assert hook_module.has_demerit_context("Cons: requires more memory")

    def test_no_demerit_context(self, hook_module):
        """デメリットコンテキストなし"""
        assert not hook_module.has_demerit_context("Just a plain description")

    def test_no_false_positive_consider(self, hook_module):
        """'cons' が 'consider' にマッチしない"""
        assert not hook_module.has_demerit_context("Please consider this option")

    def test_no_false_positive_console(self, hook_module):
        """'cons' が 'console' にマッチしない"""
        assert not hook_module.has_demerit_context("Open the console window")

    def test_no_false_positive_construct(self, hook_module):
        """'cons' が 'construct' にマッチしない"""
        assert not hook_module.has_demerit_context("We need to construct a solution")


class TestHasCostContext:
    """Tests for has_cost_context function."""

    def test_japanese_cost(self, hook_module):
        """日本語の「コスト」を検出"""
        assert hook_module.has_cost_context("実装コストが低い")

    def test_japanese_kousu(self, hook_module):
        """日本語の「工数」を検出"""
        assert hook_module.has_cost_context("工数は約2日")

    def test_japanese_fukuzatsu_specific(self, hook_module):
        """日本語の「複雑になる」を検出（具体的パターン）"""
        assert hook_module.has_cost_context("実装が複雑になる")

    def test_japanese_fukuzatsu_sei(self, hook_module):
        """日本語の「複雑性」を検出"""
        assert hook_module.has_cost_context("複雑性が高い")

    def test_japanese_kouseiga_fukuzatsu(self, hook_module):
        """日本語の「構成が複雑」を検出"""
        assert hook_module.has_cost_context("構成が複雑になります")

    def test_japanese_jissouga_fukuzatsu(self, hook_module):
        """日本語の「実装が複雑」を検出"""
        assert hook_module.has_cost_context("実装が複雑です")

    def test_no_false_positive_fukuzatsu_general(self, hook_module):
        """単独の「複雑」は汎用的すぎるためマッチしない"""
        # "複雑な問題を解決" のような一般的な文脈ではマッチしない
        assert not hook_module.has_cost_context("複雑な問題を解決できる")

    def test_no_false_positive_fukuzatsu_in_sentence(self, hook_module):
        """「複雑」が文中にあっても特定パターン以外はマッチしない"""
        # "複雑な" は汎用的すぎるためマッチしない
        assert not hook_module.has_cost_context("この機能は複雑な処理を行う")

    def test_english_cost(self, hook_module):
        """英語の 'cost' を検出"""
        assert hook_module.has_cost_context("Low implementation cost")

    def test_english_complexity(self, hook_module):
        """英語の 'complexity' を検出"""
        assert hook_module.has_cost_context("Adds complexity to the system")

    def test_english_overhead(self, hook_module):
        """英語の 'overhead' を検出"""
        assert hook_module.has_cost_context("Minimal runtime overhead")

    def test_no_cost_context(self, hook_module):
        """コストコンテキストなし"""
        assert not hook_module.has_cost_context("Just a plain description")


class TestAnalyzeOptions:
    """Tests for analyze_options function."""

    def test_full_coverage(self, hook_module):
        """すべての観点がカバーされている"""
        options = [
            {"label": "Option A", "description": "メリット: 高速。デメリット: 複雑。コスト: 低い"},
        ]
        result = hook_module.analyze_options(options)
        assert result["has_merit"]
        assert result["has_demerit"]
        assert result["has_cost"]
        assert len(result["options_without_context"]) == 0

    def test_partial_coverage(self, hook_module):
        """一部の観点のみカバー"""
        options = [
            {"label": "Option A", "description": "利点: シンプル"},
            {"label": "Option B", "description": "欠点: 遅い"},
        ]
        result = hook_module.analyze_options(options)
        assert result["has_merit"]
        assert result["has_demerit"]
        assert not result["has_cost"]

    def test_no_coverage(self, hook_module):
        """どの観点もカバーされていない"""
        options = [
            {"label": "Option A", "description": "First choice"},
            {"label": "Option B", "description": "Second choice"},
        ]
        result = hook_module.analyze_options(options)
        assert not result["has_merit"]
        assert not result["has_demerit"]
        assert not result["has_cost"]
        assert len(result["options_without_context"]) == 2

    def test_mixed_languages(self, hook_module):
        """日英混在"""
        options = [
            {"label": "Option A", "description": "メリット: fast"},
            {"label": "Option B", "description": "Cons: 複雑"},
        ]
        result = hook_module.analyze_options(options)
        assert result["has_merit"]
        assert result["has_demerit"]


class TestFormatBlockMessage:
    """Tests for format_block_message function."""

    def test_all_missing(self, hook_module):
        """すべて不足"""
        analysis = {
            "has_merit": False,
            "has_demerit": False,
            "has_cost": False,
            "options_without_context": ["Option A"],
        }
        message = hook_module.format_block_message(analysis, "Which option to choose?")
        assert "メリット/利点" in message
        assert "デメリット/欠点" in message
        assert "コスト/工数" in message
        assert "Option A" in message
        assert "🚫" in message  # Block indicator

    def test_partial_missing(self, hook_module):
        """一部不足"""
        analysis = {
            "has_merit": True,
            "has_demerit": False,
            "has_cost": True,
            "options_without_context": [],
        }
        message = hook_module.format_block_message(analysis, "Which option to choose?")
        # Check that the "missing" section only contains demerit
        # The message always contains 【必須】section with all keywords, so we check "不足している観点"
        missing_line = [line for line in message.split("\n") if "不足している観点" in line][0]
        assert "デメリット/欠点" in missing_line
        # Merit and cost should NOT be in the missing line
        assert "メリット/利点" not in missing_line
        assert "コスト/工数" not in missing_line


class TestMainFunction:
    """Tests for main function."""

    def test_non_ask_user_question_approved(self, hook_module, monkeypatch, capsys):
        """AskUserQuestion以外は承認"""
        input_data = json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
            }
        )
        monkeypatch.setattr("sys.stdin", StringIO(input_data))

        hook_module.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"

    def test_empty_questions_approved(self, hook_module, monkeypatch, capsys):
        """空のquestionsは承認"""
        input_data = json.dumps(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {"questions": []},
            }
        )
        monkeypatch.setattr("sys.stdin", StringIO(input_data))

        hook_module.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"

    def test_single_option_approved(self, hook_module, monkeypatch, capsys):
        """選択肢1つは承認（チェック対象外）"""
        input_data = json.dumps(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "Continue?",
                            "options": [{"label": "Yes", "description": "Proceed"}],
                        }
                    ]
                },
            }
        )
        monkeypatch.setattr("sys.stdin", StringIO(input_data))

        hook_module.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"
        # No warning in stderr
        assert "merit-demerit-check" not in captured.err

    def test_good_options_approved_quietly(self, hook_module, monkeypatch, capsys):
        """十分な説明がある選択肢は静かに承認"""
        input_data = json.dumps(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "Which approach?",
                            "options": [
                                {
                                    "label": "Option A",
                                    "description": "メリット: 高速。デメリット: メモリ使用量が多い",
                                },
                                {
                                    "label": "Option B",
                                    "description": "利点: 省メモリ。欠点: 遅い",
                                },
                            ],
                        }
                    ]
                },
            }
        )
        monkeypatch.setattr("sys.stdin", StringIO(input_data))

        hook_module.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"
        # No warning
        assert "⚠️" not in captured.err


class TestFactCheckSkip:
    """Tests for Issue #2305: fact-check tag skip functionality."""

    def test_is_fact_check_question_with_english_tag(self, hook_module):
        """[fact-check]タグで事実確認と判定"""
        assert hook_module.is_fact_check_question("[fact-check] Which command did you use?")
        assert hook_module.is_fact_check_question("Which command did you use? [fact-check]")

    def test_is_fact_check_question_with_japanese_tag(self, hook_module):
        """[事実確認]タグで事実確認と判定"""
        assert hook_module.is_fact_check_question("[事実確認] どのコマンドを使いましたか？")
        assert hook_module.is_fact_check_question("どのコマンドを使いましたか？ [事実確認]")

    def test_is_fact_check_question_case_insensitive(self, hook_module):
        """タグは大文字小文字を区別しない"""
        assert hook_module.is_fact_check_question("[FACT-CHECK] Which command?")
        assert hook_module.is_fact_check_question("[Fact-Check] Which command?")

    def test_is_fact_check_question_without_tag(self, hook_module):
        """タグなしは事実確認と判定しない"""
        assert not hook_module.is_fact_check_question("Which approach should we take?")
        assert not hook_module.is_fact_check_question("どのアプローチを選びますか？")

    def test_is_fact_check_question_tag_in_middle_not_matched(self, hook_module):
        """中間位置のタグはマッチしない（セキュリティ対策）

        Issue #2305: Geminiレビューの指摘に基づき、タグは先頭/末尾のみ許可。
        中間位置のタグを許可すると、意図しないバイパスの原因になりうる。
        """
        # 中間位置のタグはマッチしない
        assert not hook_module.is_fact_check_question(
            "Please tell me [fact-check] which option is correct?"
        )
        assert not hook_module.is_fact_check_question(
            "どちらが正しいですか [事実確認] 教えてください"
        )

    def test_fact_check_question_approved_without_context(self, hook_module, monkeypatch, capsys):
        """[fact-check]タグ付きの質問はメリット・デメリットなしでも承認"""
        input_data = json.dumps(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "[fact-check] どのコマンドで起動しましたか？",
                            "options": [
                                {
                                    "label": "claude --fork-session",
                                    "description": "fork-sessionで起動",
                                },
                                {"label": "claude --resume", "description": "resumeで起動"},
                            ],
                        }
                    ]
                },
            }
        )
        monkeypatch.setattr("sys.stdin", StringIO(input_data))

        hook_module.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"

    def test_fact_check_japanese_tag_approved(self, hook_module, monkeypatch, capsys):
        """[事実確認]タグ付きの質問も承認"""
        input_data = json.dumps(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "[事実確認] このセッションの起動方法は？",
                            "options": [
                                {"label": "新規起動", "description": "新しいセッション"},
                                {"label": "再開", "description": "既存セッションの再開"},
                            ],
                        }
                    ]
                },
            }
        )
        monkeypatch.setattr("sys.stdin", StringIO(input_data))

        hook_module.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"

    def test_mixed_questions_fact_check_skipped(self, hook_module, monkeypatch, capsys):
        """複数質問で事実確認のみスキップ、意思決定はブロック"""
        input_data = json.dumps(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "[fact-check] どのOSを使用していますか？",
                            "options": [
                                {"label": "macOS", "description": "Apple"},
                                {"label": "Linux", "description": "Linux"},
                            ],
                        },
                        {
                            "question": "どの実装方法を選びますか？",
                            "options": [
                                {"label": "方法A", "description": "シンプル"},
                                {"label": "方法B", "description": "複雑"},
                            ],
                        },
                    ]
                },
            }
        )
        monkeypatch.setattr("sys.stdin", StringIO(input_data))

        hook_module.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        # Second question lacks context, so should block
        assert result["decision"] == "block"
        # But first question should not appear in block message
        assert "どのOSを使用していますか" not in result.get("reason", "")

    def test_poor_options_blocked(self, hook_module, monkeypatch, capsys):
        """説明不足の選択肢はブロック"""
        input_data = json.dumps(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "Which library should we use?",
                            "options": [
                                {"label": "Library A", "description": "Popular choice"},
                                {"label": "Library B", "description": "Alternative option"},
                            ],
                        }
                    ]
                },
            }
        )
        monkeypatch.setattr("sys.stdin", StringIO(input_data))

        hook_module.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        # Blocked (not approved)
        assert result["decision"] == "block"
        # Block message in reason field
        assert "🚫" in result.get("reason", "")
        assert "merit-demerit-check" in result.get("reason", "")

    def test_invalid_json_approved(self, hook_module, monkeypatch, capsys):
        """無効なJSONは承認"""
        monkeypatch.setattr("sys.stdin", StringIO("not json"))

        hook_module.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"


class TestIntegration:
    """Integration tests for realistic scenarios."""

    def test_typical_implementation_choice_blocked(self, hook_module, monkeypatch, capsys):
        """典型的な実装選択（ブロック）"""
        input_data = json.dumps(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "How should we implement authentication?",
                            "options": [
                                {"label": "JWT", "description": "Token-based auth"},
                                {"label": "Session", "description": "Cookie-based auth"},
                            ],
                        }
                    ]
                },
            }
        )
        monkeypatch.setattr("sys.stdin", StringIO(input_data))

        hook_module.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        # Should block due to missing context
        assert result["decision"] == "block"
        assert "🚫" in result.get("reason", "")

    def test_well_documented_choice(self, hook_module, monkeypatch, capsys):
        """よく文書化された選択（警告なし）"""
        input_data = json.dumps(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "question": "Which database to use?",
                            "options": [
                                {
                                    "label": "PostgreSQL",
                                    "description": "メリット: 高機能、ACID準拠。デメリット: 運用コストが高め",
                                },
                                {
                                    "label": "SQLite",
                                    "description": "利点: シンプル、工数が少ない。欠点: 並列書き込みに弱い",
                                },
                            ],
                        }
                    ]
                },
            }
        )
        monkeypatch.setattr("sys.stdin", StringIO(input_data))

        hook_module.main()

        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["decision"] == "approve"
        # No warning
        assert "⚠️" not in captured.err
