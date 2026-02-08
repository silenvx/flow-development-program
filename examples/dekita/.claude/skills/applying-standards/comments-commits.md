# コメント・コミットメッセージ規約

コードコメント、CUSTOMIZEコメント、コミットメッセージの書き方。

## 基本原則

**「What（何を）」ではなく「Why（なぜ）」を書く**

コードは「何をしているか」は読めば分かる。時間と共に失われる「なぜそうしたか」を残す。

## コードコメント

| 書くべき内容 | 書かないべき内容 |
| ------------ | ---------------- |
| なぜこの実装を選んだか | コードを読めば分かること |
| 非自明な制約・エッジケース | 自明な処理の説明 |
| 外部仕様・バグへの対応（リンク付き） | 過度な装飾・フォーマット |
| 警告（順序変更で壊れる等） | |

```typescript
// ❌ What: コードを読めば分かる
// ユーザーIDをハッシュ化する
const hashed = hash(userId);

// ✅ Why: 背景・理由を説明
// プライバシー保護のため、ログにはハッシュ化したIDのみ記録
// 参考: https://example.com/privacy-policy
const hashed = hash(userId);
```

**注意**: コメントはコードと乖離するリスクがある。更新を怠ると害になる。

## 参照スタイルのコメント禁止

**問題**: 他のファイルを参照するコメントは、参照先が変更されると嘘になる。

```python
# ❌ 参照スタイル（禁止）
# pr_related_issue_check.pyと同じ
MIN_KEYWORD_LENGTH = 2  # 実際はpr_related_issue_check.pyは3かもしれない

# Same as utils.ts
TIMEOUT = 10

# ❌ 他の禁止パターン
# common.pyと共通
# utils.pyを参照
# config.tsからコピー
```

**解決策**:

1. **同じ値を使うなら `import` で共有**

   ```python
   from pr_related_issue_check import MIN_KEYWORD_LENGTH
   ```

2. **理由を書く**（参照ではなく「なぜ」を説明）

   ```python
   # ✅ 理由を説明
   # Issueタイトルは短い傾向があるため、PRより短いキーワードも抽出
   MIN_KEYWORD_LENGTH = 2
   ```

**フック**: `reference_comment_check.py` が編集時に警告（非ブロック）。

## コミットメッセージ

| 書くべき内容 | 書かないべき内容 |
| ------------ | ---------------- |
| 変更の動機・背景 | How（diffで分かる） |
| 影響範囲 | 自明な変更内容の列挙 |
| 関連Issue/PRへの参照 | |

```bash
# ❌ What only
Fix bug

# ✅ Why + What
fix: セッション切れ時に無限リダイレクトが発生する問題を修正

原因: トークン更新失敗時にリトライが無限ループしていた
対応: 最大3回のリトライ制限を追加

Fixes #123
```

**利点**: コミットメッセージは `git blame` で追跡でき、コードと分離しているため「腐りにくい」。

## CUSTOMIZEコメント

他プロジェクトへの再利用時に変更が必要な箇所を明示するコメント形式。

**形式**: 1行で「何を変更するか」と「どう変更するか（必要なら理由も含めてよい）」をハイフンで区切る

```python
# CUSTOMIZE: 何を変更するか - どう変更するか（必要なら理由も含める）
VARIABLE = "value"
```

**例**:

```python
# ✅ 良い例: 1行形式で情報が完結
# CUSTOMIZE: Production hostnames - Set these to your project's production domain(s)
PRODUCTION_HOSTNAMES = ["dekita.app", "api.dekita.app"]

# CUSTOMIZE: Default priority labels - Modify if your project uses different priority labels
DEFAULT_PRIORITY_LABELS = {"P0", "P1", "P2", "P3"}

# ❌ 悪い例: 説明が不十分
# CUSTOMIZE: ホスト名
PRODUCTION_HOSTNAMES = ["dekita.app"]

# ❌ 悪い例: 2行に分割（一貫性がない）
# CUSTOMIZE: Production hostnames
# Set these to your project's production domain(s)
PRODUCTION_HOSTNAMES = ["dekita.app"]
```

**Markdown内での形式**:

```markdown
<!-- CUSTOMIZE: パッケージマネージャ - 自分のプロジェクトに合わせて変更（npm/yarn/pnpm） -->
```
