#!/bin/bash
# Skill品質チェックスクリプト
# 使用方法: ./check-skill.sh <SKILL.mdのパス>
#
# 例: ./check-skill.sh .claude/skills/my-skill/SKILL.md

set -e

SKILL_FILE="$1"

if [ -z "$SKILL_FILE" ]; then
  echo "使用方法: $0 <SKILL.mdのパス>"
  exit 1
fi

if [ ! -f "$SKILL_FILE" ]; then
  echo "❌ エラー: ファイルが見つかりません: $SKILL_FILE"
  exit 1
fi

echo "🔍 Skill品質チェック: $SKILL_FILE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

ERRORS=0
WARNINGS=0

# 1. 行数チェック
LINE_COUNT=$(awk 'END {print NR}' "$SKILL_FILE")
if [ "$LINE_COUNT" -gt 1000 ]; then
  echo "❌ 行数: ${LINE_COUNT}行（1000行超 - 分割必須）"
  ERRORS=$((ERRORS + 1))
elif [ "$LINE_COUNT" -gt 500 ]; then
  echo "⚠️ 行数: ${LINE_COUNT}行（500行超 - 分割を検討）"
  WARNINGS=$((WARNINGS + 1))
else
  echo "✅ 行数: ${LINE_COUNT}行"
fi

# 2. YAML frontmatter存在チェック
if head -n 1 -- "$SKILL_FILE" | grep -q "^---$"; then
  echo "✅ YAML frontmatter: あり"
else
  echo "❌ YAML frontmatter: なし（先頭に---が必要）"
  ERRORS=$((ERRORS + 1))
fi

# 3. name存在チェック（YAML frontmatterブロックのみ抽出）
# frontmatterの終了タグ存在チェック（先頭50行以内に限定）
# Note: ファイル全体で---をカウントすると、markdown horizontal ruleを誤検出する
# Note: if ! ... を使用することでset -e下でも終了コードを正しく処理
if ! head -n 50 -- "$SKILL_FILE" | awk '
  NR == 1 && /^---$/ { start = 1; next }
  start && /^---$/ { found = 1; exit }
  END { exit (found ? 0 : 1) }
'; then
  echo "❌ YAML frontmatter: 終了タグ(---)が見つかりません（先頭50行以内に必要）"
  exit 1
fi
FRONTMATTER=$(head -n 50 -- "$SKILL_FILE" | awk '/^---$/ { i++; next } i==1 { print } i>=2 { exit }')

if printf '%s\n' "$FRONTMATTER" | grep -q "^name:"; then
  NAME=$(printf '%s\n' "$FRONTMATTER" | grep "^name:" | head -n 1 | sed 's/^name: *//; s/^"//; s/"$//; s/^'"'"'//; s/'"'"'$//')
  NAME_LEN=${#NAME}

  if [ -z "$NAME" ]; then
    echo "❌ name: 空欄（値を指定してください）"
    ERRORS=$((ERRORS + 1))
  elif [ "$NAME_LEN" -gt 64 ]; then
    echo "❌ name: ${NAME}（${NAME_LEN}文字 - 64文字以内）"
    ERRORS=$((ERRORS + 1))
  elif printf '%s\n' "$NAME" | grep -qE '[^a-z0-9-]'; then
    echo "❌ name: ${NAME}（小文字・数字・ハイフンのみ許可）"
    ERRORS=$((ERRORS + 1))
  elif printf '%s\n' "$NAME" | grep -qiE 'anthropic|claude'; then
    echo "❌ name: ${NAME}（'anthropic'/'claude'は禁止）"
    ERRORS=$((ERRORS + 1))
  else
    echo "✅ name: ${NAME}"
  fi
else
  echo "❌ name: なし"
  ERRORS=$((ERRORS + 1))
fi

# 4. description存在チェック（frontmatter内のみ検索）
# Note: 簡易チェックのため、YAMLの複数行記法（| や >）は正しく判定できない
if printf '%s\n' "$FRONTMATTER" | grep -q "^description:"; then
  # description行を取得（1行descriptionを想定、引用符除去）
  DESC=$(printf '%s\n' "$FRONTMATTER" | grep "^description:" | head -n 1 | sed 's/^description: *//; s/^"//; s/"$//; s/^'"'"'//; s/'"'"'$//')
  DESC_LEN=${#DESC}

  if [ "$DESC_LEN" -gt 1024 ]; then
    echo "❌ description: ${DESC_LEN}文字（1024文字以内）"
    ERRORS=$((ERRORS + 1))
  elif [ "$DESC_LEN" -eq 0 ]; then
    echo "❌ description: 空欄（値を指定してください）"
    ERRORS=$((ERRORS + 1))
  elif [ "$DESC_LEN" -lt 10 ]; then
    echo "⚠️ description: ${DESC_LEN}文字（短すぎる可能性）"
    WARNINGS=$((WARNINGS + 1))
  else
    echo "✅ description: ${DESC_LEN}文字"
  fi

  # 第三者視点チェック（「する」で終わる日本語は第一人称的）
  if printf '%s\n' "$DESC" | grep -qE 'する。$|します。$'; then
    echo "⚠️ description: 第一人称的な表現（第三者視点を推奨）"
    WARNINGS=$((WARNINGS + 1))
  fi

  # "Use when"チェック
  if printf '%s\n' "$DESC" | grep -qi "use when"; then
    echo "✅ description: 'Use when'トリガー条件あり"
  else
    echo "⚠️ description: 'Use when'トリガー条件なし（推奨）"
    WARNINGS=$((WARNINGS + 1))
  fi
else
  echo "❌ description: なし"
  ERRORS=$((ERRORS + 1))
fi

# 5. 時間依存情報チェック（「最終更新」「Last Updated」行は除外）
# 「以降」「以前」「〜年」などの表現を検出（日付形式YYYY-MM-DDは除外）
if grep -vE "最終更新|Last [Uu]pdated" -- "$SKILL_FILE" | grep -qE '以降|以前|[0-9]{4}年'; then
  echo "⚠️ 時間依存情報: 検出（避けることを推奨）"
  WARNINGS=$((WARNINGS + 1))
else
  echo "✅ 時間依存情報: なし"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "結果: エラー ${ERRORS}件、警告 ${WARNINGS}件"

if [ "$ERRORS" -gt 0 ]; then
  echo ""
  echo "❌ 品質チェック失敗。エラーを修正してください。"
  exit 1
elif [ "$WARNINGS" -gt 0 ]; then
  echo ""
  echo "⚠️ 品質チェック通過（警告あり）。改善を検討してください。"
  exit 0
else
  echo ""
  echo "✅ 品質チェック通過。"
  exit 0
fi
