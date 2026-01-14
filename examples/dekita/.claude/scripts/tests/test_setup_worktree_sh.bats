#!/usr/bin/env bats
# Tests for setup_worktree.sh
#
# Requirements:
#   - bats-core: brew install bats-core
#
# Usage:
#   bats .claude/scripts/tests/test_setup_worktree.bats

# Test directory setup
setup() {
    # Create temporary directory for tests
    # Export for subshell access (consistency with other bats tests)
    export TEST_DIR
    TEST_DIR=$(mktemp -d)

    # Save original working directory
    export ORIG_PWD
    ORIG_PWD="$PWD"

    # Script directory
    export SCRIPT_DIR
    SCRIPT_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
    export SCRIPT_PATH
    SCRIPT_PATH="$SCRIPT_DIR/setup_worktree.sh"
}

teardown() {
    # Restore original directory with error handling
    # Use cd / as fallback if ORIG_PWD is undefined or cd fails
    cd "$ORIG_PWD" 2>/dev/null || cd /
    # Clean up temporary directory
    /bin/rm -rf "$TEST_DIR"
}

# =============================================================================
# Argument validation tests
# =============================================================================

@test "shows usage when no argument provided" {
    run bash "$SCRIPT_PATH"

    [ "$status" -eq 1 ]
    [[ "$output" == *"Usage:"* ]]
}

@test "shows error when path does not exist" {
    run bash "$SCRIPT_PATH" "/nonexistent/path"

    [ "$status" -eq 1 ]
    [[ "$output" == *"does not exist"* ]]
}

@test "shows error when argument is empty string" {
    run bash "$SCRIPT_PATH" ""

    [ "$status" -eq 1 ]
    [[ "$output" == *"Usage:"* ]]
}

# =============================================================================
# Path handling tests
# =============================================================================

@test "accepts absolute path" {
    # Create a mock worktree directory (no package.json, no pyproject.toml)
    mkdir -p "$TEST_DIR/mock-worktree"

    run bash "$SCRIPT_PATH" "$TEST_DIR/mock-worktree"

    [ "$status" -eq 0 ]
    [[ "$output" == *"Worktree setup complete"* ]]
}

@test "accepts relative path" {
    # Create a mock worktree directory
    mkdir -p "$TEST_DIR/mock-worktree"

    # Change to parent directory and use relative path
    cd "$TEST_DIR"

    run bash "$SCRIPT_PATH" "mock-worktree"

    [ "$status" -eq 0 ]
    [[ "$output" == *"Worktree setup complete"* ]]
}

@test "converts relative path to absolute path" {
    # Create a mock worktree directory
    mkdir -p "$TEST_DIR/mock-worktree"

    # Change to parent directory and use relative path
    cd "$TEST_DIR"

    run bash "$SCRIPT_PATH" "mock-worktree"

    [ "$status" -eq 0 ]
    # Output should contain absolute path, not relative
    [[ "$output" == *"$TEST_DIR/mock-worktree"* ]]
}

@test "handles path with spaces" {
    # Create a mock worktree directory with spaces in name
    mkdir -p "$TEST_DIR/mock worktree with spaces"

    run bash "$SCRIPT_PATH" "$TEST_DIR/mock worktree with spaces"

    [ "$status" -eq 0 ]
    [[ "$output" == *"Worktree setup complete"* ]]
}

# =============================================================================
# Node.js project detection tests
# =============================================================================

@test "detects Node.js project when package.json exists" {
    # Create a mock worktree with package.json
    mkdir -p "$TEST_DIR/node-worktree"
    echo '{"name": "test"}' > "$TEST_DIR/node-worktree/package.json"

    # Mock pnpm to avoid actual installation
    mkdir -p "$TEST_DIR/bin"
    cat > "$TEST_DIR/bin/pnpm" << 'MOCK'
#!/bin/bash
echo "pnpm install mock"
MOCK
    chmod +x "$TEST_DIR/bin/pnpm"

    PATH="$TEST_DIR/bin:$PATH" run bash "$SCRIPT_PATH" "$TEST_DIR/node-worktree"

    [ "$status" -eq 0 ]
    [[ "$output" == *"Installing Node.js dependencies"* ]]
}

@test "fails when pnpm not installed for Node.js project" {
    # Create a mock worktree with package.json
    mkdir -p "$TEST_DIR/node-worktree"
    echo '{"name": "test"}' > "$TEST_DIR/node-worktree/package.json"

    # Override PATH to minimal set that typically doesn't include pnpm
    # This tests the error handling when pnpm is not found
    # Note: If pnpm happens to be in /usr/bin or /bin, the test may behave differently
    # but in practice pnpm is usually installed via npm/volta/homebrew in other paths
    PATH="/usr/bin:/bin" run bash "$SCRIPT_PATH" "$TEST_DIR/node-worktree"

    [ "$status" -eq 1 ]
    [[ "$output" == *"pnpm is not installed"* ]]
}

# =============================================================================
# Python project detection tests
# =============================================================================

@test "detects Python project when pyproject.toml exists" {
    # Create a mock worktree with pyproject.toml
    mkdir -p "$TEST_DIR/python-worktree"
    echo '[project]' > "$TEST_DIR/python-worktree/pyproject.toml"

    run bash "$SCRIPT_PATH" "$TEST_DIR/python-worktree"

    [ "$status" -eq 0 ]
    [[ "$output" == *"Python project detected"* ]]
}

# =============================================================================
# Combined project tests
# =============================================================================

@test "handles project with both package.json and pyproject.toml" {
    # Create a mock worktree with both
    mkdir -p "$TEST_DIR/mixed-worktree"
    echo '{"name": "test"}' > "$TEST_DIR/mixed-worktree/package.json"
    echo '[project]' > "$TEST_DIR/mixed-worktree/pyproject.toml"

    # Mock pnpm
    mkdir -p "$TEST_DIR/bin"
    cat > "$TEST_DIR/bin/pnpm" << 'MOCK'
#!/bin/bash
echo "pnpm install mock"
MOCK
    chmod +x "$TEST_DIR/bin/pnpm"

    PATH="$TEST_DIR/bin:$PATH" run bash "$SCRIPT_PATH" "$TEST_DIR/mixed-worktree"

    [ "$status" -eq 0 ]
    [[ "$output" == *"Installing Node.js dependencies"* ]]
    [[ "$output" == *"Python project detected"* ]]
}

@test "handles empty project (no package.json, no pyproject.toml)" {
    # Create a mock empty worktree
    mkdir -p "$TEST_DIR/empty-worktree"

    run bash "$SCRIPT_PATH" "$TEST_DIR/empty-worktree"

    [ "$status" -eq 0 ]
    [[ "$output" == *"Worktree setup complete"* ]]
    # Should not mention Node.js or Python
    [[ "$output" != *"Installing Node.js dependencies"* ]]
    [[ "$output" != *"Python project detected"* ]]
}
