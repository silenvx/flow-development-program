#!/bin/bash
# Vite環境変数がワークフローで定義されているか検証する。
#
# Why:
#     Vite環境変数はビルド時に埋め込まれるため、
#     ワークフローでの定義漏れを事前に検出するため。
#
# What:
#     - フロントエンドコードからVITE_*変数を抽出
#     - _deploy.ymlでの定義を確認
#     - 未定義変数を報告
#
# Remarks:
#     - Usage: ./scripts/check-vite-env.sh
#     - Exit 0: 全定義済み、Exit 1: 未定義あり
#     - wrangler pages secret putでは反映されない点に注意
#
# Changelog:
#     - silenvx/dekita#147: Vite環境変数チェックを追加

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

FRONTEND_DIR="$ROOT_DIR/frontend"
DEPLOY_WORKFLOW="$ROOT_DIR/.github/workflows/_deploy.yml"

# エラーカウンター
errors=0

echo "🔍 Vite環境変数の整合性をチェック中..."
echo ""

# フロントエンドコードで使用されているVITE_*変数を抽出
# import.meta.env.VITE_XXX のパターンを検索
used_vars=$(grep -rhoE 'import\.meta\.env\.VITE_[A-Z_]+' "$FRONTEND_DIR/src" 2>/dev/null | \
  sed 's/import\.meta\.env\.//' | \
  sort -u || true)

if [ -z "$used_vars" ]; then
  echo "✅ フロントエンドコードでVite環境変数が使用されていません"
  exit 0
fi

echo "📋 フロントエンドで使用されているVite環境変数:"
for var in $used_vars; do
  echo "   - $var"
done
echo ""

# _deploy.ymlの「Build frontend」ステップのenvセクションで定義されている環境変数を抽出
# 1. 「Build frontend」から「Deploy to」までの行を抽出
# 2. その中のVITE_*変数名を抽出
build_env_section=$(sed -n '/name: Build frontend/,/name: Deploy/p' "$DEPLOY_WORKFLOW" 2>/dev/null || true)
workflow_vars=$(echo "$build_env_section" | grep -oE 'VITE_[A-Z_]+:' 2>/dev/null | \
  sed 's/://' | \
  sort -u || true)

echo "📋 ビルドステップのenvで定義されているVite環境変数:"
if [ -z "$workflow_vars" ]; then
  echo "   (なし)"
else
  for var in $workflow_vars; do
    echo "   - $var"
  done
fi
echo ""

# 未定義の変数をチェック
echo "🔎 チェック結果:"
for var in $used_vars; do
  if echo "$workflow_vars" | grep -q "^${var}$"; then
    echo "   ✅ $var - ビルドステップで定義済み"
  else
    echo "   ❌ $var - ビルドステップで未定義"
    errors=$((errors + 1))
  fi
done
echo ""

if [ $errors -gt 0 ]; then
  echo "❌ 未定義のVite環境変数が $errors 件あります"
  echo ""
  echo "📝 対処方法:"
  echo "   1. .github/workflows/_deploy.yml の secrets: セクションに追加"
  echo "   2. .github/workflows/_deploy.yml の「Build frontend」ステップの env: に追加"
  echo "   3. .github/workflows/ci.yml の deploy ジョブで secrets を渡す"
  echo "   4. GitHub Secrets に値を設定 (gh secret set VITE_XXX)"
  echo ""
  echo "⚠️  注意: Vite環境変数はビルド時に埋め込まれます。"
  echo "   wrangler pages secret put ではフロントエンドに反映されません。"
  echo "   secrets/inputs に追加しただけでは不十分です。"
  echo "   必ず Build frontend ステップの env: にも追加してください。"
  exit 1
else
  echo "✅ 全てのVite環境変数がビルドステップで定義されています"
  exit 0
fi
