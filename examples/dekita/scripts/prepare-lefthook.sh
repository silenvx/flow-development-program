#!/bin/bash
# lefthook installをメインリポジトリから実行する。
#
# Why:
#     worktreeでpnpm install時にlefthookがworktreeパスを
#     ハードコードする問題を回避するため。
#
# What:
#     - メインリポジトリのパスを検出
#     - メインリポジトリのlefthookバイナリを使用
#     - メインリポジトリで lefthook install を実行
#
# Remarks:
#     - pnpm postinstallから呼び出される
#     - worktree内からでも正しいパスで動作
#
# Changelog:
#     - silenvx/dekita#958: worktreeでのlefthook問題を回避

set -e

# メインリポジトリのパスを取得
# git rev-parse --git-common-dir は .git または .git/worktrees/xxx を返す
# 相対パスの場合があるため、絶対パスに変換
GIT_COMMON_DIR=$(cd "$(git rev-parse --git-common-dir)" && pwd)

# worktree判定: .git/worktrees/<name> 形式かどうか
# パターンマッチングではなく、ディレクトリ構造で判定
WORKTREES_PARENT=$(dirname "$GIT_COMMON_DIR")
if [[ "$(basename "$WORKTREES_PARENT")" == "worktrees" ]]; then
    # worktree内の場合: /path/to/.git/worktrees/xxx → /path/to/.git → /path/to
    GIT_DIR=$(dirname "$WORKTREES_PARENT")
    MAIN_REPO=$(dirname "$GIT_DIR")
else
    # メインリポジトリの場合: /path/to/.git → /path/to
    MAIN_REPO=$(dirname "$GIT_COMMON_DIR")
fi

# メインリポジトリのnode_modules/.bin/lefthookを使用
LEFTHOOK_BIN="$MAIN_REPO/node_modules/.bin/lefthook"

if [[ -x "$LEFTHOOK_BIN" ]]; then
    echo "Installing lefthook from main repo: $MAIN_REPO"
    if ! cd "$MAIN_REPO"; then
        echo "Error: Failed to change directory to $MAIN_REPO" >&2
        exit 1
    fi
    "$LEFTHOOK_BIN" install
else
    echo "Error: lefthook not found at $LEFTHOOK_BIN" >&2
    echo "Run 'pnpm install' in the main repository first." >&2
    exit 1
fi
