# コミット・PR作成

コミットメッセージ規約、PRボディの必須項目、段階的実装のワークフロー。

## コミットメッセージ規約

**背景（Why）を必ず含める**。何をしたか（What）だけでなく、なぜその変更が必要かを記述する。

### フォーマット

```
<type>(<scope>): <summary>

<背景/理由>

<変更内容の詳細（必要に応じて）>
```

### 例

```
fix(hooks): fork-session検出をtranscriptファイル名比較で改善

別セッションで作成されたworktreeかを判定する際、cwdベースの判定では
~/.claude/projects/パスを許可した影響で誤検知が発生していた。
transcriptファイル名を直接比較することで正確な検出が可能になる。

- _is_current_session_worktree()にtranscript_path引数を追加
- ファイル名（session-id部分）の一致で判定
```

### squashマージ時

`gh pr merge --squash` はPRのbodyをそのままコミットメッセージに使用する。`--body` オプションは使用禁止（PRの詳細な説明が上書きされるため）。

```bash
# ✅ 正しい: PRのbodyがそのままコミットメッセージになる
gh pr merge {PR} --squash --delete-branch

# ❌ 禁止: --body でPRの詳細な説明が上書きされる
gh pr merge {PR} --squash --body "短い要約"
```

**PRボディに背景を含める**: マージ前に `gh pr edit {PR} --body "..."` でPRボディを更新すること。

**禁止パターン**:

| ❌ 悪い例 | ✅ 良い例 |
|----------|----------|
| `fix: バグ修正` | `fix(api): 認証トークン期限切れ時の500エラーを修正` + 背景説明 |
| `feat: 新機能追加` | `feat(ui): ダークモード切替ボタンを追加` + 背景説明 |
| `refactor: コード整理` | `refactor(hooks): BlockingReason収集パターンに統一` + 背景説明 |

## PRボディの必須項目（フック強制）

`pr-body-quality-check` フックがPR作成時・マージ時に以下を強制する:

### 必須項目

| 項目 | 説明 | 検出パターン |
|------|------|-------------|
| **Why** | この変更が必要になった理由 | `## Why` |
| **参照** | 関連Issue/PR/ドキュメント | `#123` / `Closes #XXX` / GitHub URL |

### 推奨フォーマット

```markdown
## Why
Describe the motivation/background for this change

## What
Describe what this change does

## Test plan
- [ ] Test item 1
- [ ] Test item 2

Closes #XXX
```

### ブロック時の対処

| タイミング | 対処 |
|-----------|------|
| PR作成時 | `--body` オプションに必須項目を含める |
| マージ時 | `gh pr edit {PR} --body "..."` でPRボディを更新 |

## 段階的実装Issueのワークフロー

大規模なリファクタリングや機能追加を複数PRに分割して実装する場合のガイドライン。

### PRボディの書き方

| 状況 | PRボディの書き方 | 理由 |
|------|-----------------|------|
| Issue完全実装 | `Closes #xxx` | Issueが自動クローズされる |
| Issue部分実装（段階的） | `関連: #xxx（第N段階完了）` | Issueはオープンのまま |

**重要**: `Closes #xxx` を使うと `merge-check` がIssue内の未完了チェックボックスを検出してブロックする。段階的実装では `関連:` を使用する。

### Issue本文の構造化

段階的実装を計画する場合、Issue本文で段階を明示する:

```markdown
## タスク（段階的移行）

### 第1段階（PR #xxx）
- [x] 基本クラス設計
- [x] 新API追加

### 第2段階以降（将来のPR）
- [ ] 既存APIのdeprecation
- [ ] 段階的移行
- [ ] 完全削除
```

### Issueのクローズタイミング

| パターン | クローズタイミング |
|----------|-------------------|
| 全段階を1つのIssueで管理 | 最終段階のPRマージ後 |
| 段階ごとに別Issueを作成 | 各PRマージ後に該当Issueをクローズ |

**推奨**: 大規模な場合は段階ごとに別Issueを作成し、親Issueからsub-issueとしてリンクする。

### 具体例（Issue #2413）

PR #2435で第1段階を実装した例:

**PRボディ**:
```markdown
## Why
session.pyのグローバル状態をDI化し、テスト容易性を向上させる。

## What
- HookContextクラスを追加
- create_hook_context()関数を追加

関連: #2413（第1段階完了）

## Test plan
- [x] 既存テストがパス
- [x] 新APIのユニットテスト追加
```

**Issue進捗コメントの例**:
```markdown
## 第1段階完了
PR #2435 で以下を完了:
- HookContext クラス設計
- 新API（DI版）追加

## 第2段階以降
引き続きこのIssueで残りのタスクを進めます。
```

**注**: 段階ごとに別Issueを作成する場合は「残りタスクは Issue #XXXX に分割」と記載し、元Issueをクローズする。
