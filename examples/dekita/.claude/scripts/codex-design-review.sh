#!/bin/bash
# Codex CLIで設計品質重視のコードレビューを実行する。
#
# Why:
#     結合度・凝集度・単一責任原則の観点で
#     自動コードレビューを行い、設計品質を向上させるため。
#
# What:
#     - codex review: 設計品質観点でレビュー実行
#     - --security: セキュリティ観点に集中
#     - --coupling: 結合度観点に集中
#     - --cohesion: 凝集度観点に集中
#
# Remarks:
#     - design-review-prompt.txtからプロンプトを読み込む
#     - --uncommitted: 未コミット変更をレビュー
#     - --base <branch>: 比較対象ブランチを指定（デフォルト: main）
#
# Changelog:
#     - silenvx/dekita#1500: 設計品質レビュー機能を追加

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPT_FILE="$SCRIPT_DIR/../docs/design-review-prompt.txt"

# Default values
BASE_BRANCH="main"
REVIEW_MODE="base"
EXTRA_INSTRUCTIONS=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --uncommitted)
            REVIEW_MODE="uncommitted"
            shift
            ;;
        --base)
            if [[ $# -lt 2 ]]; then
                echo "Error: --base requires a branch name argument" >&2
                echo "Use --help for usage information" >&2
                exit 1
            fi
            if [[ "$2" == -* ]]; then
                echo "Error: Invalid branch name '$2'. Branch names cannot start with a hyphen." >&2
                exit 1
            fi
            BASE_BRANCH="$2"
            shift 2
            ;;
        --security)
            EXTRA_INSTRUCTIONS="Focus heavily on security vulnerabilities. Check for: authentication bypass, authorization flaws, input validation, injection attacks, sensitive data exposure, insecure configurations."
            shift
            ;;
        --coupling)
            EXTRA_INSTRUCTIONS="Focus on coupling and dependency issues. Check for: tight coupling, circular dependencies, god classes, excessive dependencies, missing dependency injection."
            shift
            ;;
        --cohesion)
            EXTRA_INSTRUCTIONS="Focus on cohesion and module organization. Check for: low cohesion modules, scattered functionality, utility bloat, mixed responsibilities."
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --uncommitted    Review uncommitted changes (git diff)"
            echo "  --base <branch>  Review against specific branch (default: main)"
            echo "  --security       Focus on security vulnerabilities"
            echo "  --coupling       Focus on coupling and dependency issues"
            echo "  --cohesion       Focus on cohesion and module organization"
            echo "  --help, -h       Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0                    # Review current branch vs main"
            echo "  $0 --security         # Security-focused review"
            echo "  $0 --uncommitted      # Review uncommitted changes"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Use --help for usage information" >&2
            exit 1
            ;;
    esac
done

# Check if codex is available
if ! command -v codex &>/dev/null; then
    echo "Error: codex CLI is not installed or not in PATH" >&2
    echo "Please install the codex CLI and ensure it is available on your PATH." >&2
    echo "Refer to your project or internal documentation for installation instructions." >&2
    exit 1
fi

# Check if prompt file exists
if [[ ! -f "$PROMPT_FILE" ]]; then
    echo "Error: Prompt file not found: $PROMPT_FILE" >&2
    exit 1
fi

# Build instructions
INSTRUCTIONS=$(cat "$PROMPT_FILE")
if [[ -n "$EXTRA_INSTRUCTIONS" ]]; then
    INSTRUCTIONS="$INSTRUCTIONS

## Additional Focus
$EXTRA_INSTRUCTIONS"
fi

# Run codex review
echo "Running Codex design review..."
echo "Mode: $REVIEW_MODE"
if [[ "$REVIEW_MODE" == "base" ]]; then
    echo "Base branch: $BASE_BRANCH"
fi
echo ""

if [[ "$REVIEW_MODE" == "uncommitted" ]]; then
    codex review --uncommitted --instructions "$INSTRUCTIONS"
else
    codex review --base "$BASE_BRANCH" --instructions "$INSTRUCTIONS"
fi

echo ""
echo "Review complete."
