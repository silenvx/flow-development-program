#!/usr/bin/env python3
"""Unit tests for lesson-issue-check.py

This hook:
- Detects if reflection was performed (五省, 振り返り keywords)
- Checks if lessons found are associated with Issue numbers
- Blocks session end if lessons are not issued
"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for common module import
parent_dir = str(Path(__file__).parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Dynamic import for hyphenated module name
HOOK_PATH = Path(__file__).parent.parent / "lesson-issue-check.py"
_spec = importlib.util.spec_from_file_location("lesson_issue_check", HOOK_PATH)
lesson_issue_check = importlib.util.module_from_spec(_spec)
sys.modules["lesson_issue_check"] = lesson_issue_check
_spec.loader.exec_module(lesson_issue_check)

has_reflection_keywords = lesson_issue_check.has_reflection_keywords
find_lesson_mentions = lesson_issue_check.find_lesson_mentions
find_lesson_tags = lesson_issue_check.find_lesson_tags
has_issue_reference = lesson_issue_check.has_issue_reference
has_resolved_issue_reference = lesson_issue_check.has_resolved_issue_reference
has_negation_context = lesson_issue_check.has_negation_context
has_meta_discussion_context = lesson_issue_check.has_meta_discussion_context
has_work_context = lesson_issue_check.has_work_context
should_skip_lesson_check_globally = lesson_issue_check.should_skip_lesson_check_globally
get_lessons_without_issues = lesson_issue_check.get_lessons_without_issues
get_tags_without_issues = lesson_issue_check.get_tags_without_issues
strip_code_blocks = lesson_issue_check.strip_code_blocks
strip_summary_section = lesson_issue_check.strip_summary_section
strip_system_reminders = lesson_issue_check.strip_system_reminders
strip_read_tool_output = lesson_issue_check.strip_read_tool_output


class TestStripCodeBlocks:
    """Tests for strip_code_blocks function (Issue #2106)."""

    def test_removes_single_code_block(self):
        """Should remove a single code block."""
        text = "Before ```python\nprint('hello')\n``` After"
        result = strip_code_blocks(text)
        assert "```" not in result
        assert "print" not in result
        assert "Before" in result
        assert "After" in result

    def test_removes_multiple_code_blocks(self):
        """Should remove multiple code blocks."""
        text = "Start ```code1``` middle ```code2``` end"
        result = strip_code_blocks(text)
        assert "code1" not in result
        assert "code2" not in result
        assert "Start" in result
        assert "middle" in result
        assert "end" in result

    def test_preserves_text_without_code_blocks(self):
        """Should preserve text without code blocks."""
        text = "This is regular text without any code blocks."
        result = strip_code_blocks(text)
        assert result == text

    def test_removes_code_block_with_lesson_keywords(self):
        """Should remove code blocks containing lesson keywords (Issue #2106 fix)."""
        text = """Regular text.
```python
def test_lesson_keywords():
    # Test for 教訓, 反省点, 改善点 keywords
    pass
```
More regular text."""
        result = strip_code_blocks(text)
        assert "Regular text" in result
        assert "More regular text" in result
        # These keywords should be removed with the code block
        assert "test_lesson_keywords" not in result
        # Issue #2106: Verify lesson keywords themselves are removed
        assert "教訓" not in result
        assert "反省点" not in result
        assert "改善点" not in result

    def test_handles_nested_backticks(self):
        """Should handle code blocks with backticks in content."""
        text = "Before ```echo `date` ``` After"
        result = strip_code_blocks(text)
        assert "Before" in result
        assert "After" in result
        assert "echo" not in result

    def test_unclosed_code_block_preserved(self):
        """Unclosed code blocks are not matched (non-greedy pattern), text preserved.

        Note: Non-greedy matching (*?) means unclosed blocks don't match.
        This is intentional - preserving content is safer than over-removal.
        If removal were too aggressive, legitimate lesson discussions could be lost.
        """
        text = "Text before ```unclosed code block with 教訓"
        result = strip_code_blocks(text)
        # Unclosed block is not matched, so text including keyword remains
        assert "教訓" in result
        assert "unclosed" in result


class TestStripSystemReminders:
    """Tests for strip_system_reminders function (Issue #2139)."""

    def test_removes_single_system_reminder(self):
        """Should remove a single system-reminder tag."""
        text = """Before reminder.

<system-reminder>
Contents of AGENTS.md:
- 教訓: Important lesson
- 改善点: Improvement point
</system-reminder>

After reminder."""
        result = strip_system_reminders(text)
        assert "Before reminder" in result
        assert "After reminder" in result
        assert "<system-reminder>" not in result
        assert "教訓: Important lesson" not in result
        assert "改善点: Improvement point" not in result

    def test_removes_multiple_system_reminders(self):
        """Should remove multiple system-reminder tags."""
        text = """Start.

<system-reminder>
First reminder with 教訓
</system-reminder>

Middle content.

<system-reminder>
Second reminder with 反省点
</system-reminder>

End."""
        result = strip_system_reminders(text)
        assert "Start." in result
        assert "Middle content." in result
        assert "End." in result
        assert "First reminder" not in result
        assert "Second reminder" not in result

    def test_preserves_text_without_system_reminders(self):
        """Should preserve text without system-reminder tags."""
        text = "This is regular text with 教訓 and 改善点 keywords."
        result = strip_system_reminders(text)
        assert result == text
        assert "教訓" in result
        assert "改善点" in result

    def test_removes_agents_md_content(self):
        """Should remove AGENTS.md content in system-reminder (Issue #2139 root cause)."""
        text = """五省を実施しました。

<system-reminder>
Contents of /project/AGENTS.md:

# AGENTS.md

## Skills

| Skill | 用途 |
| ----- | ---- |
| `reflection` | 五省、なぜなぜ分析、振り返り |
| `coding-standards` | コーディング規約、テスト、Lint |

## Review guidelines

### 優先度定義

**教訓**: 重要な学び
**改善点**: 改善すべき事項
</system-reminder>

実際の教訓として Issue #1234 を作成しました。"""
        result = strip_system_reminders(text)
        assert "五省を実施しました" in result
        assert "Issue #1234" in result
        assert "AGENTS.md" not in result
        assert "優先度定義" not in result
        # The 教訓 in AGENTS.md should be removed, but the one in actual content remains
        assert "実際の教訓" in result

    def test_unclosed_system_reminder_preserved(self):
        """Unclosed system-reminder tags should not be matched.

        This is intentional - preserving content is safer than over-removal.
        If removal were too aggressive, legitimate lesson discussions could be lost.
        """
        text = "Text before <system-reminder>unclosed reminder with 教訓"
        result = strip_system_reminders(text)
        # Unclosed tag is not matched, so text including keyword remains
        assert "教訓" in result
        assert "unclosed reminder" in result


class TestStripReadToolOutput:
    """Tests for strip_read_tool_output function (Issue #2155)."""

    def test_removes_read_tool_output(self):
        """Should remove Read tool output containing lesson keywords."""
        text = """Before read output.

Result of calling the Read tool: "     1→#!/usr/bin/env python3
     2→# 教訓: This keyword in file content should be removed
     3→# 改善点: Another keyword
     4→def main():
     5→    pass
"

After read output."""
        result = strip_read_tool_output(text)
        assert "Before read output" in result
        assert "After read output" in result
        assert "Result of calling the Read tool" not in result
        assert "教訓: This keyword" not in result
        assert "改善点: Another keyword" not in result

    def test_removes_multiple_read_outputs(self):
        """Should remove multiple Read tool outputs."""
        text = """First section.

Result of calling the Read tool: "教訓 in file 1"

Middle content.

Result of calling the Read tool: "反省点 in file 2"

End section."""
        result = strip_read_tool_output(text)
        assert "First section" in result
        assert "Middle content" in result
        assert "End section" in result
        assert "教訓 in file 1" not in result
        assert "反省点 in file 2" not in result

    def test_preserves_text_without_read_output(self):
        """Should preserve text without Read tool output."""
        text = "This is regular text with 教訓 and 改善点 keywords."
        result = strip_read_tool_output(text)
        assert result == text
        assert "教訓" in result
        assert "改善点" in result

    def test_removes_hook_source_code(self):
        """Should remove hook source code displayed by Read tool (Issue #2155 root cause)."""
        # Simulate actual Read tool output format with hook source code
        text = """五省を実施しました。

Result of calling the Read tool: "     1→#!/usr/bin/env python3
     2→# Design reviewed: 2026-01-01
     3→# - 責務: 振り返り時に発見した教訓がIssue化されているか確認
     4→# - 重複なし: reflection-completion-checkは振り返り実施を確認
     5→
     6→LESSON_KEYWORDS = [
     7→    r\"教訓\",
     8→    r\"反省点\",
     9→    r\"改善点\",
    10→]
"

実際の教訓として Issue #1234 を作成しました。"""
        result = strip_read_tool_output(text)
        assert "五省を実施しました" in result
        assert "Issue #1234" in result
        # Hook source code should be removed
        assert "LESSON_KEYWORDS" not in result
        assert "振り返り時に発見した教訓" not in result
        # The 教訓 in actual content remains
        assert "実際の教訓" in result

    def test_handles_read_output_before_tag(self):
        """Should handle Read output that ends before a tag (like <system-reminder>)."""
        text = """五省を実施しました。

Result of calling the Read tool: "教訓 keyword in file"
<system-reminder>
Other content
</system-reminder>

問題なし。"""
        result = strip_read_tool_output(text)
        assert "五省を実施しました" in result
        assert "教訓 keyword in file" not in result
        # system-reminder remains (handled by different strip function)
        assert "<system-reminder>" in result

    def test_handles_read_output_at_end(self):
        """Should handle Read output at the end of text."""
        text = '''五省を実施しました。

Result of calling the Read tool: "教訓 in final file content"'''
        result = strip_read_tool_output(text)
        assert "五省を実施しました" in result
        assert "教訓 in final file content" not in result

    def test_handles_single_newline_after_output(self):
        """Should handle Read output followed by single newline (Codex review fix).

        Common transcript format where next message immediately follows
        with just one line break, not a blank line.
        """
        text = """五省を実施しました。

Result of calling the Read tool: "教訓 in file"
Next message immediately after single newline."""
        result = strip_read_tool_output(text)
        assert "五省を実施しました" in result
        assert "Next message" in result
        assert "教訓 in file" not in result

    def test_unclosed_read_output_preserved(self):
        """Unclosed Read tool output should not be matched.

        This is intentional - preserving content is safer than over-removal.
        """
        text = 'Text before Result of calling the Read tool: "unclosed with 教訓'
        result = strip_read_tool_output(text)
        # Unclosed output is not matched, so text including keyword remains
        assert "教訓" in result
        assert "unclosed" in result


class TestReadToolOutputIntegration:
    """Integration tests for Read tool output stripping in main flow (Issue #2155)."""

    def test_read_tool_lesson_keywords_not_flagged(self):
        """Lesson keywords inside Read tool output should not be flagged.

        Issue #2155: Verify the fix works in the integration flow.
        """
        text = """五省を実施しました。

すべて順調でした。問題なし。

Result of calling the Read tool: "# LESSON_KEYWORDS = [\"教訓\", \"反省点\", \"改善点\"]"

以上で振り返り完了。"""
        # Strip Read tool output first (as main() does)
        stripped = strip_read_tool_output(text)
        # Then check for lessons without issues
        unissued = get_lessons_without_issues(stripped)
        # Should find no unissued lessons (keywords were in Read tool output)
        assert unissued == []

    def test_mixed_keywords_inside_and_outside_read_output(self):
        """Keywords outside Read output should be flagged, inside should not.

        Tests the scenario where real lessons exist outside Read tool output
        while file content also contains lesson keywords.
        """
        text = """五省を実施しました。

Result of calling the Read tool: "教訓: ファイル内のキーワード
改善点: ドキュメント説明"

今回の新しい教訓として、テストを先に書くべきでした。"""
        stripped = strip_read_tool_output(text)
        unissued = get_lessons_without_issues(stripped)
        # The "教訓" outside Read output without Issue ref should be flagged
        # The keywords inside Read output should NOT be flagged
        assert "教訓" in unissued
        # Should only have one unissued lesson (the one outside Read output)
        assert len(unissued) == 1

    def test_combined_read_output_and_other_stripping(self):
        """Read output, system-reminder, and code blocks should all be stripped.

        Tests the full stripping flow as used in main().
        """
        text = """五省を実施しました。

<system-reminder>
Contents of AGENTS.md with 教訓 keyword
</system-reminder>

Result of calling the Read tool: "反省点 in file content"

```python
# Test code with 改善点 keyword
```

実際の問題点として Issue #1234 を作成しました。"""
        # Apply all stripping functions as main() does
        stripped = strip_system_reminders(text)
        stripped = strip_code_blocks(stripped)
        stripped = strip_read_tool_output(stripped)
        unissued = get_lessons_without_issues(stripped)
        # Should find no unissued lessons:
        # - 教訓 was in system-reminder (stripped)
        # - 反省点 was in Read output (stripped)
        # - 改善点 was in code block (stripped)
        # - 問題点 has Issue reference
        assert unissued == []


class TestStripSummarySection:
    """Tests for strip_summary_section function (Issue #2120)."""

    def test_removes_summary_section(self):
        """Should remove session continuation summary section."""
        text = """Before summary.

This session is being continued from a previous conversation that ran out of context. The conversation is summarized below:

Summary:
- 教訓: テストを書くべき
- 反省点: レビューが遅れた
- 問題点: ドキュメント不足

Please continue the conversation from where we left it off.

After summary with real content."""
        result = strip_summary_section(text)
        assert "Before summary" in result
        assert "After summary" in result
        assert "This session is being continued" not in result
        # Keywords in summary should be removed
        assert "教訓: テストを書くべき" not in result
        assert "反省点: レビューが遅れた" not in result

    def test_preserves_text_without_summary(self):
        """Should preserve text without summary section."""
        text = "This is regular text with 教訓 and 反省点 keywords."
        result = strip_summary_section(text)
        assert result == text
        assert "教訓" in result
        assert "反省点" in result

    def test_removes_summary_with_lesson_keywords(self):
        """Should remove summary containing lesson keywords (Issue #2120 fix)."""
        text = """五省を実施しました。

This session is being continued from a previous conversation that ran out of context. The conversation is summarized below:

Analysis:
1. 教訓として記録
2. 反省点を洗い出し
3. 改善点の提案
4. 問題点の特定
5. 次回への引き継ぎ

Please continue the conversation from where we left it off without asking further questions.

実際の作業結果: 問題なし"""
        result = strip_summary_section(text)
        assert "五省を実施しました" in result
        assert "実際の作業結果" in result
        # All keywords in summary section should be removed
        assert "次回への引き継ぎ" not in result
        # Summary marker should be removed
        assert "This session is being continued" not in result

    def test_handles_multiple_lines_in_summary(self):
        """Should handle multi-line summary content."""
        text = """Start.

This session is being continued from a previous conversation that ran out of context. The conversation is summarized below:

Key Technical Concepts:
- 教訓: Important lesson
- 反省点: Reflection point

Problem Solving:
- 問題点: Issue found
- 改善点: Improvement suggested

Please continue the conversation from where we left it off.

End."""
        result = strip_summary_section(text)
        assert "Start." in result
        assert "End." in result
        assert "Key Technical Concepts" not in result
        assert "Problem Solving" not in result


class TestSummarySectionIntegration:
    """Integration tests for summary section stripping in main flow (Issue #2120)."""

    def test_summary_lesson_keywords_not_flagged(self):
        """Lesson keywords inside summary section should not be flagged.

        Issue #2120: Verify the fix works in the integration flow.
        """
        # Text with reflection AND lesson keywords inside summary only
        text = """五省を実施しました。

This session is being continued from a previous conversation that ran out of context. The conversation is summarized below:

Summary:
- 教訓: Previous session lesson
- 反省点: Previous reflection
- 改善点: Previous improvement

Please continue the conversation from where we left it off.

現在のセッションでは問題なし。"""
        # Strip summary section first (as main() does)
        stripped = strip_summary_section(text)
        # Then check for lessons without issues
        unissued = get_lessons_without_issues(stripped)
        # Should find no unissued lessons (keywords were in summary)
        assert unissued == []

    def test_mixed_keywords_inside_and_outside_summary(self):
        """Keywords outside summary should be flagged, inside should not.

        Tests the scenario where real lessons exist outside summary section
        while summary also contains lesson keywords from previous session.
        """
        text = """五省を実施しました。

This session is being continued from a previous conversation that ran out of context. The conversation is summarized below:

Previous session summary:
- 教訓: Old lesson from previous session
- 反省点: Old reflection

Please continue the conversation from where we left it off.

今回の新しい教訓として、テストを先に書くべきでした。"""
        stripped = strip_summary_section(text)
        unissued = get_lessons_without_issues(stripped)
        # The "教訓" outside summary without Issue ref should be flagged
        # The keywords inside summary should NOT be flagged
        assert "教訓" in unissued
        # Should only have one unissued lesson (the one outside summary)
        assert len(unissued) == 1


class TestCodeBlockIntegration:
    """Integration tests for code block stripping in main flow (Issue #2106)."""

    def test_code_block_lesson_keywords_not_flagged(self):
        """Lesson keywords inside code blocks should not be flagged.

        Issue #2106: Verify the fix works in the integration flow.
        """
        # Text with reflection AND lesson keywords inside code block only
        text = """五省を実施しました。

すべて順調でした。問題なし。

```python
# テストコード例
LESSON_KEYWORDS = ["教訓", "反省点", "改善点"]
```

以上で振り返り完了。"""
        # Strip code blocks first (as main() does)
        stripped = strip_code_blocks(text)
        # Then check for lessons without issues
        unissued = get_lessons_without_issues(stripped)
        # Should find no unissued lessons (keywords were in code block)
        assert unissued == []

    def test_mixed_keywords_inside_and_outside_code_block(self):
        """Keywords outside code blocks should be flagged, inside should not.

        Tests the scenario where real lessons exist outside code blocks
        while code examples also contain lesson keywords.
        """
        text = """五省を実施しました。

今回の教訓として、テストを先に書くべきでした。

```python
# テストコード例
LESSON_KEYWORDS = ["教訓", "反省点", "改善点"]
```

以上で振り返り完了。"""
        stripped = strip_code_blocks(text)
        unissued = get_lessons_without_issues(stripped)
        # The "教訓" outside code block without Issue ref should be flagged
        # The keywords inside code block (教訓, 反省点, 改善点) should NOT be flagged
        assert "教訓" in unissued
        # Verify code block keywords were stripped (not in unissued list as separate items)
        # Note: 反省点, 改善点 from code block should have been removed
        assert len(unissued) == 1  # Only the one outside code block


class TestSystemReminderIntegration:
    """Integration tests for system-reminder stripping in main flow (Issue #2139)."""

    def test_system_reminder_lesson_keywords_not_flagged(self):
        """Lesson keywords inside system-reminder should not be flagged.

        Issue #2139: Verify system reminders with AGENTS.md content are stripped.
        """
        text = """五省を実施しました。

すべて順調でした。問題なし。

<system-reminder>
Contents of AGENTS.md:

## Review guidelines

| 優先度 | 対象 | 例 |
| ------ | ---- | -- |
| **P1** | マージ前に修正 | バグ、設計問題、**教訓**からの学び |
| **P2** | 改善推奨 | **改善点**、**反省点** |
</system-reminder>

以上で振り返り完了。"""
        # Strip system reminders first (as main() does)
        stripped = strip_system_reminders(text)
        # Then check for lessons without issues
        unissued = get_lessons_without_issues(stripped)
        # Should find no unissued lessons (keywords were in system-reminder)
        assert unissued == []

    def test_mixed_keywords_inside_and_outside_system_reminder(self):
        """Keywords outside system-reminder should be flagged, inside should not.

        Tests the scenario where real lessons exist outside system-reminder
        while AGENTS.md content also contains lesson keywords.
        """
        text = """五省を実施しました。

<system-reminder>
Contents of AGENTS.md:
- 教訓: ドキュメント内のキーワード
- 改善点: ガイドライン説明
</system-reminder>

今回の新しい教訓として、テストを先に書くべきでした。"""
        stripped = strip_system_reminders(text)
        unissued = get_lessons_without_issues(stripped)
        # The "教訓" outside system-reminder without Issue ref should be flagged
        # The keywords inside system-reminder should NOT be flagged
        assert "教訓" in unissued
        # Should only have one unissued lesson (the one outside system-reminder)
        assert len(unissued) == 1

    def test_combined_system_reminder_and_code_block_stripping(self):
        """Both system-reminder and code blocks should be stripped.

        Tests the full stripping flow as used in main().
        """
        text = """五省を実施しました。

<system-reminder>
Contents of AGENTS.md with 教訓 keyword
</system-reminder>

```python
# Test code with 改善点 keyword
```

実際の反省点として Issue #1234 を作成しました。"""
        # Apply both stripping functions as main() does
        stripped = strip_system_reminders(text)
        stripped = strip_code_blocks(stripped)
        unissued = get_lessons_without_issues(stripped)
        # Should find no unissued lessons:
        # - 教訓 was in system-reminder (stripped)
        # - 改善点 was in code block (stripped)
        # - 反省点 has Issue reference
        assert unissued == []


class TestHasReflectionKeywords:
    """Tests for has_reflection_keywords function."""

    def test_detects_gosei(self):
        """Should detect 五省 keyword."""
        assert has_reflection_keywords("今回の五省を行います")
        assert has_reflection_keywords("五省")

    def test_detects_furikaeri(self):
        """Should detect 振り返り keyword."""
        assert has_reflection_keywords("振り返りを実施します")
        assert has_reflection_keywords("振り返り")

    def test_detects_gosei_items(self):
        """Should detect individual 五省 items."""
        assert has_reflection_keywords("要件理解に悖るなかりしか")
        assert has_reflection_keywords("実装に恥づるなかりしか")
        assert has_reflection_keywords("検証に欠くるなかりしか")
        assert has_reflection_keywords("対応に憾みなかりしか")
        assert has_reflection_keywords("効率に欠くるなかりしか")

    def test_no_reflection_keywords(self):
        """Should return False when no reflection keywords present."""
        assert not has_reflection_keywords("Just regular code discussion")
        assert not has_reflection_keywords("Implemented the feature")
        assert not has_reflection_keywords("")

    def test_excludes_reflection_in_work_context(self):
        """Should exclude reflection keywords used in work/discussion context (Issue #2130)."""
        # These should NOT be detected as actual reflection
        assert not has_reflection_keywords("振り返りプロンプトの修正")
        assert not has_reflection_keywords("振り返りテンプレートの誤検知")
        assert not has_reflection_keywords("Issue #2119: 振り返りプロンプト誤検知修正")
        assert not has_reflection_keywords("振り返りフックの修正をIssue化")
        assert not has_reflection_keywords("「振り返り」というキーワードが検出された")

    def test_detects_genuine_reflection_with_work_context(self):
        """Should detect genuine reflection even if work context exists elsewhere (Issue #2130)."""
        # Mixed context: work reference + genuine reflection
        text = "Issue #2119: 振り返りプロンプト修正完了\n\n今回の振り返りを実施します"
        assert has_reflection_keywords(text)

        # Genuine reflection with 五省
        text = "振り返りテンプレートを使って五省を行います"
        assert has_reflection_keywords(text)


class TestFindLessonTags:
    """Tests for find_lesson_tags function (Issue #2311)."""

    def test_detects_lesson_tag(self):
        """Should detect [lesson] tag."""
        tags = find_lesson_tags("[lesson] テストを書くべきだった")
        assert len(tags) == 1
        assert "[lesson]" in tags[0][0].lower()

    def test_detects_lesson_tag_case_insensitive(self):
        """Should detect [lesson] tag case-insensitively."""
        tags = find_lesson_tags("[LESSON] テストを書くべきだった")
        assert len(tags) == 1
        tags = find_lesson_tags("[Lesson] テストを書くべきだった")
        assert len(tags) == 1

    def test_detects_multiple_tags(self):
        """Should detect multiple [lesson] tags."""
        text = "[lesson] 教訓A\n[lesson] 教訓B\n[lesson] 教訓C"
        tags = find_lesson_tags(text)
        assert len(tags) == 3

    def test_no_tags(self):
        """Should return empty list when no tags found."""
        tags = find_lesson_tags("Just regular text without tags")
        assert tags == []

    def test_extracts_context(self):
        """Should extract surrounding context."""
        text = "前のテキスト [lesson] 重要な教訓 後のテキスト"
        tags = find_lesson_tags(text)
        assert len(tags) == 1
        context = tags[0][1]
        assert "前のテキスト" in context
        assert "後のテキスト" in context


class TestGetTagsWithoutIssues:
    """Tests for get_tags_without_issues function (Issue #2311)."""

    def test_returns_tags_without_issues(self):
        """Should return tags that lack issue references."""
        text = "[lesson] 早めにIssue作成すべき"
        unissued = get_tags_without_issues(text)
        assert len(unissued) == 1

    def test_excludes_tags_with_issues(self):
        """Should exclude tags that have issue references nearby."""
        text = "[lesson] 早めにIssue作成すべき → Issue #2075"
        unissued = get_tags_without_issues(text)
        assert len(unissued) == 0

    def test_excludes_resolved_issue_reference(self):
        """Should exclude tags with resolved Issue reference."""
        text = "[lesson] この問題は Issue #2090 として仕組み化済み"
        unissued = get_tags_without_issues(text)
        assert len(unissued) == 0

    def test_excludes_meta_discussion_context(self):
        """Should exclude tags in meta-discussion context."""
        text = "これは誤検知です。[lesson] タグが検出されました。"
        unissued = get_tags_without_issues(text)
        assert len(unissued) == 0

    def test_excludes_negation_context(self):
        """Should exclude tags in negation context (Copilot review fix)."""
        text = "[lesson] 改善点は発見されませんでした"
        unissued = get_tags_without_issues(text)
        assert len(unissued) == 0

    def test_excludes_work_context(self):
        """Should exclude tags in work context (Copilot review fix)."""
        text = "[lesson] 改善点を修正しました"
        unissued = get_tags_without_issues(text)
        assert len(unissued) == 0

    def test_mixed_tags(self):
        """Should correctly handle mix of issued and unissued tags."""
        padding = "x" * 250
        text = f"""
        [lesson] 教訓A → Issue #1234
        {padding}
        [lesson] 教訓B (まだIssue化していない)
        {padding}
        [lesson] 教訓C → Issue #5678
        """
        unissued = get_tags_without_issues(text)
        # Only the middle tag should be unissued
        assert len(unissued) == 1


class TestFindLessonMentions:
    """Tests for find_lesson_mentions function."""

    def test_detects_kyoukun(self):
        """Should detect 教訓 keyword."""
        lessons = find_lesson_mentions("今回の教訓として記録します")
        assert len(lessons) == 1
        assert lessons[0][0] == "教訓"

    def test_detects_hanseiten(self):
        """Should detect 反省点 keyword."""
        lessons = find_lesson_mentions("反省点: 早めにIssue作成すべき")
        assert len(lessons) == 1
        assert lessons[0][0] == "反省点"

    def test_detects_kaizenten(self):
        """Should detect 改善点 keyword."""
        lessons = find_lesson_mentions("改善点を洗い出します")
        assert len(lessons) == 1
        assert lessons[0][0] == "改善点"

    def test_detects_multiple_lessons(self):
        """Should detect multiple lesson keywords."""
        text = "反省点: A\n教訓: B\n改善点: C"
        lessons = find_lesson_mentions(text)
        keywords = [lesson[0] for lesson in lessons]
        assert "反省点" in keywords
        assert "教訓" in keywords
        assert "改善点" in keywords

    def test_no_lessons(self):
        """Should return empty list when no lessons found."""
        lessons = find_lesson_mentions("Just regular code")
        assert lessons == []


class TestHasIssueReference:
    """Tests for has_issue_reference function."""

    def test_detects_hash_number(self):
        """Should detect #1234 format."""
        assert has_issue_reference("#1234")
        assert has_issue_reference("対応済み (#2075)")

    def test_detects_issue_hash_number(self):
        """Should detect Issue #1234 format."""
        assert has_issue_reference("Issue #1234")
        assert has_issue_reference("Issue#1234")

    def test_case_insensitive(self):
        """Should be case insensitive."""
        assert has_issue_reference("issue #1234")
        assert has_issue_reference("ISSUE #1234")

    def test_no_issue_reference(self):
        """Should return False when no issue reference found."""
        assert not has_issue_reference("no reference here")
        assert not has_issue_reference("")


class TestHasNegationContext:
    """Tests for has_negation_context function (Issue #2094)."""

    def test_detects_hakken_sarenakatta(self):
        """Should detect 発見されませんでした/なかった patterns."""
        assert has_negation_context("改善点は発見されませんでした")
        assert has_negation_context("問題点は発見されませんでした。")
        assert has_negation_context("改善点は発見されなかった")

    def test_detects_mitsukaranakatta(self):
        """Should detect 見つかりませんでした/なかった patterns."""
        assert has_negation_context("教訓は見つかりませんでした")
        assert has_negation_context("教訓は見つからなかった")

    def test_detects_arimasen_deshita(self):
        """Should detect ありませんでした pattern."""
        assert has_negation_context("反省点はありませんでした")

    def test_detects_toku_ni_nashi(self):
        """Should detect 特になし pattern."""
        assert has_negation_context("改善点: 特になし")

    def test_detects_nashi_at_end(self):
        """Should detect なし at end of text or followed by whitespace."""
        assert has_negation_context("問題点: なし")
        assert has_negation_context("問題点: なし\n他の内容が続く")
        assert has_negation_context("問題点: なし 次の項目")

    def test_detects_toku_ni_arimasen(self):
        """Should detect 特にありません pattern."""
        assert has_negation_context("教訓は特にありません")

    def test_detects_hakken_shiteinai(self):
        """Should detect 発見していません/ない patterns (Issue #2114)."""
        assert has_negation_context("教訓は発見していません")
        assert has_negation_context("改善点は発見していない")
        assert has_negation_context("問題点は発見しておりません")

    def test_detects_miatari(self):
        """Should detect 見当たり patterns (Issue #2114)."""
        assert has_negation_context("反省点は見当たりません")
        assert has_negation_context("教訓は見当たりませんでした")
        assert has_negation_context("問題点は見当たらない")
        assert has_negation_context("改善点は見当たらなかった")

    def test_detects_kakunin_sarenakatta(self):
        """Should detect 確認されませんでした/なかった patterns (Issue #2114)."""
        assert has_negation_context("問題点は確認されませんでした")
        assert has_negation_context("反省点は確認されなかった")

    def test_detects_mitomerarenakatta(self):
        """Should detect 認められませんでした/なかった patterns (Issue #2114)."""
        assert has_negation_context("改善点は認められませんでした")
        assert has_negation_context("教訓は認められなかった")

    def test_no_negation(self):
        """Should return False when no negation pattern found."""
        assert not has_negation_context("改善点: テストを追加すべき")
        assert not has_negation_context("反省点として記録")
        assert not has_negation_context("")

    def test_detects_nashi_with_punctuation(self):
        """Should detect なし followed by punctuation (Issue #2149)."""
        assert has_negation_context("問題点: なし。")
        assert has_negation_context("改善点: なし、")
        assert has_negation_context("反省点: なし．")
        assert has_negation_context("教訓: なし，")
        assert has_negation_context("問題点: なし.")
        assert has_negation_context("改善点: なし,")

    def test_detects_mondai_nashi(self):
        """Should detect 問題なし pattern for 五省 responses (Issue #2149)."""
        # With word boundary (whitespace/punctuation/EOL)
        assert has_negation_context("- 問題なし ")
        assert has_negation_context("- 問題なし。")
        assert has_negation_context("問題なし。特に懸念事項はありません")
        assert has_negation_context("1. 要件理解に悖るなかりしか\n- 問題なし\n次の項目")
        # End of string
        assert has_negation_context("問題なし")

    def test_detects_mondai_nai_forms(self):
        """Should detect 問題ない/ありません patterns (Issue #2149)."""
        # With word boundary (whitespace/punctuation/EOL)
        assert has_negation_context("問題ない。")
        assert has_negation_context("問題ありません。")
        assert has_negation_context("問題はありません ")
        # End of string
        assert has_negation_context("問題ない")
        assert has_negation_context("問題ありません")
        assert has_negation_context("問題はありません")

    def test_mondai_nashi_excludes_question_forms(self):
        """Should NOT detect 問題ないか? (question form) as negation (Issue #2149)."""
        # Question forms should NOT be matched
        assert not has_negation_context("問題ないか?")
        assert not has_negation_context("問題ないか確認してください")
        # Also ensure 問題点 keyword is not excluded
        assert not has_negation_context("問題点: ドキュメントが不足している")


class TestHasMetaDiscussionContext:
    """Tests for has_meta_discussion_context function (Issue #2111)."""

    def test_detects_gokechi(self):
        """Should detect 誤検知 pattern."""
        assert has_meta_discussion_context("これは誤検知です")
        assert has_meta_discussion_context("フックの誤検知が発生")

    def test_detects_hook_trigger(self):
        """Should detect フックが...トリガー pattern."""
        assert has_meta_discussion_context("フックがトリガーされている")
        assert has_meta_discussion_context("フックが繰り返しトリガー")

    def test_detects_hook_detection(self):
        """Should detect フックの...検知 pattern."""
        assert has_meta_discussion_context("フックの検知が誤っている")

    def test_detects_kaiwa_rireki(self):
        """Should detect 会話履歴 pattern."""
        assert has_meta_discussion_context("会話履歴に含まれるキーワード")

    def test_detects_korewa_dewanaku(self):
        """Should detect これは...教訓/反省点/改善点/問題点...ではなく pattern."""
        # Now limited to lesson keywords to avoid matching general negation
        assert has_meta_discussion_context("これは新しい教訓ではなく既存の説明")
        assert has_meta_discussion_context("これは反省点ではなく提案")
        assert has_meta_discussion_context("これは改善点ではなく仕様")
        # Should NOT match general negation like "これはバグではなく"
        assert not has_meta_discussion_context("これはバグではなく仕様です")

    def test_detects_atarashii_dewanai(self):
        """Should detect 新しい教訓/反省点/改善点/問題点...ではない pattern."""
        # Now limited to lesson keywords
        assert has_meta_discussion_context("新しい教訓ではない")
        assert has_meta_discussion_context("新しい反省点ではない")
        assert has_meta_discussion_context("新しい改善点ではないです")
        # Should NOT match general phrases like "新しい機能ではない"
        assert not has_meta_discussion_context("新しい機能ではない")

    def test_detects_pattern_across_newlines(self):
        """Should detect patterns across newlines thanks to re.DOTALL."""
        # フックが\nトリガー should still match フックが.*?トリガー
        assert has_meta_discussion_context("フックが\nトリガーされています")
        # これは\n新しい教訓\nではなく should match the lesson keyword pattern
        assert has_meta_discussion_context("これは\n教訓ではなく\n既存の説明です")
        assert has_meta_discussion_context("キーワードが\n検出されました")

    def test_detects_reflection_template_instructions(self):
        """Should detect reflection template section headers and instruction phrases (Issue #2119).

        e.g., "## 4. 改善点の洗い出し" in execute.md L140
        Also catches similar instruction patterns that discuss lesson identification.
        """
        # Section header pattern from execute.md L140
        assert has_meta_discussion_context("改善点の洗い出し")
        assert has_meta_discussion_context("問題点の洗い出し")
        # Instruction-style phrases
        assert has_meta_discussion_context("教訓や反省点を発見したら")
        assert has_meta_discussion_context("改善点を洗い出します")

    def test_does_not_exclude_real_lessons(self):
        """Should NOT match actual lessons that happen to contain similar words (Issue #2119).

        Important: These are real lessons with actionable content, not template instructions.
        They should not be excluded as meta-discussion.
        """
        # Real lesson: describes a concrete reflection, not an instruction
        assert not has_meta_discussion_context("反省点: 改善点を早めに特定すべきだった")
        assert not has_meta_discussion_context("教訓: 問題点を事前に確認すること")
        # Real lesson: uses lesson keywords but in actual reflection context
        assert not has_meta_discussion_context("今回の反省点として記録します")

    def test_no_meta_discussion(self):
        """Should return False when no meta-discussion pattern found."""
        assert not has_meta_discussion_context("反省点: 早めにIssue作成すべき")
        assert not has_meta_discussion_context("教訓がありました")
        assert not has_meta_discussion_context("")

    def test_detects_lesson_keyword_pattern(self):
        """Should detect 教訓キーワード pattern (Issue #2125)."""
        assert has_meta_discussion_context("教訓キーワードを検出する")
        assert has_meta_discussion_context("反省点キーワードを追加")
        assert has_meta_discussion_context("改善点キーワードのリスト")
        assert has_meta_discussion_context("問題点キーワードをチェック")

    def test_detects_hakken_sareta_keyword(self):
        """Should detect 発見されたキーワード pattern (Issue #2125)."""
        assert has_meta_discussion_context("発見されたキーワード: 教訓")
        assert has_meta_discussion_context("発見されたキーワードは改善点")

    def test_detects_hook_block_message_marker(self):
        """Should detect universal hook block message marker (Issue #2137).

        This marker is added to all hook block messages for robust
        self-feedback loop prevention.
        """
        # Standard marker format
        assert has_meta_discussion_context("<!-- HOOK_BLOCK_MESSAGE:lesson-issue-check -->")
        assert has_meta_discussion_context("<!-- HOOK_BLOCK_MESSAGE:some-other-hook -->")
        # Should NOT match invalid marker formats
        assert not has_meta_discussion_context("<!-- HOOK_BLOCK_MESSAGE -->")  # Missing hook name
        assert not has_meta_discussion_context("HOOK_BLOCK_MESSAGE:test")  # Missing comment tags

    def test_detects_hook_block_message(self):
        """Should detect hook's own block message patterns (Issue #2129).

        The hook's block message contains lesson keywords, which would cause
        an infinite self-feedback loop when the message is added to transcript.
        """
        # Hook name pattern
        assert has_meta_discussion_context("[lesson-issue-check] メッセージ")
        # Block message header
        assert has_meta_discussion_context("振り返りで発見した教訓がIssue化されていません")
        assert has_meta_discussion_context("振り返りで発見した反省点がIssue化されていません")
        # Instruction pattern (specific to hook message)
        # Issue #2133: Pattern now matches exact hook message "教訓や反省点を発見したら"
        assert has_meta_discussion_context("教訓や反省点を発見したら、必ずIssue化してください")
        assert has_meta_discussion_context("教訓や反省点を発見したら")
        # Should NOT match unrelated user instructions (avoids false negatives for real lessons)
        assert not has_meta_discussion_context("改善点: バリデーション不足。Issue化してください")

    def test_detects_full_hook_block_message(self):
        """Should detect keywords in full hook block message context (Issue #2129, #2137)."""
        # Updated to include the new marker from Issue #2137
        full_message = """<!-- HOOK_BLOCK_MESSAGE:lesson-issue-check -->
[lesson-issue-check] **振り返りで発見した教訓がIssue化されていません**

発見されたキーワード: 反省点、教訓、問題点 他2件

教訓や反省点を発見したら、必ずIssue化してください:
```bash
gh issue create --title "fix: [教訓の内容]" --label "bug,P2" --body "[詳細]"
```"""
        # The marker at the beginning should be detected
        assert has_meta_discussion_context(full_message)

    def test_markdown_section_headers(self):
        """Should detect markdown section headers with lesson keywords (Issue #2135)."""
        # Numbered section headers from execute.md
        assert has_meta_discussion_context("## 4. 改善点の洗い出し")
        assert has_meta_discussion_context("## 5. 教訓のIssue化")
        assert has_meta_discussion_context("##  1.  反省点を記録")
        assert has_meta_discussion_context("## 10. 問題点の整理")
        # Should NOT match non-numbered section headers (may contain real lessons)
        assert not has_meta_discussion_context("## 改善点")
        assert not has_meta_discussion_context("### 教訓まとめ")

    def test_markdown_table_header_cells(self):
        """Should detect markdown table header cells (bold) with lesson keywords (Issue #2135)."""
        # Bold header cells in tables - these are likely template headers
        assert has_meta_discussion_context("| **改善点** | 内容 |")
        assert has_meta_discussion_context("| **教訓** | 詳細 |")
        assert has_meta_discussion_context("|  **反省点**  | メモ |")
        # Non-bold cells may contain real lessons, should NOT be excluded
        # (per Codex review - avoiding false negatives)
        assert not has_meta_discussion_context("| 改善点 | API遅延への恒久対策 |")
        assert not has_meta_discussion_context("| 教訓: 早めに確認すべき |")

    # Note: test_hook_keyword_list_output removed (Issue #2135 Copilot review)
    # "発見されたキーワード: ..." is already covered by test_detects_hakken_sareta_keyword
    # which tests L82 pattern r"発見されたキーワード"

    def test_detects_session_continuation_context(self):
        """Should detect session continuation context patterns (Issue #2141).

        When a session continues from a summarized conversation, keywords from
        the original reflection may appear but the connection to created Issues
        is lost. These patterns detect session continuation markers.
        """
        # English session continuation markers
        assert has_meta_discussion_context("conversation is summarized below")
        assert has_meta_discussion_context("session is being continued")
        assert has_meta_discussion_context("summarized below:")
        assert has_meta_discussion_context("Previous session")
        # Japanese session continuation markers
        assert has_meta_discussion_context("セッション継続中")
        assert has_meta_discussion_context("会話が要約されています")
        # Reflection execution context
        assert has_meta_discussion_context("/reflect を実行しました")
        assert has_meta_discussion_context("振り返りを実行した結果")
        # Completed Issue references
        assert has_meta_discussion_context("Issue #2137 が完了しました")
        assert has_meta_discussion_context("Issue #123 はマージ済み")
        assert has_meta_discussion_context("Issue #456 を実装済み")
        assert has_meta_discussion_context("完了した Issue #789")
        assert has_meta_discussion_context("マージ済みの Issue #101")


class TestHasWorkContext:
    """Tests for has_work_context function (Issue #2113)."""

    def test_detects_wo_shusei(self):
        """Should detect を修正 pattern."""
        assert has_work_context("改善点を修正しました")
        assert has_work_context("問題点を修正")

    def test_detects_ni_taiou(self):
        """Should detect に対応 pattern."""
        assert has_work_context("反省点に対応しました")
        assert has_work_context("改善点に対応")

    def test_detects_taiou_zumi(self):
        """Should detect 対応済 pattern."""
        assert has_work_context("問題点対応済み")
        assert has_work_context("教訓対応済")

    def test_detects_no_review(self):
        """Should detect のレビュー pattern."""
        assert has_work_context("反省点のレビュー")
        assert has_work_context("改善点のレビューコメント")

    def test_detects_review_comment(self):
        """Should detect レビューコメント pattern."""
        assert has_work_context("レビューコメント対応")
        assert has_work_context("レビューコメントに返信")

    def test_detects_comment_taiou(self):
        """Should detect コメント対応 pattern."""
        assert has_work_context("コメント対応しました")

    def test_detects_wo_jissou(self):
        """Should detect を実装 pattern."""
        assert has_work_context("教訓を実装しました")

    def test_detects_toshite_touroku(self):
        """Should detect として登録 pattern."""
        assert has_work_context("反省点として登録")

    def test_detects_issue_ka(self):
        """Should detect をIssue化 pattern (more specific to avoid false positives)."""
        assert has_work_context("教訓をIssue化")
        assert has_work_context("問題点をIssue化しました")
        assert has_work_context("反省点をIssue化する")

    def test_excludes_issue_ka_negation(self):
        """Should NOT detect をIssue化していない (negation means lesson is unissued)."""
        # These are NOT work context - they indicate lesson is actually unissued
        # Plain negation
        assert not has_work_context("教訓をIssue化していない")
        assert not has_work_context("改善点をIssue化しない")
        assert not has_work_context("問題点をIssue化してない")
        # Polite negation forms
        assert not has_work_context("教訓をIssue化していません")
        assert not has_work_context("改善点をIssue化しておりません")
        # Past tense negation forms
        assert not has_work_context("教訓をIssue化していませんでした")
        assert not has_work_context("改善点をIssue化しておりませんでした")
        # Casual negation forms
        assert not has_work_context("問題点をIssue化してません")
        assert not has_work_context("反省点をIssue化してませんでした")
        # Classical/formal negation forms (しておらず/しておらん)
        assert not has_work_context("教訓をIssue化しておらず")
        assert not has_work_context("改善点をIssue化しておらん")

    def test_detects_gokenchi(self):
        """Should detect 誤検知 pattern."""
        assert has_work_context("改善点の誤検知")
        assert has_work_context("誤検知の対応")

    def test_no_work_context(self):
        """Should return False when no work context pattern found."""
        assert not has_work_context("教訓: 早めにテストを書くべき")
        assert not has_work_context("反省点として、設計レビューが不足していた")
        assert not has_work_context("")


class TestShouldSkipLessonCheckGlobally:
    """Tests for should_skip_lesson_check_globally function (Issue #2144).

    This function performs a GLOBAL check (entire transcript, not context window)
    to detect session continuation markers and skip the lesson check entirely.
    """

    def test_detects_session_continuation_marker(self):
        """Should detect English session continuation markers."""
        assert should_skip_lesson_check_globally(
            "This session is being continued from a previous conversation"
        )
        assert should_skip_lesson_check_globally("The conversation is summarized below")
        assert should_skip_lesson_check_globally("summarized below:")

    def test_detects_hook_block_message_marker(self):
        """Should detect hook block message marker."""
        assert should_skip_lesson_check_globally("<!-- HOOK_BLOCK_MESSAGE:lesson-issue-check -->")
        assert should_skip_lesson_check_globally(
            "Some text <!-- HOOK_BLOCK_MESSAGE:some-hook --> more text"
        )

    def test_case_insensitive(self):
        """Should match markers case-insensitively."""
        assert should_skip_lesson_check_globally("Session is being continued")
        assert should_skip_lesson_check_globally("CONVERSATION IS SUMMARIZED")

    def test_returns_false_for_normal_text(self):
        """Should return False for normal text without markers."""
        assert not should_skip_lesson_check_globally("振り返りを実行しました")
        assert not should_skip_lesson_check_globally("教訓: テストを書くべき")
        assert not should_skip_lesson_check_globally("")

    def test_marker_anywhere_in_text(self):
        """Should detect marker even if far from lesson keywords (global check)."""
        # This is the key difference from context-based detection
        long_text = (
            "session is being continued from a previous conversation. "
            + "x" * 500  # 500 chars of padding
            + "教訓として記録します"
        )
        assert should_skip_lesson_check_globally(long_text)


class TestHasResolvedIssueReference:
    """Tests for has_resolved_issue_reference function (Issue #2100)."""

    def test_detects_issue_with_sumi(self):
        """Should detect Issue reference with 済み keyword."""
        assert has_resolved_issue_reference("Issue #2090 として仕組み化済み")
        assert has_resolved_issue_reference("#1234 対応済み")
        assert has_resolved_issue_reference("実装済み (#2090)")

    def test_detects_issue_with_kanryo(self):
        """Should detect Issue reference with 完了 keyword."""
        assert has_resolved_issue_reference("Issue #2090 で対応完了")
        assert has_resolved_issue_reference("完了 (#1234)")

    def test_detects_issue_with_merge(self):
        """Should detect Issue reference with マージ keyword."""
        assert has_resolved_issue_reference("Issue #123 をマージしました")
        assert has_resolved_issue_reference("#456 マージ済み")

    def test_detects_issue_with_kaiketu(self):
        """Should detect Issue reference with 解決 keyword."""
        assert has_resolved_issue_reference("Issue #789 で解決")
        assert has_resolved_issue_reference("#100 解決済み")

    def test_detects_issue_with_close(self):
        """Should detect Issue reference with クローズ keyword."""
        assert has_resolved_issue_reference("Issue #111 をクローズ")
        assert has_resolved_issue_reference("#222 クローズ済み")

    def test_false_when_issue_only(self):
        """Should return False when only Issue reference exists."""
        assert not has_resolved_issue_reference("Issue #456 を作成しました")
        assert not has_resolved_issue_reference("#789 を参照")

    def test_false_when_resolved_only(self):
        """Should return False when only resolved keyword exists."""
        assert not has_resolved_issue_reference("作業完了しました")
        assert not has_resolved_issue_reference("実装済みです")

    def test_false_when_neither(self):
        """Should return False when neither exists."""
        assert not has_resolved_issue_reference("教訓がある")
        assert not has_resolved_issue_reference("")

    def test_proximity_required(self):
        """Should require Issue and resolved keyword to be within 30 chars."""
        # Close enough (within 30 chars)
        assert has_resolved_issue_reference("Issue #123 が完了")
        # Too far apart (more than 30 chars between)
        long_text = "Issue #123 " + "x" * 50 + " 完了"
        assert not has_resolved_issue_reference(long_text)

    def test_unrelated_keywords_not_matched(self):
        """Should not match when Issue and resolved keyword are unrelated (Issue #2100 fix)."""
        # This was the original bug: "完了" and "Issue #123" in same context but unrelated
        # Now with proximity check, this should return False
        # Example: "教訓Aは完了。次の教訓Bは Issue #123 で対応中。"
        # Make sure they're more than 30 chars apart
        text_separated = "作業A完了" + "x" * 35 + "Issue #123 作業中"
        assert not has_resolved_issue_reference(text_separated)


class TestGetLessonsWithoutIssues:
    """Tests for get_lessons_without_issues function."""

    def test_returns_lessons_without_issues(self):
        """Should return lessons that lack issue references."""
        text = "反省点: 早めにIssue作成すべき"
        unissued = get_lessons_without_issues(text)
        assert "反省点" in unissued

    def test_excludes_lessons_with_issues(self):
        """Should exclude lessons that have issue references nearby."""
        text = "反省点: 早めにIssue作成すべき (#2075 で対応)"
        unissued = get_lessons_without_issues(text)
        assert "反省点" not in unissued

    def test_mixed_lessons(self):
        """Should correctly handle mix of issued and unissued lessons."""
        # Use longer text with proper separation to avoid context overlap
        # The context window is 200 chars before and after each match
        padding = "x" * 250  # Ensure lessons are isolated from each other
        text = f"""
        反省点: A (#1234 で対応済み)
        {padding}
        教訓: B (まだIssue化していない)
        {padding}
        改善点: C (Issue #5678 作成済み)
        """
        unissued = get_lessons_without_issues(text)
        # 反省点 has #1234, 改善点 has #5678, only 教訓 is unissued
        assert "反省点" not in unissued
        assert "改善点" not in unissued
        assert "教訓" in unissued

    def test_excludes_negation_context(self):
        """Should exclude lessons in negation context (Issue #2094)."""
        text = "改善点は発見されませんでした"
        unissued = get_lessons_without_issues(text)
        # 改善点 is in negation context, should be excluded
        assert "改善点" not in unissued

    def test_excludes_toku_ni_nashi(self):
        """Should exclude lessons with 特になし (Issue #2094)."""
        text = "反省点: 特になし"
        unissued = get_lessons_without_issues(text)
        assert "反省点" not in unissued

    def test_mixed_negation_and_real_lessons(self):
        """Should correctly handle mix of negation and real lessons (Issue #2094)."""
        padding = "x" * 250
        text = f"""
        改善点は発見されませんでした
        {padding}
        教訓: 実際にあった問題点です
        {padding}
        反省点: 特になし
        """
        unissued = get_lessons_without_issues(text)
        # 改善点 is negated, 反省点 is 特になし, only 教訓 is real
        assert "改善点" not in unissued
        assert "反省点" not in unissued
        assert "教訓" in unissued

    def test_excludes_resolved_issue_reference(self):
        """Should exclude lessons with resolved Issue reference (Issue #2100)."""
        text = "教訓: この問題は Issue #2090 として仕組み化済み"
        unissued = get_lessons_without_issues(text)
        # 教訓 has resolved issue reference, should be excluded
        assert "教訓" not in unissued

    def test_mixed_resolved_and_unresolved_lessons(self):
        """Should correctly handle mix of resolved and unresolved lessons (Issue #2100)."""
        padding = "x" * 250
        text = f"""
        教訓: この問題は Issue #2090 として完了
        {padding}
        反省点: まだIssue化していない新しい問題
        {padding}
        改善点: #1234 マージ済み
        """
        unissued = get_lessons_without_issues(text)
        # 教訓 and 改善点 have resolved issue references
        # 反省点 is a new unissued lesson
        assert "教訓" not in unissued
        assert "改善点" not in unissued
        assert "反省点" in unissued

    def test_excludes_meta_discussion_context(self):
        """Should exclude lessons in meta-discussion context (Issue #2111)."""
        text = "これは誤検知です。教訓として扱わないでください。"
        unissued = get_lessons_without_issues(text)
        # 教訓 is in meta-discussion context (誤検知), should be excluded
        assert "教訓" not in unissued

    def test_mixed_meta_discussion_and_real_lessons(self):
        """Should correctly handle mix of meta-discussion and real lessons (Issue #2111)."""
        padding = "x" * 250
        text = f"""
        これは誤検知です。教訓ではありません。
        {padding}
        反省点: 実際にあった問題点です
        {padding}
        フックがトリガーしている改善点について
        """
        unissued = get_lessons_without_issues(text)
        # 教訓 is in meta context (誤検知), 改善点 is in meta context (フックがトリガー)
        # 反省点 is a real lesson without Issue
        assert "教訓" not in unissued
        assert "改善点" not in unissued
        assert "反省点" in unissued

    def test_excludes_work_context(self):
        """Should exclude lessons in work context (Issue #2113)."""
        text = "改善点を修正しました"
        unissued = get_lessons_without_issues(text)
        # 改善点 is in work context, should be excluded
        assert "改善点" not in unissued

    def test_excludes_review_context(self):
        """Should exclude lessons in review context (Issue #2113)."""
        text = "反省点のレビューコメントに対応しました"
        unissued = get_lessons_without_issues(text)
        assert "反省点" not in unissued

    def test_excludes_keyword_discussion(self):
        """Should exclude when discussing keyword detection itself (Issue #2113)."""
        text = "教訓キーワードの誤検知を修正しました"
        unissued = get_lessons_without_issues(text)
        assert "教訓" not in unissued

    def test_mixed_work_context_and_real_lessons(self):
        """Should correctly handle mix of work context and real lessons (Issue #2113)."""
        padding = "x" * 250
        text = f"""
        改善点を修正しました
        {padding}
        教訓: 実際の反省として、テストを先に書くべきだった
        {padding}
        問題点のレビューコメント対応
        """
        unissued = get_lessons_without_issues(text)
        # 改善点 and 問題点 are in work context
        # 教訓 is a real lesson without issue reference
        assert "改善点" not in unissued
        assert "問題点" not in unissued
        assert "教訓" in unissued


class TestWorkContextIntegration:
    """Integration tests for work context in main flow (Issue #2113)."""

    def test_work_context_lesson_keywords_not_flagged(self, capsys):
        """Lesson keywords in work context should not block session."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_file = Path(tmpdir) / "transcript.txt"
            transcript_file.write_text(
                "五省を実施しました。\n"
                "改善点を修正しました。\n"
                "問題点のレビューコメントに対応しました。\n"
                "反省点に対応済みです。"
            )

            input_data = {
                "session_id": "test-session",
                "transcript_path": str(transcript_file),
            }
            with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                lesson_issue_check.main()
                captured = capsys.readouterr()
                result = json.loads(captured.out)
                # Should approve because all lessons are in work context
                assert result["decision"] == "approve"

    def test_mixed_work_context_and_keyword_only_warns(self, capsys):
        """Keywords without issue refs should warn (not block) with work context present (Issue #2311)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            padding = "x" * 250
            transcript_file = Path(tmpdir) / "transcript.txt"
            transcript_file.write_text(
                f"五省を実施しました。\n"
                f"改善点を修正しました。\n"
                f"{padding}\n"
                f"教訓: テストを先に書くべきだった（まだIssue化していない）"
            )

            input_data = {
                "session_id": "test-session",
                "transcript_path": str(transcript_file),
            }
            with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                lesson_issue_check.main()
                captured = capsys.readouterr()
                result = json.loads(captured.out)
                # Issue #2311: Keywords only trigger warning, not block
                assert result["decision"] == "approve"
                assert "[lesson-issue-check] 警告" in captured.err

    def test_mixed_work_context_and_tag_blocks(self, capsys):
        """Tags without issue refs should block even with work context present (Issue #2311)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            padding = "x" * 250
            transcript_file = Path(tmpdir) / "transcript.txt"
            transcript_file.write_text(
                f"五省を実施しました。\n"
                f"改善点を修正しました。\n"
                f"{padding}\n"
                f"[lesson] テストを先に書くべきだった（まだIssue化していない）"
            )

            input_data = {
                "session_id": "test-session",
                "transcript_path": str(transcript_file),
            }
            with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                lesson_issue_check.main()
                captured = capsys.readouterr()
                result = json.loads(captured.out)
                # Should block because [lesson] tag is present without issue ref
                assert result["decision"] == "block"
                assert "[lesson]" in result["reason"]


class TestMainIntegration:
    """Integration tests for main function."""

    def test_no_transcript_allows_stop(self, capsys):
        """Should allow stop when no transcript provided."""
        input_data = {"session_id": "test-session"}
        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            lesson_issue_check.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            assert result["decision"] == "approve"

    def test_no_reflection_allows_stop(self, capsys):
        """Should allow stop when no reflection keywords in transcript."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_file = Path(tmpdir) / "transcript.txt"
            transcript_file.write_text("Just regular code discussion")

            input_data = {
                "session_id": "test-session",
                "transcript_path": str(transcript_file),
            }
            with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                lesson_issue_check.main()
                captured = capsys.readouterr()
                result = json.loads(captured.out)
                assert result["decision"] == "approve"

    def test_reflection_with_issued_lessons_allows_stop(self, capsys):
        """Should allow stop when all lessons have issue references."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_file = Path(tmpdir) / "transcript.txt"
            transcript_file.write_text(
                "五省を実行しました。\n"
                "反省点: 早めにIssue作成すべき (#2075 で対応)\n"
                "教訓: 問題発見時にすぐIssue化することが重要 (Issue #2076 作成済み)"
            )

            input_data = {
                "session_id": "test-session",
                "transcript_path": str(transcript_file),
            }
            with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                lesson_issue_check.main()
                captured = capsys.readouterr()
                result = json.loads(captured.out)
                assert result["decision"] == "approve"

    def test_reflection_with_unissued_keyword_only_warns(self, capsys):
        """Should warn (not block) when only keywords exist without issue references (Issue #2311).

        Keywords without [lesson] tags now only produce warnings, not blocks.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_file = Path(tmpdir) / "transcript.txt"
            transcript_file.write_text(
                "五省を実行しました。\n"
                "反省点: 早めにIssue作成すべき\n"
                "教訓: 問題発見時にすぐIssue化することが重要"
            )

            input_data = {
                "session_id": "test-session",
                "transcript_path": str(transcript_file),
            }
            with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                lesson_issue_check.main()
                captured = capsys.readouterr()
                result = json.loads(captured.out)
                # Issue #2311: Keywords only trigger warning, not block
                assert result["decision"] == "approve"
                # Warning should be in stderr
                assert "[lesson-issue-check] 警告" in captured.err

    def test_reflection_with_unissued_tag_blocks(self, capsys):
        """Should block when [lesson] tags exist without issue references (Issue #2311).

        Tags are high-precision markers that should block when no Issue reference.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_file = Path(tmpdir) / "transcript.txt"
            transcript_file.write_text(
                "五省を実行しました。\n"
                "[lesson] 早めにIssue作成すべき\n"
                "[lesson] 問題発見時にすぐIssue化することが重要"
            )

            input_data = {
                "session_id": "test-session",
                "transcript_path": str(transcript_file),
            }
            with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                lesson_issue_check.main()
                captured = capsys.readouterr()
                result = json.loads(captured.out)
                assert result["decision"] == "block"
                assert "Issue化されていません" in result["reason"]
                assert "[lesson]" in result["reason"]

    def test_reflection_with_issued_tag_allows_stop(self, capsys):
        """Should allow stop when [lesson] tags have issue references (Issue #2311)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_file = Path(tmpdir) / "transcript.txt"
            transcript_file.write_text(
                "五省を実行しました。\n"
                "[lesson] 早めにIssue作成すべき → Issue #2311\n"
                "[lesson] 問題発見時にすぐIssue化 → #2312"
            )

            input_data = {
                "session_id": "test-session",
                "transcript_path": str(transcript_file),
            }
            with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                lesson_issue_check.main()
                captured = capsys.readouterr()
                result = json.loads(captured.out)
                assert result["decision"] == "approve"

    def test_error_handling_allows_stop(self, capsys):
        """Should allow stop on errors to not block sessions."""
        input_data = {"session_id": "test-session", "transcript_path": "/nonexistent/file"}
        with patch("sys.stdin.read", return_value=json.dumps(input_data)):
            lesson_issue_check.main()
            captured = capsys.readouterr()
            result = json.loads(captured.out)
            # Should not block on errors
            assert result["decision"] == "approve"

    def test_reflection_with_negation_allows_stop(self, capsys):
        """Should allow stop when lessons are in negation context (Issue #2094)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_file = Path(tmpdir) / "transcript.txt"
            transcript_file.write_text(
                "五省を実行しました。\n"
                "改善点は発見されませんでした。\n"
                "反省点: 特になし\n"
                "問題点は見つかりませんでした。"
            )

            input_data = {
                "session_id": "test-session",
                "transcript_path": str(transcript_file),
            }
            with patch("sys.stdin.read", return_value=json.dumps(input_data)):
                lesson_issue_check.main()
                captured = capsys.readouterr()
                result = json.loads(captured.out)
                # Should approve because all lessons are in negation context
                assert result["decision"] == "approve"
