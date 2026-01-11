# 参照リポジトリからのインポート

FDP（Flow Development Program）または任意の参照リポジトリを参考にして、プロジェクトに必要な機能を**再実装**する。

---

## 使い方

このpromptは**目的ベース**で使用する：

```
prompts/import-from-fdp.md を読んで、worktree保護機能を実装してください
```

```
prompts/import-from-fdp.md を読んで、PRマージ前のチェック機能を実装してください
```

**目的なしで使用しないこと**。「とりあえずFDPを参考にして」では何も実装できない。

---

## 重要: コピーではなく再実装

FDPはコピー元ではなく**参考**として使用する。

| 禁止 | 推奨 |
|------|------|
| コードをそのままコピー | パターンを理解して再実装 |
| ファイルを丸ごと取り込む | 必要な機能を独自に実装 |

**理由**:
- **セキュリティ**: 外部コードは内容を理解してから使用
- **適応**: プロジェクト固有の要件に最適化

---

## 前提

**参照ソースは `examples/<source>/.claude/index.json` を使用する。**

- `<source>` は参照リポジトリ名（例: `dekita`, `project-a`）
- index.json が存在しない場合は `examples/<source>/.claude/scripts/generate_index.py` を実行して生成

> index.json はフック/スクリプト/スキルのメタデータ（Why/What/keywords等）を含む軽量インデックス

---

## 実行手順

### 1. 目的を明確化

- 何を実現したいか（1〜2文で記述）
- 既存の仕組みと衝突しそうな点（あれば）

### 2. 参照ソースを選択

- FDPを参照するか、別リポジトリを参照するかを決める
- 参照ソースの `examples/<source>/.claude/index.json` を対象にする

### 3. index.json でパターンを検索

ユーザーが指定した目的に関連するフック/スクリプトを探す：

```bash
# キーワードでフックを検索（例: worktree関連）
jq '.hooks[] | select(.keywords[]? | test("worktree"; "i")) | {name, summary, why}' \
  examples/<source>/.claude/index.json

# サマリーで検索（例: マージ関連）
jq '.hooks[] | select(.summary | test("マージ|merge"; "i")) | {name, summary, hook_type}' \
  examples/<source>/.claude/index.json

# hook_type で絞り込み（blocking/warning/info/logging）
jq '.hooks[] | select(.hook_type == "blocking") | {name, summary}' \
  examples/<source>/.claude/index.json

# スクリプトを検索
jq '.scripts[] | select(.summary | test("ci|monitor"; "i")) | {name, summary}' \
  examples/<source>/.claude/index.json

# スキルを検索
jq '.skills[] | {name, summary, description}' \
  examples/<source>/.claude/index.json
```

### 4. パターンを理解

見つかったエントリについて以下を把握：

| 情報 | index.json のフィールド |
|------|------------------------|
| 目的（Why） | `.why` |
| 機能（What） | `.what` |
| 概要 | `.summary` |
| 実行タイミング | `.trigger` / `.matcher` |
| フックタイプ | `.hook_type` (blocking/warning/info/logging) |
| 検索キーワード | `.keywords` |
| 備考 | `.remarks` |

**詳細コードの確認**（必要に応じて）:

```bash
# パスからソースコードを読む
cat examples/<source>/<path>
```

### 5. プロジェクトの要件を確認

- 使用言語・フレームワーク
- 既存の `.claude/` 構成
- プロジェクト固有の制約

### 6. 再実装

参照パターンを参考に、プロジェクトに適した実装を作成：

1. index.json で目的（Why/What）を把握
2. ソースコードを読んで実装アプローチを理解
3. プロジェクトの要件に合わせて再設計
4. 独自のコードとして実装
5. テストで動作確認
6. settings.jsonにフックを登録

**禁止**: 参照リポジトリのコードをそのままコピーすること。

### 7. 検証

- 成功ケース / 失敗ケース / 境界ケースを最低限確認
- 自動テストがある場合は追加、なければ手順を記載

## docstring規約

実装するフックには以下の形式を推奨：

```python
"""<1行の説明>

Why:
    <なぜ必要か>

What:
    <何をするか>

Remarks:
    <補足情報>

Tags:
    type: blocking
    category: quality-gate
"""
```

---

## 参照先で見つかる主なパターン

| カテゴリ | 検索キーワード例 |
|----------|-----------------|
| Worktree保護 | `worktree`, `session`, `locked` |
| PRマージチェック | `merge`, `review`, `closes` |
| Issue管理 | `issue`, `assign`, `priority` |
| セッション管理 | `session`, `handoff`, `continuation` |
| 振り返り | `reflect`, `gosei`, `lesson` |
| CI監視 | `ci`, `monitor`, `checks`, `behind` |
| コード品質 | `lint`, `test`, `coverage` |

---

## 出力

実装完了後、以下を報告：

1. 実装したファイル一覧
2. settings.jsonへの登録内容
3. 動作確認結果
4. 参照元（`<source>`）と参照フック名
