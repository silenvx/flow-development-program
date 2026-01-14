# パターン参照・移植ガイド

このプロジェクトから開発フローパターンを参照・移植する方法。

---

## 1. パターン検索

```bash
# キーワードで検索
jq '.hooks[] | select(.keywords[] | contains("worktree"))' .fdp/index.json

# フックタイプで検索
jq '.hooks[] | select(.hook_type == "blocking")' .fdp/index.json

# サマリーで検索
jq '.hooks[] | select(.summary | contains("PR"))' .fdp/index.json
```

---

## 2. パターン理解

```bash
# 詳細を確認
jq '.hooks[] | select(.name == "merge_check")' .fdp/index.json
```

**確認ポイント:**
- `why`: なぜこのフックが必要か
- `what`: 何をするか
- `trigger`: いつ発火するか
- `hook_type`: blocking/warning/info/logging

---

## 3. ソースコード参照

```bash
# pathフィールドからソースコードを確認
cat $(jq -r '.hooks[] | select(.name == "merge_check") | .path' .fdp/index.json)
```

---

## 4. 移植

1. ソースコードをコピー
2. プロジェクト固有の設定を調整（パス、コマンド等）
3. settings.jsonにフックを登録
4. テスト実行
