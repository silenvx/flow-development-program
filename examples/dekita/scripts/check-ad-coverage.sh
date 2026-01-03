#!/bin/bash
# 広告配置の網羅性をチェックする。
#
# Why:
#     全ページに広告が適切に配置されているか検証し、
#     広告収益の漏れを防ぐため。
#
# What:
#     - routes/配下のページファイルをスキャン
#     - AdBannerコンポーネントの存在確認
#     - 除外ページ（privacy/terms）の確認
#
# Remarks:
#     - Exit 0: 全ページ適切、Exit 1: 問題あり
#     - privacy.tsx/terms.tsxには広告配置しない
#     - __root.tsx（ルートレイアウト）は除外
#
# Changelog:
#     - silenvx/dekita#140: 広告網羅性チェックを追加

set -euo pipefail

ROUTES_DIR="frontend/src/routes"

# 広告を配置しないページ（プライバシーポリシー、利用規約）
EXCLUDED_PAGES=("privacy.tsx" "terms.tsx")

# チェック対象から除外するファイル（レイアウトファイル等）
LAYOUT_FILES=("__root.tsx")

errors=0

# ディレクトリが存在するか確認
if [[ ! -d "$ROUTES_DIR" ]]; then
    echo "Error: Routes directory not found: $ROUTES_DIR"
    exit 1
fi

echo "Checking ad coverage in $ROUTES_DIR..."
echo ""

# 再帰的にtsxファイルを検索
while IFS= read -r -d '' file; do
    filename=$(basename "$file")
    relative_path="${file#"$ROUTES_DIR"/}"

    # レイアウトファイルは除外（全てのディレクトリレベルで）
    # このプロジェクトでは __root.tsx は広告を含まないレイアウトファイル
    is_layout=false
    for layout in "${LAYOUT_FILES[@]}"; do
        if [[ "$filename" == "$layout" ]]; then
            is_layout=true
            break
        fi
    done
    if $is_layout; then
        continue
    fi

    # 広告禁止ページかどうかをチェック（ルートレベルのみ対象）
    # サブディレクトリに同名ファイルがあっても誤検知しないよう完全パス比較
    is_excluded=false
    for excluded in "${EXCLUDED_PAGES[@]}"; do
        if [[ "$relative_path" == "$excluded" ]]; then
            is_excluded=true
            break
        fi
    done

    # ファイル内のAdBanner使用をチェック
    # JSXで実際に使用されているかを確認（<AdBanner /> または <AdBanner> の形式）
    # Note: コメント内や文字列内のマッチも理論上は可能だが、
    #       このプロジェクトではそのようなケースは存在しないため許容する
    has_adbanner=false
    if grep -qE '<AdBanner[[:space:]/>]' "$file"; then
        has_adbanner=true
    fi

    if $is_excluded; then
        # 広告禁止ページに広告がある場合はエラー
        if $has_adbanner; then
            echo "ERROR: $relative_path contains AdBanner but should not (privacy/terms page)"
            ((errors++))
        else
            echo "OK: $relative_path (no ads - expected)"
        fi
    else
        # 通常ページに広告がない場合はエラー
        if $has_adbanner; then
            echo "OK: $relative_path (has AdBanner)"
        else
            echo "ERROR: $relative_path is missing AdBanner"
            ((errors++))
        fi
    fi
done < <(find "$ROUTES_DIR" -name "*.tsx" -type f -print0)

echo ""
if [[ $errors -gt 0 ]]; then
    echo "Found $errors error(s) in ad coverage check."
    echo ""
    echo "To fix:"
    echo "  - Add <AdBanner /> to pages that should have ads"
    echo "  - Remove <AdBanner /> from privacy.tsx and terms.tsx"
    exit 1
else
    echo "All pages have correct ad coverage."
    exit 0
fi
