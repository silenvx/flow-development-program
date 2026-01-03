#!/usr/bin/env bash
# Worktree作成後の自動セットアップ。
#
# Why:
#     worktree作成後に依存関係インストール等の初期化を
#     自動実行し、作業開始までの手順を簡略化するため。
#
# What:
#     - pnpm install: package.json存在時に依存インストール
#     - uvx連携: pyproject.toml存在時はuvxに委譲
#
# Remarks:
#     - Usage: .claude/scripts/setup-worktree.sh <worktree-path>
#     - CI環境変数を自動設定（対話プロンプト抑制）
#     - 相対パスは自動で絶対パスに変換
#
# Changelog:
#     - silenvx/dekita#1170: worktree初期化自動化機能を追加
#     - silenvx/dekita#1179: 相対パスの絶対パス変換を追加

set -euo pipefail

WORKTREE_PATH="${1:-}"

if [ -z "$WORKTREE_PATH" ]; then
    echo "Usage: $0 <worktree-path>" >&2
    echo "Example: $0 .worktrees/issue-123" >&2
    exit 1
fi

# Check existence first (before path conversion)
if [ ! -d "$WORKTREE_PATH" ]; then
    echo "Error: Worktree path does not exist: $WORKTREE_PATH" >&2
    exit 1
fi

# Issue #1179: Convert relative path to absolute path for reliable operation
# Safe to call cd && pwd now since we've verified the path exists
if [[ "$WORKTREE_PATH" != /* ]]; then
    WORKTREE_PATH="$(cd "$WORKTREE_PATH" && pwd)"
fi

echo "Setting up worktree: $WORKTREE_PATH"

# Change to worktree directory
cd "$WORKTREE_PATH"

# Check if package.json exists (Node.js project)
if [ -f "package.json" ]; then
    if ! command -v pnpm &> /dev/null; then
        echo "Error: pnpm is not installed" >&2
        exit 1
    fi
    echo "Installing Node.js dependencies..."
    # CIが未設定の場合のみCI=trueを設定し、対話プロンプトを抑制する
    CI="${CI:-true}" pnpm install
    echo "Node.js dependencies installed."
fi

# Check if pyproject.toml exists (Python project)
if [ -f "pyproject.toml" ]; then
    echo "Python project detected. Dependencies managed by uvx."
fi

echo ""
echo "Worktree setup complete: $WORKTREE_PATH"
echo "You can now work in this worktree."
