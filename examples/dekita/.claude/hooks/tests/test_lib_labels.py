"""Tests for lib/labels.py module.

Issue #1957: Tests for consolidated label extraction functions.
"""

import sys
from pathlib import Path

# Add hooks directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.labels import (
    DEFAULT_PRIORITY_LABELS,
    extract_body_from_command,
    extract_labels_from_command,
    extract_priority_from_labels,
    extract_title_from_command,
    has_priority_label,
    split_comma_separated_labels,
    suggest_labels_from_text,
)


class TestExtractLabelsFromCommand:
    """Tests for extract_labels_from_command function."""

    def test_single_label_long_form(self):
        """Single label with --label."""
        cmd = 'gh issue create --label "bug"'
        assert extract_labels_from_command(cmd) == ["bug"]

    def test_single_label_short_form(self):
        """Single label with -l."""
        cmd = 'gh issue create -l "enhancement"'
        assert extract_labels_from_command(cmd) == ["enhancement"]

    def test_label_equals_form(self):
        """Label with --label=value form."""
        cmd = 'gh issue create --label="bug"'
        assert extract_labels_from_command(cmd) == ["bug"]

    def test_short_label_equals_form(self):
        """-l=value form."""
        cmd = 'gh issue create -l="bug"'
        assert extract_labels_from_command(cmd) == ["bug"]

    def test_multiple_labels(self):
        """Multiple label options."""
        cmd = 'gh issue create --label "bug" --label "P1"'
        assert extract_labels_from_command(cmd) == ["bug", "P1"]

    def test_multiple_labels_mixed_forms(self):
        """Multiple labels with mixed option forms."""
        cmd = 'gh issue create --label bug -l P1 --label="enhancement"'
        assert extract_labels_from_command(cmd) == ["bug", "P1", "enhancement"]

    def test_comma_separated_labels(self):
        """Comma-separated labels in single option (not split)."""
        cmd = 'gh issue create --label "bug,P2"'
        # Raw extraction preserves comma-separated values
        assert extract_labels_from_command(cmd) == ["bug,P2"]

    def test_no_labels(self):
        """Command without labels."""
        cmd = 'gh issue create --title "test"'
        assert extract_labels_from_command(cmd) == []

    def test_labels_with_spaces(self):
        """Labels with spaces in quoted strings."""
        cmd = 'gh issue create --label "needs review"'
        assert extract_labels_from_command(cmd) == ["needs review"]

    def test_unquoted_label(self):
        """Unquoted label value."""
        cmd = "gh issue create --label bug"
        assert extract_labels_from_command(cmd) == ["bug"]

    def test_empty_command(self):
        """Empty command."""
        assert extract_labels_from_command("") == []

    def test_invalid_command_quote_mismatch(self):
        """Invalid command with unbalanced quotes returns empty list."""
        cmd = 'gh issue create --label "unclosed'
        assert extract_labels_from_command(cmd) == []


class TestSplitCommaSeparatedLabels:
    """Tests for split_comma_separated_labels function."""

    def test_single_label(self):
        """Single label without comma."""
        assert split_comma_separated_labels(["bug"]) == ["bug"]

    def test_comma_separated(self):
        """Comma-separated labels."""
        assert split_comma_separated_labels(["bug,P1"]) == ["bug", "P1"]

    def test_comma_with_spaces(self):
        """Comma-separated with spaces."""
        assert split_comma_separated_labels(["bug, P1"]) == ["bug", "P1"]

    def test_multiple_entries(self):
        """Multiple entries with comma-separated values."""
        assert split_comma_separated_labels(["bug,P1", "enhancement"]) == [
            "bug",
            "P1",
            "enhancement",
        ]

    def test_empty_list(self):
        """Empty list."""
        assert split_comma_separated_labels([]) == []

    def test_strips_whitespace(self):
        """Strips whitespace from labels."""
        assert split_comma_separated_labels(["  bug  , P1  "]) == ["bug", "P1"]

    def test_filters_empty_labels(self):
        """Filters out empty labels from split."""
        assert split_comma_separated_labels(["bug,,P1"]) == ["bug", "P1"]


