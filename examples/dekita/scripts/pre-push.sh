#!/bin/bash
# push前にlintとtypecheckを実行するpre-push hook。
#
# Why:
#     CIで検知される前にローカルでエラーを検出し、
#     プッシュ後の手戻りを防ぐため。
#
# What:
#     - pnpm lint: ESLintチェック
#     - pnpm typecheck: TypeScriptの型チェック
#
# Remarks:
#     - セットアップ: ./scripts/setup-hooks.sh
#     - 失敗時はpushがブロックされる
#     - 参考: https://git-scm.com/book/en/v2/Customizing-Git-Git-Hooks
#
# Changelog:
#     - silenvx/dekita#110: pre-push hookを追加

set -euo pipefail

# 色付き出力（ターミナルの場合のみ）
if [[ -t 1 ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    NC='\033[0m' # No Color
else
    RED=''
    GREEN=''
    YELLOW=''
    NC=''
fi

echo -e "${YELLOW}Running pre-push checks...${NC}"
echo ""

# プロジェクトルートに移動
cd "$(git rev-parse --show-toplevel)"

# pnpmがインストールされているか確認
if ! command -v pnpm &> /dev/null; then
    echo -e "${RED}Error: pnpm is not installed${NC}"
    echo "Please install pnpm first: corepack enable"
    exit 1
fi

errors=0

# 1. Lint check
echo -e "${YELLOW}[1/2] Running lint...${NC}"
if pnpm lint; then
    echo -e "${GREEN}OK: Lint passed${NC}"
else
    echo -e "${RED}ERROR: Lint failed${NC}"
    errors=$((errors + 1))
fi
echo ""

# 2. Type check
echo -e "${YELLOW}[2/2] Running typecheck...${NC}"
if pnpm typecheck; then
    echo -e "${GREEN}OK: Typecheck passed${NC}"
else
    echo -e "${RED}ERROR: Typecheck failed${NC}"
    errors=$((errors + 1))
fi
echo ""

# 結果サマリー
if [[ $errors -gt 0 ]]; then
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${RED}Pre-push check failed. Push aborted.${NC}"
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "Fix the errors above and try again."
    echo "To skip this check (not recommended): git push --no-verify"
    exit 1
else
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}All pre-push checks passed!${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    exit 0
fi
