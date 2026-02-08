#!/bin/bash
# TypeScriptフックの依存関係をチェックし、不足時は自動インストールする。
#
# Why:
#     TypeScriptフック（gemini_review_check.ts等）はnpm依存関係（zod等）を
#     必要とする。node_modulesが存在しないと実行時エラーになり、フックが
#     サイレントに失敗する問題（Issue #2885）を防ぐため。
#
# What:
#     - .claude/hooks/node_modules の存在確認
#     - 不足時は bun install を自動実行
#     - bunが未インストールの場合は警告
#
# Remarks:
#     - Exit 0: 常に成功（警告・自動修復のみ）
#     - SessionStartフックとして実行される
#
# Changelog:
#     - silenvx/dekita#2885: TypeScriptフック依存関係チェックを追加

set -euo pipefail

# Get project directory (from CLAUDE_PROJECT_DIR or fallback to script location)
if [[ -n "${CLAUDE_PROJECT_DIR:-}" ]]; then
    PROJECT_DIR="$CLAUDE_PROJECT_DIR"
else
    # Fallback: script is in scripts/, so project root is parent
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
fi

TS_HOOKS_DIR="$PROJECT_DIR/.claude/hooks"
NODE_MODULES_DIR="$TS_HOOKS_DIR/node_modules"

# Check if TypeScript hooks directory exists
if [[ ! -d "$TS_HOOKS_DIR" ]]; then
    # No TypeScript hooks in this project, nothing to check
    exit 0
fi

# Check if package.json exists
if [[ ! -f "$TS_HOOKS_DIR/package.json" ]]; then
    # No package.json, nothing to install
    exit 0
fi

# Check if node_modules exists and is not empty
if [[ -d "$NODE_MODULES_DIR" && -n "$(ls -A "$NODE_MODULES_DIR" 2>/dev/null)" ]]; then
    # Check if package.json and bun.lock (if exists) are older than node_modules
    # to detect stale dependencies
    if [[ "$TS_HOOKS_DIR/package.json" -ot "$NODE_MODULES_DIR" ]] && \
       [[ ! -f "$TS_HOOKS_DIR/bun.lock" || "$TS_HOOKS_DIR/bun.lock" -ot "$NODE_MODULES_DIR" ]]; then
        # Dependencies installed and up-to-date
        exit 0
    fi
    # If package.json or bun.lock is newer, proceed to install
fi

# Dependencies missing or stale - attempt auto-install
echo "⚠️  TypeScriptフックの依存関係がインストールされていないか、更新が必要です。" >&2
echo "   場所: $TS_HOOKS_DIR/node_modules" >&2
echo "" >&2

# Check if bun is available
if ! command -v bun &> /dev/null; then
    echo "❌ bunがインストールされていません。" >&2
    echo "" >&2
    echo "以下のいずれかを実行してください:" >&2
    echo "  1. bunをインストール: https://bun.sh" >&2
    echo "  2. 手動でインストール: cd $TS_HOOKS_DIR && bun install" >&2
    echo "" >&2
    echo "⚠️  TypeScriptフック（gemini_review_check.ts等）は動作しません。" >&2
    exit 0
fi

# Attempt auto-install
echo "🔧 依存関係を自動インストールします..." >&2
if (cd "$TS_HOOKS_DIR" && bun install --frozen-lockfile); then
    echo "✅ TypeScriptフック依存関係のインストール完了" >&2
else
    echo "❌ インストールに失敗しました。" >&2
    echo "" >&2
    echo "手動で実行してください:" >&2
    echo "  cd $TS_HOOKS_DIR && bun install" >&2
fi

exit 0