class TestExtractPriorityFromLabels:
    """Tests for extract_priority_from_labels function."""

    def test_p0_label(self):
        """P0 should be detected."""
        assert extract_priority_from_labels(["P0"]) == "P0"

    def test_p1_label(self):
        """P1 should be detected."""
        assert extract_priority_from_labels(["P1"]) == "P1"

    def test_p2_label(self):
        """P2 should be detected."""
        assert extract_priority_from_labels(["P2"]) == "P2"

    def test_p3_label(self):
        """P3 should be detected."""
        assert extract_priority_from_labels(["P3"]) == "P3"

    def test_priority_in_comma_separated(self):
        """Priority in comma-separated label value."""
        assert extract_priority_from_labels(["bug,P1"]) == "P1"

    def test_highest_priority_wins(self):
        """Returns highest priority (P0 > P1 > P2 > P3)."""
        assert extract_priority_from_labels(["P2", "P0", "P1"]) == "P0"

    def test_priority_colon_format(self):
        """priority:P0 format should be detected."""
        assert extract_priority_from_labels(["priority:P1"]) == "P1"

    def test_priority_colon_in_comma_separated(self):
        """priority:P0 in comma-separated format."""
        assert extract_priority_from_labels(["bug,priority:P2"]) == "P2"

    def test_no_priority(self):
        """No priority label returns None."""
        assert extract_priority_from_labels(["bug", "enhancement"]) is None

    def test_case_insensitive(self):
        """Priority detection is case-insensitive."""
        assert extract_priority_from_labels(["p1"]) == "P1"
        assert extract_priority_from_labels(["p0"]) == "P0"

    def test_custom_priority_set(self):
        """Custom priority set can be provided."""
        assert extract_priority_from_labels(["P0"], priority_labels={"P0", "P1"}) == "P0"
        # P2 not in custom set
        assert extract_priority_from_labels(["P2"], priority_labels={"P0", "P1"}) is None

    def test_empty_labels(self):
        """Empty labels returns None."""
        assert extract_priority_from_labels([]) is None


class TestHasPriorityLabel:
    """Tests for has_priority_label function."""

    def test_has_p0(self):
        """P0 returns True."""
        assert has_priority_label(["P0"]) is True

    def test_has_p3(self):
        """P3 returns True."""
        assert has_priority_label(["P3"]) is True

    def test_no_priority(self):
        """No priority returns False."""
        assert has_priority_label(["bug", "enhancement"]) is False

    def test_in_comma_separated(self):
        """Priority in comma-separated returns True."""
        assert has_priority_label(["bug,P1"]) is True

    def test_custom_priority_set(self):
        """Custom priority set works."""
        assert has_priority_label(["P3"], priority_labels={"P0", "P1"}) is False


class TestDefaultPriorityLabels:
    """Tests for DEFAULT_PRIORITY_LABELS constant."""

    def test_contains_p0_to_p3(self):
        """Default set contains P0-P3."""
        assert "P0" in DEFAULT_PRIORITY_LABELS
        assert "P1" in DEFAULT_PRIORITY_LABELS
        assert "P2" in DEFAULT_PRIORITY_LABELS
        assert "P3" in DEFAULT_PRIORITY_LABELS

    def test_is_set(self):
        """DEFAULT_PRIORITY_LABELS is a set."""
        assert isinstance(DEFAULT_PRIORITY_LABELS, set)


