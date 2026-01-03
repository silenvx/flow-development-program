#!/bin/bash
# lefthookのインストール状態を確認する。
#
# Why:
#     lefthookが正しくセットアップされているか確認し、
#     pre-push等のフックが有効になっていることを保証するため。
#
# What:
#     - pre-pushフックファイルの存在確認
#     - lefthook管理フックかどうかの判定
#
# Remarks:
#     - Exit 0: 確認完了（警告メッセージは出力される場合あり）
#     - worktree対応（git rev-parse --git-common-dir使用）
#
# Changelog:
#     - silenvx/dekita#130: lefthook確認スクリプトを追加

set -euo pipefail

# Get the actual hooks directory (works for both regular repos and worktrees)
GIT_COMMON_DIR=$(git rev-parse --git-common-dir 2>/dev/null || echo ".git")
HOOKS_DIR="$GIT_COMMON_DIR/hooks"

# Check if pre-push hook exists
if [[ ! -f "$HOOKS_DIR/pre-push" ]]; then
    echo "⚠️  Lefthook pre-push hook is not installed."
    echo ""
    echo "Run one of the following to set up:"
    echo "  make setup        # Installs lefthook and hooks"
    echo "  pnpm install      # If working on frontend/worker"
    echo "  lefthook install  # If lefthook is already installed"
    exit 0
fi

# Check if it's actually a lefthook hook (not some other hook)
if ! grep -q "lefthook" "$HOOKS_DIR/pre-push" 2>/dev/null; then
    echo "⚠️  pre-push hook exists but is not managed by lefthook."
    echo ""
    echo "Run 'lefthook install' to set up lefthook hooks."
    exit 0
fi

# All good - no output needed
exit 0
