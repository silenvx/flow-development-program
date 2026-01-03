"""Tests for ci_monitor.constants module."""

from ci_monitor.constants import (
    AI_REVIEWER_IDENTIFIERS,
    CODE_BLOCK_PATTERN,
    DEFAULT_MAX_COPILOT_RETRY,
    DEFAULT_MAX_MERGE_ATTEMPTS,
    DEFAULT_MAX_REBASE,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_TIMEOUT_MINUTES,
    GITHUB_FILES_LIMIT,
    RATE_LIMIT_CACHE_TTL,
    RATE_LIMIT_CRITICAL_THRESHOLD,
    RATE_LIMIT_WARNING_THRESHOLD,
)


class TestDefaultConstants:
    """Tests for default configuration constants."""

    def test_default_max_rebase_is_positive(self):
        """Verify DEFAULT_MAX_REBASE is a positive integer."""
        assert isinstance(DEFAULT_MAX_REBASE, int)
        assert DEFAULT_MAX_REBASE > 0

    def test_default_polling_interval_is_positive(self):
        """Verify DEFAULT_POLLING_INTERVAL is a positive integer."""
        assert isinstance(DEFAULT_POLLING_INTERVAL, int)
        assert DEFAULT_POLLING_INTERVAL > 0

    def test_default_timeout_minutes_is_positive(self):
        """Verify DEFAULT_TIMEOUT_MINUTES is a positive integer."""
        assert isinstance(DEFAULT_TIMEOUT_MINUTES, int)
        assert DEFAULT_TIMEOUT_MINUTES > 0

    def test_default_max_copilot_retry_is_positive(self):
        """Verify DEFAULT_MAX_COPILOT_RETRY is a positive integer."""
        assert isinstance(DEFAULT_MAX_COPILOT_RETRY, int)
        assert DEFAULT_MAX_COPILOT_RETRY > 0

    def test_default_max_merge_attempts_is_positive(self):
        """Verify DEFAULT_MAX_MERGE_ATTEMPTS is a positive integer."""
        assert isinstance(DEFAULT_MAX_MERGE_ATTEMPTS, int)
        assert DEFAULT_MAX_MERGE_ATTEMPTS > 0


class TestRateLimitThresholds:
    """Tests for rate limit threshold constants."""

    def test_warning_threshold_greater_than_critical(self):
        """Verify warning threshold is greater than critical threshold."""
        assert RATE_LIMIT_WARNING_THRESHOLD > RATE_LIMIT_CRITICAL_THRESHOLD

    def test_thresholds_are_non_negative(self):
        """Verify thresholds are non-negative integers."""
        assert isinstance(RATE_LIMIT_WARNING_THRESHOLD, int)
        assert isinstance(RATE_LIMIT_CRITICAL_THRESHOLD, int)
        assert RATE_LIMIT_WARNING_THRESHOLD >= 0
        assert RATE_LIMIT_CRITICAL_THRESHOLD >= 0

    def test_cache_ttl_is_positive(self):
        """Verify RATE_LIMIT_CACHE_TTL is a positive integer."""
        assert isinstance(RATE_LIMIT_CACHE_TTL, int)
        assert RATE_LIMIT_CACHE_TTL > 0


class TestGitHubFilesLimit:
    """Tests for GitHub API limits."""

    def test_files_limit_is_positive(self):
        """Verify GITHUB_FILES_LIMIT is a positive integer."""
        assert isinstance(GITHUB_FILES_LIMIT, int)
        assert GITHUB_FILES_LIMIT > 0


class TestCodeBlockPattern:
    """Tests for CODE_BLOCK_PATTERN regex."""

    def test_matches_fenced_code_block(self):
        """Test matching fenced code blocks."""
        text = "```python\nprint('hello')\n```"
        match = CODE_BLOCK_PATTERN.search(text)
        assert match is not None
        assert match.group() == text

    def test_matches_fenced_code_block_without_language(self):
        """Test matching fenced code blocks without language specifier."""
        text = "```\nsome code\n```"
        match = CODE_BLOCK_PATTERN.search(text)
        assert match is not None
        assert match.group() == text

    def test_matches_inline_code(self):
        """Test matching inline code."""
        text = "Use `some_function()` here"
        match = CODE_BLOCK_PATTERN.search(text)
        assert match is not None
        assert match.group() == "`some_function()`"

    def test_does_not_match_plain_text(self):
        """Test that plain text is not matched."""
        text = "This is plain text without code"
        match = CODE_BLOCK_PATTERN.search(text)
        assert match is None

    def test_inline_code_does_not_span_newlines(self):
        """Test that inline code does not match across newlines."""
        text = "`start\nend`"
        match = CODE_BLOCK_PATTERN.search(text)
        assert match is None

    def test_findall_multiple_code_blocks(self):
        """Test finding multiple code blocks."""
        text = "Use `func1()` and `func2()` here"
        matches = CODE_BLOCK_PATTERN.findall(text)
        assert len(matches) == 2
        assert "`func1()`" in matches
        assert "`func2()`" in matches

    def test_mixed_fenced_and_inline(self):
        """Test document with both fenced and inline code."""
        text = "See `example()` below:\n```\ncode here\n```"
        matches = CODE_BLOCK_PATTERN.findall(text)
        assert len(matches) == 2


class TestAIReviewerIdentifiers:
    """Tests for AI reviewer identifiers."""

    def test_identifiers_is_list(self):
        """Verify AI_REVIEWER_IDENTIFIERS is a list."""
        assert isinstance(AI_REVIEWER_IDENTIFIERS, list)

    def test_identifiers_contains_copilot(self):
        """Verify list contains copilot identifier."""
        assert "copilot" in AI_REVIEWER_IDENTIFIERS

    def test_identifiers_are_lowercase(self):
        """Verify all identifiers are lowercase strings."""
        for identifier in AI_REVIEWER_IDENTIFIERS:
            assert isinstance(identifier, str)
            assert identifier == identifier.lower()
