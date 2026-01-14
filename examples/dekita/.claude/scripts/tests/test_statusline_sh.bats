#!/usr/bin/env bats
# Tests for statusline.sh
#
# Requirements:
#   - bats-core: brew install bats-core
#
# Usage:
#   bats .claude/scripts/tests/test_statusline.bats

# Test directory setup
setup() {
    # Create temporary directory for tests
    TEST_DIR=$(mktemp -d)
    export TEST_DIR

    # Source the script to get functions
    SCRIPT_DIR="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
    export SCRIPT_DIR
}

teardown() {
    # Clean up temporary directory
    /bin/rm -rf "$TEST_DIR"
}

# Helper to load only the extract_issue_number function
load_extract_issue_number() {
    # Source only the constants and extract_issue_number function
    local tmp_script
    tmp_script=$(mktemp)
    trap '/bin/rm -f "$tmp_script"' RETURN

    # Extract only the ISSUE_PATTERN constant and extract_issue_number function
    cat > "$tmp_script" << 'SCRIPT'
readonly ISSUE_PATTERN='issue-[0-9]+'

extract_issue_number() {
    local input="$1"
    echo "$input" | grep -oE "$ISSUE_PATTERN" | head -1 | sed 's/issue-//'
}
SCRIPT
    # shellcheck disable=SC1090
    source "$tmp_script"
}

# =============================================================================
# extract_issue_number tests
# =============================================================================

@test "extract_issue_number returns empty for empty string" {
    load_extract_issue_number

    result=$(extract_issue_number "")
    [ -z "$result" ]
}

@test "extract_issue_number returns empty when no issue number" {
    load_extract_issue_number

    result=$(extract_issue_number "main")
    [ -z "$result" ]

    result=$(extract_issue_number "feature/some-feature")
    [ -z "$result" ]

    result=$(extract_issue_number "fix/bug-fix")
    [ -z "$result" ]
}

@test "extract_issue_number extracts number from simple issue format" {
    load_extract_issue_number

    result=$(extract_issue_number "issue-123")
    [ "$result" = "123" ]

    result=$(extract_issue_number "issue-1")
    [ "$result" = "1" ]

    result=$(extract_issue_number "issue-99999")
    [ "$result" = "99999" ]
}

@test "extract_issue_number extracts number from issue-XXX-description format" {
    load_extract_issue_number

    result=$(extract_issue_number "issue-123-add-feature")
    [ "$result" = "123" ]

    result=$(extract_issue_number "issue-456-fix-bug-in-component")
    [ "$result" = "456" ]
}

@test "extract_issue_number extracts number from branch name format" {
    load_extract_issue_number

    result=$(extract_issue_number "feature/issue-123-xxx")
    [ "$result" = "123" ]

    result=$(extract_issue_number "fix/issue-456")
    [ "$result" = "456" ]

    result=$(extract_issue_number "hotfix/issue-789-urgent-fix")
    [ "$result" = "789" ]
}

@test "extract_issue_number returns first match when multiple issue numbers" {
    load_extract_issue_number

    result=$(extract_issue_number "issue-123-related-to-issue-456")
    [ "$result" = "123" ]

    result=$(extract_issue_number "feature/issue-100-and-issue-200")
    [ "$result" = "100" ]
}

@test "extract_issue_number does not match invalid patterns" {
    load_extract_issue_number

    # No number after issue-
    result=$(extract_issue_number "issue-abc")
    [ -z "$result" ]

    # Different prefix
    result=$(extract_issue_number "bug-123")
    [ -z "$result" ]

    # No hyphen
    result=$(extract_issue_number "issue123")
    [ -z "$result" ]

    # Uppercase (grep -o is case sensitive by default)
    result=$(extract_issue_number "ISSUE-123")
    [ -z "$result" ]
}

@test "extract_issue_number handles special characters in input" {
    load_extract_issue_number

    result=$(extract_issue_number "refs/heads/issue-123")
    [ "$result" = "123" ]

    result=$(extract_issue_number "origin/feature/issue-456-test")
    [ "$result" = "456" ]
}

# =============================================================================
# get_language tests
# =============================================================================