class TestSuggestLabelsFromText:
    """Tests for suggest_labels_from_text function (Issue #2451)."""

    def test_bug_from_title(self):
        """Bug detected from title keywords."""
        assert suggest_labels_from_text("fix: バグを修正") == [("bug", "バグ報告")]

    def test_bug_from_english_keywords(self):
        """Bug detected from English keywords."""
        assert suggest_labels_from_text("Fix error in login") == [("bug", "バグ報告")]

    def test_enhancement_from_title(self):
        """Enhancement detected from title."""
        assert suggest_labels_from_text("feat: 新機能追加") == [("enhancement", "新機能・改善")]

    def test_documentation_from_body(self):
        """Documentation detected from body."""
        result = suggest_labels_from_text("Update files", "ドキュメントを更新")
        assert ("documentation", "ドキュメント") in result

    def test_multiple_labels(self):
        """Multiple labels can be detected."""
        result = suggest_labels_from_text("feat: 新機能追加", "ドキュメントも更新")
        labels = [label for label, _ in result]
        assert "enhancement" in labels
        assert "documentation" in labels

    def test_no_match(self):
        """No match returns empty list."""
        assert suggest_labels_from_text("Update configuration") == []

    def test_empty_title(self):
        """Empty title handled gracefully."""
        assert suggest_labels_from_text("", "バグ修正") == [("bug", "バグ報告")]

    def test_none_body(self):
        """None body handled gracefully."""
        assert suggest_labels_from_text("fix: バグ修正", None) == [("bug", "バグ報告")]

    def test_refactor_detection(self):
        """Refactor detected from keywords."""
        assert suggest_labels_from_text("refactor: コードを整理") == [
            ("refactor", "リファクタリング")
        ]

    def test_case_insensitive(self):
        """Detection is case-insensitive."""
        assert suggest_labels_from_text("FIX: Bug") == [("bug", "バグ報告")]

    def test_no_duplicate_labels(self):
        """Same label not suggested twice."""
        result = suggest_labels_from_text("fix: バグ修正", "エラーを修正")
        assert len([label for label, _ in result if label == "bug"]) == 1

    def test_no_false_positive_production(self):
        """'production' should not trigger 'documentation' label (substring issue)."""
        result = suggest_labels_from_text("production outage")
        labels = [label for label, _ in result]
        assert "documentation" not in labels

    def test_no_false_positive_docker(self):
        """'docker' should not trigger 'documentation' label."""
        result = suggest_labels_from_text("docker deployment fix")
        labels = [label for label, _ in result]
        assert "documentation" not in labels


class TestExtractTitleFromCommand:
    """Tests for extract_title_from_command function (Issue #2451)."""

    def test_long_form(self):
        """--title option."""
        cmd = 'gh issue create --title "Test title" --body "body"'
        assert extract_title_from_command(cmd) == "Test title"

    def test_short_form(self):
        """-t option."""
        cmd = 'gh issue create -t "Short title"'
        assert extract_title_from_command(cmd) == "Short title"

    def test_equals_form(self):
        """--title=value form."""
        cmd = 'gh issue create --title="Equals title"'
        assert extract_title_from_command(cmd) == "Equals title"

    def test_short_equals_form(self):
        """-t=value form."""
        cmd = 'gh issue create -t="Short equals"'
        assert extract_title_from_command(cmd) == "Short equals"

    def test_no_title(self):
        """No title returns None."""
        cmd = "gh issue create --body body"
        assert extract_title_from_command(cmd) is None

    def test_empty_command(self):
        """Empty command returns None."""
        assert extract_title_from_command("") is None

    def test_invalid_quotes(self):
        """Invalid quotes returns None."""
        cmd = 'gh issue create --title "unclosed'
        assert extract_title_from_command(cmd) is None


class TestExtractBodyFromCommand:
    """Tests for extract_body_from_command function (Issue #2451)."""

    def test_long_form(self):
        """--body option."""
        cmd = 'gh issue create --title "title" --body "Test body"'
        assert extract_body_from_command(cmd) == "Test body"

    def test_short_form(self):
        """-b option."""
        cmd = 'gh issue create -b "Short body"'
        assert extract_body_from_command(cmd) == "Short body"

    def test_equals_form(self):
        """--body=value form."""
        cmd = 'gh issue create --body="Equals body"'
        assert extract_body_from_command(cmd) == "Equals body"

    def test_short_equals_form(self):
        """-b=value form."""
        cmd = 'gh issue create -b="Short equals"'
        assert extract_body_from_command(cmd) == "Short equals"

    def test_no_body(self):
        """No body returns None."""
        cmd = 'gh issue create --title "title"'
        assert extract_body_from_command(cmd) is None

    def test_multiline_body(self):
        """Multiline body in quotes."""
        cmd = 'gh issue create --body "Line1\nLine2"'
        assert extract_body_from_command(cmd) == "Line1\nLine2"

    def test_heredoc_body(self):
        """Body from command substitution (as seen in actual usage)."""
        # When shlex parses, the whole $(cat ...) becomes one token
        cmd = 'gh issue create --body "## Summary\nDetails here"'
        result = extract_body_from_command(cmd)
        assert result == "## Summary\nDetails here"
