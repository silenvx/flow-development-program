# コード品質

共通パターン修正、Pythonフックベストプラクティス、後方互換性。

## 共通パターン修正時の網羅チェック

**背景**: #2054 で `json.dumps` に `ensure_ascii=False` を追加した際、2箇所の修正漏れがあり #2065 で再修正が必要になった。

### ルール

共通関数・設定値・定数を変更する場合、**リポジトリ全体を検索して影響範囲を網羅的に確認**する。

### チェック手順

```bash
# 1. 修正対象のパターンを検索（例: json.dumps の場合）
rg "<修正対象のパターン>" --type py

# 2. 全ての呼び出し箇所を確認
# 3. 漏れなく修正
```

### 適用対象

| 変更内容                 | 検索対象               |
| ------------------------ | ---------------------- |
| 関数の引数追加           | その関数の全呼び出し   |
| 定数の変更               | その定数の全参照       |
| 設定値の変更             | その設定の全使用箇所   |
| 共通ユーティリティ変更   | 全import箇所           |

### PRレビュー時の確認項目

AIレビュー（Copilot/Codex）は以下を確認:

- [ ] この変更は他のファイルに影響しないか？
- [ ] 同様のパターンが他のファイルに存在しないか？
- [ ] 修正漏れがないか？

**関連Issue**: #2054, #2065, #2069

## Pythonフックベストプラクティス

フック実装時のコーディングパターン。Issue #1634で明文化。

### インポート

```python
# ✅ モジュールレベルでインポート
import time
from pathlib import Path

def main():
    start = time.time()

# ❌ 関数内インポート（呼び出し毎にインポート処理が走りパフォーマンスに影響し、依存関係も分かりにくくなるため避ける）
def main():
    import time  # パフォーマンスと可読性の観点から避ける
```

### 定数化

```python
# ✅ マジックナンバーを定数化
BLOCK_CLEANUP_WINDOW_SECONDS = 300
MAX_RETRY_COUNT = 3

if elapsed > BLOCK_CLEANUP_WINDOW_SECONDS:
    cleanup()

# ❌ マジックナンバーを直接使用
if elapsed > 300:  # 何の数値か不明
    cleanup()
```

### Fail-Close設計

不確実な状況ではブロック側に倒す（セキュリティ優先）:

```python
# ✅ 不確実な場合はブロック（安全側）
try:
    if marker_path.exists():
        return marker_path.read_text().strip()
except OSError:
    return None  # 不確実 → ブロック側に倒す

# ❌ エラー時に許可（危険）
except OSError:
    return "approved"  # エラー時に許可は危険
```

### 早期リターン

```python
# ✅ 早期リターンで条件をフラット化
def main():
    if not is_target_tool(tool_name):
        print_continue_and_log_skip("my-hook", "not target tool")
        return

    if not meets_condition():
        print_continue_and_log_skip("my-hook", "condition not met")
        return

    # メイン処理
    process()

# ❌ 深いネスト
def main():
    if is_target_tool(tool_name):
        if meets_condition():
            process()
```

### 関連ドキュメント

- `implementing-hooks` Skill: 「プロセス間状態共有」セクション
- Issue #1617: インメモリ状態管理の問題
- Issue #1634: コーディングパターンの明文化

## 後方互換性

### KVスキーマ変更時

- 古いデータとの互換性を保つ
- 新フィールドは optional + フォールバック処理
- **72時間後**に必須化・削除可能（データTTL=24h + 安全マージン）

### TODOコメント

```typescript
// TODO: 2025-12-17以降に削除可能（データTTL=24h、安全マージン72時間）
statusUpdatedAt?: string
```
