#!/bin/bash
# Git hooksのセットアップ（シンボリックリンク作成）。
#
# Why:
#     pre-push等のGit hooksを有効化し、
#     CIで検知される前にローカルでエラーを検出するため。
#
# What:
#     - scripts/内のhookを.git/hooks/にシンボリックリンク
#     - worktree対応（git rev-parse --git-path hooks使用）
#
# Remarks:
#     - Usage: ./scripts/setup-hooks.sh
#     - clone後に1回実行すればOK
#     - 既存のシンボリックリンクは上書き
#
# Changelog:
#     - silenvx/dekita#100: Git hooks自動セットアップを追加

set -euo pipefail

# 色付き出力（ターミナルの場合のみ）
if [[ -t 1 ]]; then
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    NC='\033[0m'
else
    GREEN=''
    YELLOW=''
    NC=''
fi

# プロジェクトルートに移動
cd "$(git rev-parse --show-toplevel)"

SCRIPTS_DIR="$(pwd)/scripts"
# worktree対応: git rev-parse で実際のhooksディレクトリを取得
HOOKS_DIR="$(git rev-parse --git-path hooks)"

# hooksディレクトリが存在しない場合は作成
if [[ ! -d "$HOOKS_DIR" ]]; then
    mkdir -p "$HOOKS_DIR"
fi

# セットアップするhookのリスト（拡張子なし）
HOOKS=("pre-push")

echo -e "${YELLOW}Setting up Git hooks...${NC}"
echo ""

for hook in "${HOOKS[@]}"; do
    src="$SCRIPTS_DIR/${hook}.sh"
    dest="$HOOKS_DIR/$hook"

    if [[ ! -f "$src" ]]; then
        echo "Warning: $src not found, skipping..."
        continue
    fi

    # 既存のhookがある場合はバックアップ
    if [[ -f "$dest" && ! -L "$dest" ]]; then
        backup="${dest}.backup.$(date +%Y%m%d%H%M%S)"
        echo "Backing up existing $hook to $backup"
        mv "$dest" "$backup"
    fi

    # ソースファイルに実行権限を付与（シンボリックリンク作成前に）
    chmod +x "$src"

    # シンボリックリンクを作成（絶対パスで）
    if [[ -L "$dest" ]]; then
        rm "$dest"
    fi
    ln -s "$src" "$dest"

    echo -e "${GREEN}OK: Installed $hook hook${NC}"
done

echo ""
echo -e "${GREEN}Git hooks setup complete!${NC}"
echo ""
echo "Installed hooks:"
for hook in "${HOOKS[@]}"; do
    echo "  - $hook: runs lint and typecheck before push"
done
echo ""
echo "To skip hooks temporarily: git push --no-verify"