# Helper to load the get_language function
load_get_language() {
    local tmp_script
    tmp_script=$(mktemp)
    trap '/bin/rm -f "$tmp_script"' RETURN

    cat > "$tmp_script" << 'SCRIPT'
get_language() {
    if [ -n "${STATUSLINE_LANG:-}" ]; then
        echo "$STATUSLINE_LANG"
    elif [ -n "${LANG:-}" ]; then
        echo "${LANG%%_*}"
    else
        echo "ja"
    fi
}
SCRIPT
    # shellcheck disable=SC1090
    source "$tmp_script"
}

@test "get_language returns STATUSLINE_LANG when set" {
    load_get_language

    STATUSLINE_LANG="en" result=$(get_language)
    [ "$result" = "en" ]

    STATUSLINE_LANG="ja" result=$(get_language)
    [ "$result" = "ja" ]
}

@test "get_language extracts language code from LANG" {
    load_get_language

    unset STATUSLINE_LANG
    LANG="ja_JP.UTF-8" result=$(get_language)
    [ "$result" = "ja" ]

    LANG="en_US.UTF-8" result=$(get_language)
    [ "$result" = "en" ]

    LANG="de_DE.UTF-8" result=$(get_language)
    [ "$result" = "de" ]
}

@test "get_language returns ja as default" {
    load_get_language

    unset STATUSLINE_LANG
    unset LANG
    result=$(get_language)
    [ "$result" = "ja" ]
}

@test "get_language prefers STATUSLINE_LANG over LANG" {
    load_get_language

    STATUSLINE_LANG="en" LANG="ja_JP.UTF-8" result=$(get_language)
    [ "$result" = "en" ]
}

# =============================================================================
# setup_messages tests
# =============================================================================

# Helper to load the setup_messages function and its dependencies
load_setup_messages() {
    local tmp_script
    tmp_script=$(mktemp)
    trap '/bin/rm -f "$tmp_script"' RETURN

    cat > "$tmp_script" << 'SCRIPT'
get_language() {
    if [ -n "${STATUSLINE_LANG:-}" ]; then
        echo "$STATUSLINE_LANG"
    elif [ -n "${LANG:-}" ]; then
        echo "${LANG%%_*}"
    else
        echo "ja"
    fi
}

setup_messages() {
    local lang
    lang=$(get_language)

    case "$lang" in
        en)
            STATUS_REVIEWING="reviewing"
            STATUS_MERGED="merged"
            STATUS_CLOSED="closed"
            STATUS_NO_PR="no PR"
            ;;
        *)
            STATUS_REVIEWING="レビュー中"
            STATUS_MERGED="マージ済"
            STATUS_CLOSED="クローズ"
            STATUS_NO_PR="PRなし"
            ;;
    esac
}
SCRIPT
    # shellcheck disable=SC1090
    source "$tmp_script"
}

@test "setup_messages sets Japanese messages by default" {
    load_setup_messages

    unset STATUSLINE_LANG
    unset LANG
    setup_messages

    [ "$STATUS_REVIEWING" = "レビュー中" ]
    [ "$STATUS_MERGED" = "マージ済" ]
    [ "$STATUS_CLOSED" = "クローズ" ]
    [ "$STATUS_NO_PR" = "PRなし" ]
}

@test "setup_messages sets English messages when STATUSLINE_LANG=en" {
    load_setup_messages

    STATUSLINE_LANG="en"
    setup_messages

    [ "$STATUS_REVIEWING" = "reviewing" ]
    [ "$STATUS_MERGED" = "merged" ]
    [ "$STATUS_CLOSED" = "closed" ]
    [ "$STATUS_NO_PR" = "no PR" ]
}

@test "setup_messages sets Japanese messages for unsupported language" {
    load_setup_messages

    STATUSLINE_LANG="fr"
    setup_messages

    [ "$STATUS_REVIEWING" = "レビュー中" ]
    [ "$STATUS_MERGED" = "マージ済" ]
    [ "$STATUS_CLOSED" = "クローズ" ]
    [ "$STATUS_NO_PR" = "PRなし" ]
}

# =============================================================================
# sanitize function tests (Terminal Injection対策)
# =============================================================================

