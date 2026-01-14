#!/usr/bin/env bats
# Tests for update_codex_marker_on_rebase.sh
#
# Requirements:
#   - bats-core: brew install bats-core
#
# Usage:
#   bats .claude/scripts/tests/test_update_codex_marker.bats

# Test directory setup
setup() {
    # Create temporary directory for tests
    TEST_DIR=$(mktemp -d)
    export TEST_DIR

    # Script location
    SCRIPT_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
    export SCRIPT_DIR

    # Create a mock git repository
    cd "$TEST_DIR" || exit 1
    git init -q
    git config user.email "test@example.com"
    git config user.name "Test User"

    # Create initial commit
    echo "test" > test.txt
    git add test.txt
    git commit -q -m "Initial commit"

    # Create the markers directory
    mkdir -p .claude/logs/markers
}

teardown() {
    # Clean up temporary directory
    cd /
    /bin/rm -rf "$TEST_DIR"
}

# =============================================================================
# sanitize_branch_name tests (via script sourcing)
# =============================================================================

# Helper to extract and test sanitize_branch_name function
# Issue #812: Extract function from actual script to prevent divergence
load_sanitize_function() {
    # Source only the sanitize_branch_name function from the actual script
    # This ensures tests always use the same implementation as production
    local tmp_script
    tmp_script=$(mktemp)
    trap '/bin/rm -f "$tmp_script"' RETURN

    # Extract the sanitize_branch_name function from the actual script
    # Pattern: from "sanitize_branch_name() {" to next "^}" (function end)
    sed -n '/^sanitize_branch_name() {$/,/^}$/p' "$SCRIPT_DIR/update_codex_marker_on_rebase.sh" > "$tmp_script"

    # Verify extraction succeeded
    if [ ! -s "$tmp_script" ]; then
        echo "ERROR: Failed to extract sanitize_branch_name function from script" >&2
        return 1
    fi

    # shellcheck disable=SC1090
    source "$tmp_script"
}

@test "sanitize_branch_name replaces slash with dash" {
    load_sanitize_function

    result=$(sanitize_branch_name "feature/test")
    [ "$result" = "feature-test" ]
}

@test "sanitize_branch_name replaces backslash with dash" {
    load_sanitize_function

    result=$(sanitize_branch_name "feature\\test")
    [ "$result" = "feature-test" ]
}

@test "sanitize_branch_name replaces colon with dash" {
    load_sanitize_function

    result=$(sanitize_branch_name "feature:test")
    [ "$result" = "feature-test" ]
}

@test "sanitize_branch_name replaces multiple special chars" {
    load_sanitize_function

    result=$(sanitize_branch_name "feat/issue-123/test:foo")
    [ "$result" = "feat-issue-123-test-foo" ]
}

@test "sanitize_branch_name replaces space with underscore" {
    load_sanitize_function

    result=$(sanitize_branch_name "feature test")
    [ "$result" = "feature_test" ]
}

@test "sanitize_branch_name removes consecutive dashes" {
    load_sanitize_function

    result=$(sanitize_branch_name "feat//test")
    [ "$result" = "feat-test" ]
}

@test "sanitize_branch_name removes leading dash" {
    load_sanitize_function

    result=$(sanitize_branch_name "/test")
    [ "$result" = "test" ]
}

@test "sanitize_branch_name removes trailing dash" {
    load_sanitize_function

    result=$(sanitize_branch_name "test/")
    [ "$result" = "test" ]
}

# =============================================================================
# Main script behavior tests
# =============================================================================

@test "script skips when on main branch" {
    cd "$TEST_DIR" || exit 1

    # We're on the default branch (main or master depending on git config)
    # The script should exit silently
    run bash "$SCRIPT_DIR/update_codex_marker_on_rebase.sh"
    [ "$status" -eq 0 ]
}

@test "script skips when marker file does not exist" {
    cd "$TEST_DIR" || exit 1

    # Create a feature branch
    git checkout -q -b feature/test-branch

    # No marker file exists
    run bash "$SCRIPT_DIR/update_codex_marker_on_rebase.sh"
    [ "$status" -eq 0 ]

    # Marker file should not be created
    [ ! -f ".claude/logs/markers/codex-review-feature-test-branch.done" ]
}

@test "script updates marker file when it exists" {
    cd "$TEST_DIR" || exit 1

    # Create a feature branch
    git checkout -q -b feature/test-update

    # Create a marker file with old commit
    echo "feature/test-update:oldcommit123" > .claude/logs/markers/codex-review-feature-test-update.done

    # Run the script
    run bash "$SCRIPT_DIR/update_codex_marker_on_rebase.sh"
    [ "$status" -eq 0 ]

    # Verify marker was updated (format: branch:commit:diff_hash per Issue #841)
    content=$(cat .claude/logs/markers/codex-review-feature-test-update.done)
    current_commit=$(git rev-parse --short HEAD)
    # diff_hash is sha256 of "git diff main" output, first 12 chars
    # In test repo, main doesn't exist so diff_hash may be empty or computed from empty diff
    # Check that content starts with expected branch:commit prefix
    [[ "$content" == "feature/test-update:$current_commit:"* ]]
}

@test "script outputs to stderr not stdout" {
    cd "$TEST_DIR" || exit 1

    # Create a feature branch
    git checkout -q -b feature/test-output

    # Create a marker file
    echo "feature/test-output:oldcommit" > .claude/logs/markers/codex-review-feature-test-output.done

    # Run the script and capture stdout and stderr separately
    stdout_output=$(bash "$SCRIPT_DIR/update_codex_marker_on_rebase.sh" 2>/dev/null)

    # stdout should be empty
    [ -z "$stdout_output" ]
}
