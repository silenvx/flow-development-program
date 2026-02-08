---
name: analyzing-trends
description: Analyzes trends over the past 3 days including fix ratio, NOT_PLANNED Issues, and block patterns to detect systemic problems. Use when /analyzing-trends is invoked or cross-session pattern analysis is needed.
---

# 傾向分析

単一セッションでは見えない問題を検出するための複数粒度分析。

ultrathink

## 1. 直近3日間の傾向分析

```bash
# fix/feat比率を確認（高いfix比率は問題の兆候）
echo "=== Fix/Feat比率 ===" && \
git log --oneline --since="3 days ago" | grep -cE "(fix|refactor)[:(]" && \
git log --oneline --since="3 days ago" | grep -cE "feat[:(]"

# NOT_PLANNED Issueの確認（判断ミスの可能性）
echo "=== NOT_PLANNED Issues ===" && \
gh issue list --state closed --limit 20 --json number,title,stateReason \
  --jq '.[] | select(.stateReason == "NOT_PLANNED") | "#\(.number): \(.title[0:40])"'

# 頻出ブロックパターン（回避行動の兆候）
echo "=== 頻出ブロック ===" && \
tail -100 .claude/logs/execution/hook-errors.log 2>/dev/null | \
  jq -r '.reason[0:50]' 2>/dev/null | sort | uniq -c | sort -rn | head -5
```

| 指標 | 健全な状態 | 問題の兆候 |
|------|-----------|-----------|
| fix比率 | < 40% | > 50%（反応的な開発） |
| NOT_PLANNED | < 10% | > 20%（判断ミス多発） |
| 同一ブロック | < 5回 | > 10回（回避行動パターン） |

## 2. システム健全性

```bash
# フック総数と依存関係の複雑度
echo "=== フック数 ===" && \
ls .claude/hooks/*.py 2>/dev/null | wc -l

# 最近追加されたフック（過剰なガードレール追加の兆候）
echo "=== 直近追加フック ===" && \
git log --oneline --since="7 days ago" -- ".claude/hooks/*.py" | grep -cE "feat[:(]"
```

## 3. パターン検出

以下のパターンを探す:

| パターン | 説明 | 対応 |
|----------|------|------|
| **ガードレール回避サイクル** | ガード追加→回避行動→より厳格なガード | 根本原因（判断基準の曖昧さ）を解決 |
| **fix連鎖** | 同じ領域で連続fix | 設計見直しが必要 |
| **Issue乱発** | 軽微な問題もIssue化 | 閾値の見直し |

## 4. 分析結果のまとめ

上記の分析結果に基づき：

1. 問題パターンを特定
2. 根本原因を推測
3. 改善策をIssue化（必要な場合）
