# インデックス再生成

.fdp/のインデックスとドキュメントを再生成する。

---

## 使い方

```bash
bun run .claude/scripts/generate_index.ts
```

---

## 出力

| ファイル | 内容 |
|---------|------|
| `.fdp/index.json` | 機械処理用インデックス |
| `.fdp/README.md` | 機能カタログ |

---

## オプション

```bash
# 詳細出力
bun run .claude/scripts/generate_index.ts --verbose

# ドライラン（ファイル出力なし）
bun run .claude/scripts/generate_index.ts --dry-run
```
