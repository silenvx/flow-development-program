#!/usr/bin/env bash
# TypeScriptフックのBun実行テスト
#
# Why:
#   Issue #2933: JSDocコメント内のglob風パターン（*/.）がBunでパースエラーになる問題があった。
#   構文チェック（typecheck）では検出できず、実際にBunで実行しないと発覚しない。
#
# What:
#   各TypeScriptフックを実際にBunで実行し、パースエラーがないか確認する。
#   空の入力（{}）を与えて即座に終了させ、パースが成功することのみを検証。
#
# Usage:
#   ./check_ts_hook_execution.sh [--verbose]
#
# Note:
#   - フックの実行結果（approve/block）は検証しない（パースのみ）
#   - 入力不足によるエラー（decision出力なし）は許容
#   - Bunのパースエラー（SyntaxError等）は失敗
#
# Skip list:
#   既知の問題があるフックをスキップするには、SKIP_HOOKS環境変数にカンマ区切りで指定:
#   SKIP_HOOKS="branch_rename_guard,checkout_block" ./check_ts_hook_execution.sh
#
#   または .claude/scripts/check_ts_hook_execution_skip.txt にフック名を1行1つで記載

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERBOSE="${1:-}"
SKIP_FILE="$SCRIPT_DIR/check_ts_hook_execution_skip.txt"
TIMEOUT_SECONDS=10

# Build skip list from env var and file
SKIP_LIST=""
if [[ -n "${SKIP_HOOKS:-}" ]]; then
    SKIP_LIST="$SKIP_HOOKS"
fi
if [[ -f "$SKIP_FILE" ]]; then
    # Read file, ignore comments and empty lines
    while IFS= read -r line || [[ -n "$line" ]]; do
        line=$(echo "$line" | sed 's/#.*//' | tr -d ' ')
        if [[ -n "$line" ]]; then
            if [[ -n "$SKIP_LIST" ]]; then
                SKIP_LIST="$SKIP_LIST,$line"
            else
                SKIP_LIST="$line"
            fi
        fi
    done < "$SKIP_FILE"
fi

# Check if bun is available
if ! command -v bun &> /dev/null; then
    echo "Error: bun is not installed"
    exit 1
fi

# Change to hooks directory for proper imports
cd "$SCRIPT_DIR/../hooks"

# Get list of hook files
HOOK_FILES=$(find handlers -name "*.ts" -type f | sort)
TOTAL=$(echo "$HOOK_FILES" | wc -l | tr -d ' ')
PASSED=0
FAILED=0
SKIPPED=0
FAILED_LIST=""
SKIPPED_LIST=""

echo "Testing $TOTAL TypeScript hooks for Bun execution..."
if [[ -n "$SKIP_LIST" ]]; then
    echo "Skip list: $SKIP_LIST"
fi
echo ""

for hook_file in $HOOK_FILES; do
    hook_name=$(basename "$hook_file" .ts)

    # Check if hook is in skip list
    if echo ",$SKIP_LIST," | grep -q ",$hook_name,"; then
        SKIPPED=$((SKIPPED + 1))
        SKIPPED_LIST="$SKIPPED_LIST\n  - $hook_name"
        if [[ "$VERBOSE" == "--verbose" ]]; then
            echo "SKIP: $hook_name"
        fi
        continue
    fi

    # Run hook with empty input and capture stderr
    # We expect the hook to fail due to missing input, but should not have parse errors
    set +e
    output=$(echo '{}' | timeout "$TIMEOUT_SECONDS" bun run "$hook_file" 2>&1)
    hook_exit=$?
    set -e

    # Check for timeout (exit 124) or Bun parse/load errors
    # Timeout indicates the hook hung, which is a failure
    # Parse/load errors contain specific error patterns from Bun:
    # - "error:" - Bun's parse error format
    # - "SyntaxError" - includes parse errors AND missing export errors
    # - "ReferenceError" - runtime errors during module load
    # - "TypeError" - type errors during module load
    # - "Unexpected" - Bun's unexpected token errors
    if [[ $hook_exit -eq 124 ]]; then
        # Timeout - hook hung
        FAILED=$((FAILED + 1))
        FAILED_LIST="$FAILED_LIST\n  - $hook_file (TIMEOUT)"
        echo "FAIL: $hook_name (TIMEOUT - hook hung for ${TIMEOUT_SECONDS}+ seconds)"
    elif echo "$output" | grep -qE "^error:|SyntaxError|ReferenceError|TypeError|Unexpected|at .*$hook_file:[0-9]+"; then
        FAILED=$((FAILED + 1))
        FAILED_LIST="$FAILED_LIST\n  - $hook_file"
        if [[ "$VERBOSE" == "--verbose" ]]; then
            echo "FAIL: $hook_name"
            echo "  Output: $output"
        else
            echo "FAIL: $hook_name"
        fi
    else
        PASSED=$((PASSED + 1))
        if [[ "$VERBOSE" == "--verbose" ]]; then
            echo "PASS: $hook_name"
        fi
    fi
done

echo ""
echo "Results: $PASSED passed, $FAILED failed, $SKIPPED skipped (of $TOTAL)"

if [[ $SKIPPED -gt 0 ]]; then
    echo ""
    echo -e "Skipped hooks (known issues):$SKIPPED_LIST"
fi

if [[ $FAILED -gt 0 ]]; then
    echo ""
    echo -e "Failed hooks:$FAILED_LIST"
    echo ""
    echo "Note: These hooks have Bun execution errors. Check for:"
    echo "  - JSDoc comments with glob-like patterns (e.g., */.)"
    echo "  - Missing exports in lib modules"
    echo "  - Syntax errors in TypeScript code"
    exit 1
fi

echo ""
echo "All tested hooks passed Bun execution test."
