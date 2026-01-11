"""Reflection-related utilities for hooks.

Issue #2694: Extract common reflection-checking functions from
immediate-pending-check.py and reflection-completion-check.py.
"""

import re

# Whitelist of allowed [IMMEDIATE] actions
# Only these specific commands are recognized to prevent false positives
# from test examples, documentation, and code fragments in transcripts.
ALLOWED_IMMEDIATE_ACTIONS: frozenset[str] = frozenset(
    [
        "/reflect",
    ]
)


def check_transcript_for_reflection(transcript_content: str) -> bool:
    """Check the conversation transcript for reflection indicators.

    Returns True if reflection keywords (五省, 振り返り, etc.) are found.

    Issue #2708: Performance optimization - combine patterns with | and search once
    instead of looping through each pattern.
    """
    # Combine all patterns with | for single regex search (performance optimization)
    # ReDoS mitigation: replace .* with negated character class and length limit
    reflection_pattern = "|".join(
        [
            r"五省",
            r"振り返り",
            r"反省点",
            r"改善点",
            r"教訓",
            # Issue #2708: ReDoS mitigation - use [^X]{0,512} instead of .*
            r"要件理解[^悖]{0,512}悖",
            r"実装[^恥]{0,512}恥",
            r"検証[^欠]{0,512}欠",
            r"対応[^憾]{0,512}憾",
            r"効率[^欠]{0,512}欠",
        ]
    )
    return bool(re.search(reflection_pattern, transcript_content))


def check_skill_invocation(transcript_content: str) -> bool:
    """Check if /reflect skill was invoked in the session.

    Issue #2140: Detect when the reflect skill is invoked so that
    reflection completion can be enforced even without PR merge.

    Issue #2489: Exclude [IMMEDIATE: /reflect] tags from detection.
    The IMMEDIATE tag is issued by the hook system, not by actual skill invocation.

    Returns True if skill invocation patterns are found.
    """
    # Issue #2489: Remove [IMMEDIATE: ...] tags before checking
    # These are hook-issued tags, not actual skill invocations
    # ReDoS mitigation: limit content length to 256 characters
    cleaned_content = re.sub(
        r"\[IMMEDIATE:\s*[^\]]{1,256}\]", "", transcript_content, flags=re.IGNORECASE
    )

    # Performance: combine patterns with | and search once instead of looping
    skill_pattern = "|".join(
        [
            r"Skill: reflect",  # Skill tool invocation
            r"@\.claude/skills/reflect/SKILL\.md",  # Direct skill reference
            r"/reflect\b",  # Slash command
            # Issue #2707: ReDoS mitigation - use [^)]* instead of .* to prevent
            # catastrophic backtracking
            r"Skill\([^)]*reflect[^)]*\)",  # Skill tool call syntax
        ]
    )
    return bool(re.search(skill_pattern, cleaned_content, re.IGNORECASE))


def is_valid_immediate_action(action: str) -> bool:
    """Validate that an extracted action is an allowed command.

    Issue #2193: The regex pattern can match code examples in the transcript,
    such as pattern definitions or test strings. This function filters out
    such false positives.

    Issue #2201: Restricted to slash commands only.

    Issue #2209: Further restricted to explicit whitelist to prevent false
    positives from test examples like [IMMEDIATE: /test] or [IMMEDIATE: /commit].

    Valid actions:
    - Only commands in ALLOWED_IMMEDIATE_ACTIONS whitelist
    - Currently only /reflect is allowed

    Args:
        action: The extracted action string

    Returns:
        True if the action is in the allowed whitelist
    """
    action = action.strip().lower()
    return action in ALLOWED_IMMEDIATE_ACTIONS


def extract_immediate_tags(transcript_content: str) -> list[str]:
    """Extract [IMMEDIATE: action] tags from transcript.

    Issue #2186: Detect [IMMEDIATE: /reflect] or similar tags that require
    immediate execution without user confirmation.

    Issue #2193: Validates extracted actions to filter out code fragments
    that accidentally match the pattern.

    Issue #2209: Normalizes actions to lowercase for consistent deduplication.

    Returns:
        List of actions that were requested (e.g., ["/reflect"])
    """
    # Pattern: [IMMEDIATE: action] where action can be a slash command or text
    # Issue #2704: ReDoS mitigation - limit content length to 256 characters
    pattern = r"\[IMMEDIATE:\s*([^\]]{1,256})\]"
    matches = re.findall(pattern, transcript_content, re.IGNORECASE)
    # Normalize (lowercase), validate, and deduplicate
    # Issue #2704: Use dict.fromkeys for O(1) deduplication while preserving order
    processed_actions = (match.strip().lower() for match in matches)
    valid_actions = [
        action for action in processed_actions if action and is_valid_immediate_action(action)
    ]
    return list(dict.fromkeys(valid_actions))


def check_immediate_action_executed(action: str, transcript_content: str) -> bool:
    """Check if an [IMMEDIATE] action was executed.

    Issue #2186: Verify that the specified action was performed.

    For the special case of "/reflect", this verifies BOTH:
    1. Skill invocation (via `check_skill_invocation`) - actual /reflect skill was called
    2. Reflection content (via `check_transcript_for_reflection`) - 五省 keywords present

    Issue #2489: Manual 五省 summaries without skill invocation are not sufficient.

    For other (generic) actions, this function currently returns False
    because reliable verification requires action-specific logic (e.g.,
    checking command execution logs, test results, etc.) which is not
    yet implemented.

    Note: Future enhancement could add verification for other common
    actions like "/commit", "run tests", etc.

    Args:
        action: The action string (e.g., "/reflect", "run tests")
        transcript_content: Full transcript to search

    Returns:
        True if the action appears to have been executed.
        Currently only /reflect is verifiable; other actions return False.
    """
    action_lower = action.lower().strip()

    # Handle /reflect action - verify BOTH skill invocation AND reflection content
    # Issue #2489: Keyword-only detection allowed manual summaries to bypass enforcement
    if "/reflect" in action_lower:
        # Must verify skill was actually invoked (not just keywords in transcript)
        skill_invoked = check_skill_invocation(transcript_content)
        has_reflection_content = check_transcript_for_reflection(transcript_content)
        return skill_invoked and has_reflection_content

    # Future enhancement: For actions other than /reflect, implement
    # action-specific verification logic (e.g., check command execution
    # logs, test results, etc.).
    #
    # Currently, we cannot reliably verify generic actions, so we return
    # False to indicate "not verified" (which will trigger a block if
    # called from the main verification flow).
    return False
