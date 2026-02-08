# Worktree管理

Git Worktreeの作成・管理、作業開始時のチェックリスト。

## 作業開始前チェックリスト

- [ ] オープンIssue確認（`gh issue list --state open`）
  - assignee付きは他エージェント作業中
- [ ] **Issue依存関係確認**（AGENTS.md「Issue依存関係の確認」参照）
  - Dependenciesセクション取得: `gh issue view <対象Issue番号> --json body -q .body | awk '/^## Dependencies/{flag=1; print; next} /^## /{flag=0} flag'`
  - Blocked byがあれば**各ブロック元**の状態確認: `gh issue view <ブロック元番号> --json state -q .state`
  - 全ブロック元がCLOSED以外なら着手禁止
- [ ] オリジナルディレクトリにいるか確認
- [ ] worktree作成（Issue関連ならブランチ名にIssue番号含める: `feat/issue-123-desc`）
- [ ] worktreeに移動

## worktree作成直後のチェック（重要）

worktree作成後、実装開始前に以下を確認する。

**目的**: プラン作成後にmainが更新されている可能性があるため、最新パターンとの差分を確認し、リベース時のコンフリクトを防ぐ。

### 手順

```bash
# 1. main最新を取得
git fetch origin main

# 2. worktree作成時点とmain最新の差分確認（変更予定ファイル）
git diff HEAD..origin/main -- <変更予定ファイル>

# 例: merge_check.pyを変更する場合
git diff HEAD..origin/main -- .claude/hooks/merge_check.py

# 3. 差分が大きい場合は内容を確認
git show origin/main:<変更予定ファイル> | head -100
```

### 確認ポイント

| チェック項目 | 対応 |
| ------------ | ---- |
| 差分なし | そのまま実装開始 |
| 軽微な変更（typo、コメント等） | そのまま実装開始 |
| 構造的な変更（関数追加、パターン変更） | **プランを見直す** |
| 大きな変更（ファイル分割、リファクタリング） | **プランを見直す** |

### 特に注意すべきファイル

以下のファイルは共通パターンが変更されることが多い:

- `.claude/hooks/*.py` - BlockingReasonパターン、早期exit vs 収集パターン
- `.claude/skills/*/SKILL.md` - セクション構造、フォーマット
- `.claude/scripts/*.py` - 引数パース、出力形式

### 例: プラン見直しが必要なケース

Issue #858実装時の実例:

1. プラン作成時点: `make_block_result()` + `sys.exit(0)` パターン
2. worktree作成後: Issue #874で `BlockingReason` 収集パターンに変更済み
3. 結果: リベース時にコンフリクト発生、書き直しが必要

**教訓**: worktree作成直後に `git diff HEAD..origin/main -- <変更予定ファイル>` を実行していれば、最新パターンで実装開始できた。

## Worktree基本操作

```bash
# 作成（--lock必須: 他エージェントの削除を防止）
git worktree add --lock .worktrees/feature-auth -b feature/auth

# 一覧表示
git worktree list

# ロック解除→削除（PRマージ後）
git worktree unlock .worktrees/<name>
git worktree remove .worktrees/<name>
```

**禁止事項**:

- mainで `git checkout -b` - 常にworktreeを使用
- `--force --force` でのロック解除 - 他エージェント作業中の可能性
- worktree内から自分自身を削除 - シェルが壊れる
