#!/usr/bin/env bats
# Tests for issue-ai-review.sh
#
# Requirements:
#   - bats-core: brew install bats-core
#
# Usage:
#   bats .claude/scripts/tests/test_issue_ai_review.bats

# Test directory setup
setup() {
    # Create temporary directory for tests
    TEST_DIR=$(mktemp -d)
    export TEST_DIR

    # Source the script directory
    SCRIPT_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
    export SCRIPT_DIR
    SCRIPT_PATH="$SCRIPT_DIR/issue-ai-review.sh"
    export SCRIPT_PATH
}

teardown() {
    # Clean up temporary directory
    /bin/rm -rf "$TEST_DIR"
}

# =============================================================================
# Basic argument validation tests
# =============================================================================

@test "script exits with error when no argument provided" {
    run "$SCRIPT_PATH"
    [ "$status" -eq 1 ]
    [[ "$output" == *"Usage:"* ]]
}

@test "script exits with error for empty argument" {
    run "$SCRIPT_PATH" ""
    [ "$status" -eq 1 ]
    [[ "$output" == *"Usage:"* ]]
}

# =============================================================================
# Shell escaping security tests
# =============================================================================

@test "jq safely handles shell metacharacters in issue content" {
    # Create mock issue JSON with dangerous content
    MOCK_JSON='{"title":"Test $(rm -rf /)","body":"Test `id` and $HOME","labels":[]}'

    # Run jq expression from the script
    result=$(echo "$MOCK_JSON" | jq -r '
        "# " + .title + "\n\n" + (.body // "") + "\n\nLabels: " + ([.labels[].name] | join(", "))
    ')

    # Verify shell metacharacters are preserved as literal text (not executed)
    [[ "$result" == *'$(rm -rf /)'* ]]
    [[ "$result" == *'`id`'* ]]
    [[ "$result" == *'$HOME'* ]]
}

@test "jq safely handles quotes in issue content" {
    MOCK_JSON='{"title":"Test \"quoted\" content","body":"Single '\''quotes'\'' too","labels":[]}'

    result=$(echo "$MOCK_JSON" | jq -r '
        "# " + .title + "\n\n" + (.body // "") + "\n\nLabels: " + ([.labels[].name] | join(", "))
    ')

    # Verify quotes are preserved
    [[ "$result" == *'quoted'* ]]
    [[ "$result" == *'quotes'* ]]
}

@test "jq safely handles newlines in issue body" {
    MOCK_JSON='{"title":"Test","body":"Line1\nLine2\nLine3","labels":[]}'

    result=$(echo "$MOCK_JSON" | jq -r '
        "# " + .title + "\n\n" + (.body // "") + "\n\nLabels: " + ([.labels[].name] | join(", "))
    ')

    # Verify newlines are preserved
    line_count=$(echo "$result" | wc -l)
    [ "$line_count" -gt 3 ]
}

# =============================================================================
# Sentinel value tests
# =============================================================================

@test "sentinel values are distinct from normal output" {
    # Define the same sentinel values as the script
    GEMINI_UNAVAILABLE="__GEMINI_CLI_NOT_AVAILABLE__"
    GEMINI_FAILED="__GEMINI_REVIEW_FAILED__"
    CODEX_UNAVAILABLE="__CODEX_CLI_NOT_AVAILABLE__"
    CODEX_FAILED="__CODEX_REVIEW_FAILED__"

    # These sentinel values should not appear in normal review output
    # They use double underscore prefix/suffix to avoid collisions
    [ "${#GEMINI_UNAVAILABLE}" -gt 20 ]
    [ "${#GEMINI_FAILED}" -gt 20 ]
    [ "${#CODEX_UNAVAILABLE}" -gt 20 ]
    [ "${#CODEX_FAILED}" -gt 20 ]

    # Verify format
    [[ "$GEMINI_UNAVAILABLE" == __*__ ]]
    [[ "$GEMINI_FAILED" == __*__ ]]
    [[ "$CODEX_UNAVAILABLE" == __*__ ]]
    [[ "$CODEX_FAILED" == __*__ ]]
}

# =============================================================================
# Output filtering logic tests
# =============================================================================

@test "review output check correctly identifies useful output" {
    # Simulate the script's logic for checking useful output
    GEMINI_UNAVAILABLE="__GEMINI_CLI_NOT_AVAILABLE__"
    GEMINI_FAILED="__GEMINI_REVIEW_FAILED__"

    # Case 1: Useful output
    GEMINI_OUTPUT="This is a useful review"
    gemini_useful=false
    if [[ "$GEMINI_OUTPUT" != "$GEMINI_UNAVAILABLE" && "$GEMINI_OUTPUT" != "$GEMINI_FAILED" && -n "$GEMINI_OUTPUT" ]]; then
        gemini_useful=true
    fi
    [ "$gemini_useful" = "true" ]

    # Case 2: Unavailable sentinel
    GEMINI_OUTPUT="$GEMINI_UNAVAILABLE"
    gemini_useful=false
    if [[ "$GEMINI_OUTPUT" != "$GEMINI_UNAVAILABLE" && "$GEMINI_OUTPUT" != "$GEMINI_FAILED" && -n "$GEMINI_OUTPUT" ]]; then
        gemini_useful=true
    fi
    [ "$gemini_useful" = "false" ]

    # Case 3: Failed sentinel
    GEMINI_OUTPUT="$GEMINI_FAILED"
    gemini_useful=false
    if [[ "$GEMINI_OUTPUT" != "$GEMINI_UNAVAILABLE" && "$GEMINI_OUTPUT" != "$GEMINI_FAILED" && -n "$GEMINI_OUTPUT" ]]; then
        gemini_useful=true
    fi
    [ "$gemini_useful" = "false" ]

    # Case 4: Empty output
    GEMINI_OUTPUT=""
    gemini_useful=false
    if [[ "$GEMINI_OUTPUT" != "$GEMINI_UNAVAILABLE" && "$GEMINI_OUTPUT" != "$GEMINI_FAILED" && -n "$GEMINI_OUTPUT" ]]; then
        gemini_useful=true
    fi
    [ "$gemini_useful" = "false" ]
}

# =============================================================================
# Label extraction tests
# =============================================================================

@test "jq correctly extracts and joins labels" {
    MOCK_JSON='{"title":"Test","body":"Body","labels":[{"name":"bug"},{"name":"enhancement"},{"name":"P1"}]}'

    result=$(echo "$MOCK_JSON" | jq -r '[.labels[].name] | join(", ")')

    [ "$result" = "bug, enhancement, P1" ]
}

@test "jq handles empty labels array" {
    MOCK_JSON='{"title":"Test","body":"Body","labels":[]}'

    result=$(echo "$MOCK_JSON" | jq -r '[.labels[].name] | join(", ")')

    [ "$result" = "" ]
}

@test "jq handles null body" {
    MOCK_JSON='{"title":"Test","body":null,"labels":[]}'

    result=$(echo "$MOCK_JSON" | jq -r '.body // ""')

    [ "$result" = "" ]
}