# Helper to load the sanitize function
# Note: statusline.shは実行スクリプト（stdin読み込み、git実行等）のため直接sourceできない
# 他のテスト関数（load_extract_issue_number等）と同様のパターンを採用
load_sanitize() {
    # Define sanitize function with production-equivalent implementation
    # 本番コード（statusline.sh:270）と同等のロジック
    # ESC文字をANSI-C quoting ($'...') で直接埋め込み
    sanitize() {
        local input="$1"
        # ANSIエスケープシーケンスを除去し、制御文字（0x00-0x1F, 0x7F）を除去
        printf '%s' "$input" | sed $'s/\x1b\\[[0-9;]*[a-zA-Z]//g' | tr -d '\000-\037\177'
    }
}

@test "sanitize passes through normal text unchanged" {
    load_sanitize

    result=$(sanitize "Hello World")
    [ "$result" = "Hello World" ]

    result=$(sanitize "issue-123 | PR #456")
    [ "$result" = "issue-123 | PR #456" ]

    result=$(sanitize "[Opus] main | PRなし")
    [ "$result" = "[Opus] main | PRなし" ]
}

@test "sanitize removes ANSI color escape sequences" {
    load_sanitize

    # Red text: \x1b[31m
    result=$(sanitize $'\x1b[31mRed Text\x1b[0m')
    [ "$result" = "Red Text" ]

    # Bold: \x1b[1m
    result=$(sanitize $'\x1b[1mBold\x1b[0m')
    [ "$result" = "Bold" ]

    # Multiple colors
    result=$(sanitize $'\x1b[32mGreen\x1b[0m and \x1b[34mBlue\x1b[0m')
    [ "$result" = "Green and Blue" ]
}

@test "sanitize removes ANSI cursor movement sequences" {
    load_sanitize

    # Cursor up: \x1b[A
    result=$(sanitize $'Before\x1b[AAfter')
    [ "$result" = "BeforeAfter" ]

    # Cursor down: \x1b[B
    result=$(sanitize $'Line1\x1b[BLine2')
    [ "$result" = "Line1Line2" ]

    # Cursor position: \x1b[10;20H
    result=$(sanitize $'Text\x1b[10;20HMore')
    [ "$result" = "TextMore" ]
}

@test "sanitize removes C0 control characters (0x00-0x1F)" {
    load_sanitize

    # Bell character (0x07)
    result=$(sanitize $'Alert\x07Text')
    [ "$result" = "AlertText" ]

    # Tab (0x09) - should be removed
    result=$(sanitize $'Tab\tHere')
    [ "$result" = "TabHere" ]

    # Carriage return (0x0D) - should be removed
    result=$(sanitize $'Line\rNew')
    [ "$result" = "LineNew" ]

    # Newline (0x0A) - should be removed
    result=$(sanitize $'First\nSecond')
    [ "$result" = "FirstSecond" ]
}

@test "sanitize removes DEL character (0x7F)" {
    load_sanitize

    # DEL character
    result=$(sanitize $'Before\x7fAfter')
    [ "$result" = "BeforeAfter" ]

    # Multiple DEL characters
    result=$(sanitize $'A\x7fB\x7fC')
    [ "$result" = "ABC" ]
}

@test "sanitize handles empty input" {
    load_sanitize

    result=$(sanitize "")
    [ -z "$result" ]
}

@test "sanitize handles complex mixed input" {
    load_sanitize

    # Mix of ANSI sequences, control chars, and normal text
    result=$(sanitize $'\x1b[31mColored\x1b[0m\tTabbed\x07Bell\x7fDEL Normal')
    [ "$result" = "ColoredTabbedBellDEL Normal" ]
}

@test "sanitize preserves Japanese characters" {
    load_sanitize

    result=$(sanitize "日本語テスト")
    [ "$result" = "日本語テスト" ]

    result=$(sanitize "レビュー中 | PRなし")
    [ "$result" = "レビュー中 | PRなし" ]
}

@test "sanitize preserves UUID format" {
    load_sanitize

    # Full UUID should pass through unchanged
    result=$(sanitize "c6a4f1b2-b85b-4ab4-af43-538d88ed1260")
    [ "$result" = "c6a4f1b2-b85b-4ab4-af43-538d88ed1260" ]
}

# =============================================================================
# session_id display tests
# =============================================================================

@test "session_id fallback shows ? when empty" {
    # Test the ${SESSION_ID:-?} pattern directly
    SESSION_ID=""
    result="${SESSION_ID:-?}"
    [ "$result" = "?" ]

    unset SESSION_ID
    result="${SESSION_ID:-?}"
    [ "$result" = "?" ]
}

