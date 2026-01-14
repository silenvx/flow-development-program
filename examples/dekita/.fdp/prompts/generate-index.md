# インデックス再生成

.fdp/のインデックスとドキュメントを再生成する。

---

## 使い方

```bash
python3 .claude/scripts/generate_index.py
```

---

## 出力

| ファイル | 内容 |
|---------|------|
| `.fdp/index.json` | 機械処理用インデックス |
| `.fdp/README.md` | 機能カタログ |
| `.fdp/flows.md` | Mermaidフロー図 |

---

## オプション

```bash
# 詳細出力
python3 .claude/scripts/generate_index.py --verbose

# ドライラン（ファイル出力なし）
python3 .claude/scripts/generate_index.py --dry-run
```