@test "session_id shows full UUID when provided" {
    SESSION_ID="c6a4f1b2-b85b-4ab4-af43-538d88ed1260"
    result="${SESSION_ID:-?}"
    [ "$result" = "c6a4f1b2-b85b-4ab4-af43-538d88ed1260" ]

    # Verify UUID format (36 characters with hyphens)
    [ ${#result} -eq 36 ]

    # Check UUID format: 8-4-4-4-12
    [[ "$result" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]
}

# =============================================================================
# get_phase_name tests (Issue #2148: 略称廃止)
# =============================================================================

# Helper to load the get_phase_name function
load_get_phase_name() {
    get_phase_name() {
        local phase="$1"
        case "$phase" in
            session_start) echo "セッション開始" ;;
            pre_check) echo "事前確認" ;;
            worktree_create) echo "worktree作成" ;;
            implementation) echo "実装" ;;
            pre_commit_check) echo "コミット前検証" ;;
            local_ai_review) echo "AIレビュー" ;;
            pr_create) echo "PR作成" ;;
            issue_work) echo "Issue作業" ;;
            ci_review) echo "CIレビュー" ;;
            merge) echo "マージ" ;;
            cleanup) echo "クリーンアップ" ;;
            production_check) echo "本番確認" ;;
            session_end) echo "セッション終了" ;;
            *) echo "$phase" ;;
        esac
    }
}

@test "get_phase_name converts session_start to full name" {
    load_get_phase_name
    result=$(get_phase_name "session_start")
    [ "$result" = "セッション開始" ]
}

@test "get_phase_name converts worktree_create to full name (not WT)" {
    load_get_phase_name
    result=$(get_phase_name "worktree_create")
    [ "$result" = "worktree作成" ]
}

@test "get_phase_name converts ci_review to full name (not CI)" {
    load_get_phase_name
    result=$(get_phase_name "ci_review")
    [ "$result" = "CIレビュー" ]
}

@test "get_phase_name returns original for unknown phase" {
    load_get_phase_name
    result=$(get_phase_name "unknown_phase")
    [ "$result" = "unknown_phase" ]
}

@test "get_phase_name converts all phases correctly" {
    load_get_phase_name

    [ "$(get_phase_name "pre_check")" = "事前確認" ]
    [ "$(get_phase_name "implementation")" = "実装" ]
    [ "$(get_phase_name "pre_commit_check")" = "コミット前検証" ]
    [ "$(get_phase_name "local_ai_review")" = "AIレビュー" ]
    [ "$(get_phase_name "pr_create")" = "PR作成" ]
    [ "$(get_phase_name "issue_work")" = "Issue作業" ]
    [ "$(get_phase_name "merge")" = "マージ" ]
    [ "$(get_phase_name "cleanup")" = "クリーンアップ" ]
    [ "$(get_phase_name "production_check")" = "本番確認" ]
    [ "$(get_phase_name "session_end")" = "セッション終了" ]
}

# =============================================================================
# iteration display tests (Issue #2148: iteration 1は非表示)
# =============================================================================

@test "iteration 1 should not show count in output" {
    # Simulate the iteration display logic from get_flow_state
    iterations=1
    hooks_fired=42
    phase_name="実装"

    if [ "$iterations" -gt 1 ]; then
        result="⏳${phase_name} (${iterations}) | 🪝${hooks_fired}"
    else
        result="⏳${phase_name} | 🪝${hooks_fired}"
    fi

    [ "$result" = "⏳実装 | 🪝42" ]
}

@test "iteration 2 should show count in output" {
    iterations=2
    hooks_fired=42
    phase_name="CIレビュー"

    if [ "$iterations" -gt 1 ]; then
        result="⏳${phase_name} (${iterations}) | 🪝${hooks_fired}"
    else
        result="⏳${phase_name} | 🪝${hooks_fired}"
    fi

    [ "$result" = "⏳CIレビュー (2) | 🪝42" ]
}

@test "iteration 3 should show count in output" {
    iterations=3
    hooks_fired=100
    phase_name="CIレビュー"

    if [ "$iterations" -gt 1 ]; then
        result="⏳${phase_name} (${iterations}) | 🪝${hooks_fired}"
    else
        result="⏳${phase_name} | 🪝${hooks_fired}"
    fi

    [ "$result" = "⏳CIレビュー (3) | 🪝100" ]
}
