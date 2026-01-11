---
name: development-workflow
description: Git Worktreeを使った開発フロー、PR作成、CI監視、マージ手順。worktree、PR、マージ、CI、ブランチ作成時に使用。
---

# 開発ワークフロー

Git Worktreeを使用した開発フローの詳細手順。

**オリジナルは常にmainを維持、全ての作業はworktreeで行う。**

## 基本フロー

1. `git worktree add --lock .worktrees/<name> -b <branch>` - Worktree作成
2. `.claude/scripts/setup-worktree.sh .worktrees/<name>` - 依存インストール（自動検出）
3. `cd .worktrees/<name>` - worktreeに移動
4. `git fetch origin main && git diff HEAD..origin/main -- <変更予定ファイル>` - **main最新確認**
5. 作業・コミット
6. **ローカルテスト・Lint（必須）** - CI前に問題を検出
7. `/simplify` - コード簡素化（**長時間セッション後に推奨**）
8. `codex review --base main` - ローカルレビュー（**コミット追加後は再実行**）
9. `gh pr create` - PR作成（**UI変更時はスクリーンショット必須**）
10. `ci_monitor.py` でCI監視 + AIレビュー確認・対応
11. `gh pr merge --squash` - マージ（必ず実行）
12. worktree削除・main更新

**重要**: タスク完了時は必ずマージまで実行する。PR作成で止まらない。

**マージ後**:

```bash
cd <オリジナル> && git worktree unlock .worktrees/<name> && git worktree remove .worktrees/<name> && git pull
```

## タスク要件確認（毎回実行）

**実装前に必ず確認する**（`task-start-checklist` フックが自動リマインド）:

### 要件確認

- [ ] 要件は明確か？曖昧な点があれば質問する
- [ ] ユーザーの意図を正しく理解しているか？
- [ ] 「〜したい」の背景・目的は何か？

### 設計判断

- [ ] 設計上の選択肢がある場合、ユーザーに確認する
- [ ] 既存のコードパターン・規約を把握しているか？
- [ ] 事前に決めておくべきことはないか？

### 影響範囲

- [ ] 変更の影響範囲を把握しているか？
- [ ] 破壊的変更はないか？あれば事前に確認する

### 前提条件

- [ ] 必要な環境・依存関係は整っているか？
- [ ] Context7/Web検索で最新情報を確認すべきか？

**重要**: 不明点があれば、実装前に必ず質問する。実装後の手戻りを防ぐ。

## 作業開始前チェックリスト

- [ ] オープンIssue確認（`gh issue list --state open`）
  - assignee付きは他エージェント作業中
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
| **なぜ（背景・動機）** | この変更が必要になった理由 | `## なぜ` / `## Why` / `## 背景` / `## 理由` / `**なぜ**` 等 |
| **参照** | 関連Issue/PR/ドキュメント | `#123` / `Closes #XXX` / GitHub URL / `## 参照` 等 |

### 推奨フォーマット

```markdown
## なぜ
この変更が必要になった背景・動機を記述

## 何を
変更内容の概要

## テストプラン
- [ ] テスト項目1
- [ ] テスト項目2

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
## なぜ
session.pyのグローバル状態をDI化し、テスト容易性を向上させる。

## 何を
- HookContextクラスを追加
- create_hook_context()関数を追加

関連: #2413（第1段階完了）

## テストプラン
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

## ローカルテスト・Lint（PR作成前必須）

**目的**: CIでの失敗を事前に検出し、手戻りを防ぐ。

### 実行コマンド

```bash
# 1. Lint（TypeScript/JavaScript）
pnpm lint

# 2. 型チェック
pnpm typecheck

# 3. Python Lint（フック変更時）
uvx ruff check .claude/hooks/
uvx ruff format --check .claude/hooks/

# 4. テスト（変更に関連するもの）
pnpm test:ci

# Pythonフック変更時
uv run pytest .claude/hooks/tests/
```

### チェックリスト

| 変更対象 | 必須チェック |
|----------|-------------|
| TypeScript/JavaScript | `pnpm lint && pnpm typecheck` |
| Pythonフック | `uvx ruff check && uv run pytest` |
| React コンポーネント | 上記 + ブラウザでの目視確認 |
| API エンドポイント | 上記 + 手動リクエストテスト |

### なぜローカルで先に実行するか

| CI依存のみ | ローカル実行 |
|-----------|-------------|
| 失敗に気づくまで数分〜十数分 | 即座に問題を検出 |
| CI待ち中に他作業を始めて文脈スイッチ | 文脈を保ったまま即修正 |
| 手戻り遷移が発生（implementation→pre_check） | 手戻りなし |

**重要**: `pnpm lint && pnpm typecheck` を実行してから `gh pr create` を行う。

## Codexレビュー（プッシュ前必須）

**目的**: AIコードレビューでバグや設計問題を事前に検出。プッシュ時に `codex-review-check` フックがブロックするため、必ず実行する。

### 実行タイミング

| タイミング | 必須 | 理由 |
|-----------|------|------|
| PR作成前（初回プッシュ前） | ✅ | プッシュ時にブロックされる |
| レビュー対応後のコミット追加後 | ✅ | 再レビューが必要（ブロック対象） |
| typo修正など軽微な変更後 | ✅ | フックは区別しない |

### コマンド

```bash
# 基本実行
codex review --base main

# バックグラウンド実行（CI待ちと並行）
codex review --base main  # Claude CodeのBashツールで run_in_background=true を指定して実行
```

### よくある問題

| 問題 | 原因 | 対策 |
|------|------|------|
| 「レビュー未実行」でブロック | `codex review` を実行していない | PR作成前に必ず実行 |
| 「レビュー後に新コミット」でブロック | レビュー後にコミットを追加した | 再度 `codex review` を実行 |

### チェックリスト

プッシュ前に以下を確認:

- [ ] `codex review --base main` を実行した
- [ ] P0/P1の指摘があれば対応済み
- [ ] レビュー後にコミットを追加していない（追加した場合は再レビュー）

**重要**: レビュー対応でコミットを追加したら、再度 `codex review --base main` を実行する。

## Worktree管理

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

## CI監視

`gh pr checks --watch` の代わりに `ci_monitor.py` を使用。

### シフトレフト原則

**完了を待たず、問題を早期検知して即対応**:

| イベント | 対応 |
|----------|------|
| CI失敗を検知 | 即座に修正開始（完了を待たない） |
| レビューコメント検知 | 即座に対応開始（CI完了を待たない） |
| BEHIND検知 | 自動リベース（ci_monitor.pyが処理） |

### バックグラウンド実行（シフトレフト推奨）

```bash
# バックグラウンドでCI監視開始（--early-exit でシフトレフト有効化）
# Issue #1637: --wait-review と --json はデフォルトで有効
python3 .claude/scripts/ci_monitor.py {PR} --early-exit

# → レビューコメント検知時に即座に終了・通知（CI失敗は常に即座に終了）
# → 待ち時間中は並行タスクを実行
```

### 待ち時間の有効活用

CI/レビュー待ちの間は受動的に待機せず、以下のタスクを**並行実行**:

| 優先度 | タスク | コマンド例 |
| ------ | ------ | ---------- |
| 1 | 未解決スレッドをresolve | GraphQL APIでresolve |
| 2 | レビューコメントに返信 | `gh api` でコメント追加 |
| 3 | PR descriptionの更新 | `gh pr edit --body` |
| 4 | 関連ドキュメントの確認・更新 | 変更に伴うドキュメント整備 |
| 5 | フォローアップIssueの作成 | スコープ外の指摘をIssue化 |

**重要**: 「待っている」と報告するだけでなく、上記タスクを能動的に実行する

### コマンドオプション

**重要**: 全てのci_monitor.py呼び出しには `--session-id <SESSION_ID>` を付ける（ログのセッション紐付けに必要）

※ `<SESSION_ID>`はUserPromptSubmit hookで提供されるセッションID

```bash
# Issue #2454: 簡素化されたオプション（JSON出力とレビュー待機は常に有効）

# 基本（CI + レビュー待機、JSON出力）
python3 .claude/scripts/ci_monitor.py {PR} --session-id <SESSION_ID>

# シフトレフト推奨: CI失敗/レビュー検知で即座に終了
python3 .claude/scripts/ci_monitor.py {PR} --session-id <SESSION_ID> --early-exit

# タイムアウト指定（デフォルト: 30分）
python3 .claude/scripts/ci_monitor.py {PR} --session-id <SESSION_ID> --timeout 60
```

### ci_monitor.py 機能

| 監視対象                  | 自動対処       |
| ------------------------- | -------------- |
| BEHIND（mainより遅れ）    | 自動リベース   |
| DIRTY（コンフリクト）     | エラー終了     |
| レビュー完了              | コメント表示   |
| CI成功/失敗               | 結果表示・終了 |

## 並行作業パターン（別Issue対応）

CI監視やcodex reviewをバックグラウンドで実行しながら、別のIssue作業を並行して行うパターン。

### ユースケース

| 状況 | 並行作業の例 |
|------|-------------|
| CI待ち（5-10分） | 軽量なバグ修正、ドキュメント更新 |
| codex review待ち | 別Issueの調査、設計検討 |
| レビュー待ち | 次のIssueの着手 |

### 手順

```bash
# === 現在のPR作業 ===
# ※ worktree: /path/to/original/repo/.worktrees/issue-123 で実行

# 1. CI監視をバックグラウンドで開始
#    Bashツールの run_in_background=true で実行し、task_idを取得
python3 .claude/scripts/ci_monitor.py {PR} --early-exit
# → task_id を控えておく（例: bg_task_abc123）

# 2. codex reviewをバックグラウンドで開始（任意）
codex review --base main
# → task_id を控えておく

# === 別Issue作業を開始 ===

# 3. オリジナルに戻る
cd /path/to/original/repo

# 4. 別のworktreeを作成
git worktree add --lock .worktrees/issue-456 -b feat/issue-456-desc

# 5. 別worktreeに移動して作業
cd .worktrees/issue-456
# ... 作業 ...

# === 定期的に確認 ===

# 6. TaskOutputでCI監視の結果を確認（non-blocking）
# TaskOutput: task_id=<ci-monitor-task-id>, block=false

# 7. CI完了を検知したら...

# === 元のPRに戻る ===

# 8. 元のworktreeに戻る
cd /path/to/original/repo/.worktrees/issue-123

# 9. マージ処理を続行
gh pr merge {PR} --squash
```

### 注意事項

| 項目 | 説明 |
|------|------|
| **worktreeのロック** | 両方のworktreeは `--lock` で作成済みのため、他セッションからの削除は防止される |
| **コンテキスト管理** | TodoWriteで両方のタスクを追跡し、どこまで進んだか把握する |
| **CI失敗時** | TaskOutputで失敗を検知したら、即座に元のworktreeに戻って修正 |
| **codex review結果** | TaskOutputで結果を確認し、指摘があれば対応 |

### 推奨しないケース

| 状況 | 理由 |
|------|------|
| 両方のPRが同じファイルを変更 | コンフリクトのリスク |
| 並行Issueが複雑 | コンテキストスイッチのコストが高い |
| CI待ちが1-2分 | 切り替えのオーバーヘッドの方が大きい |

### 実践例

```bash
# PR #100 のCI監視をバックグラウンドで開始
python3 .claude/scripts/ci_monitor.py 100 --early-exit
# → task_id: bg_task_abc123

# 別Issueの軽量な修正を開始
cd /Users/me/project
git worktree add --lock .worktrees/issue-101 -b fix/issue-101-typo
cd .worktrees/issue-101

# typo修正してコミット
# ...

# CI監視の状況を確認（non-blocking）
# TaskOutput: task_id=bg_task_abc123, block=false
# → まだ実行中なら別作業を続行
# → 完了していればPR #100に戻ってマージ
```

## 複数セッション並行作業時のBEHIND対策

複数のClaudeセッションが同時にPRをマージしようとすると、BEHINDループ（リベース→CI待ち→マージ試行→BEHIND→繰り返し）が発生することがある。

### 根本原因

- セッションAがマージ → mainが更新
- セッションBのPRがBEHINDに
- セッションBがリベース → CI待ち
- その間にセッションAの別PRがマージ → またBEHIND
- 繰り返し...

### GitHub Merge Queueは使用不可

[GitHub Merge Queue](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue) は Organization 所有リポジトリのみ対応。個人アカウント所有リポジトリでは利用できない。

### 既存の対策（ci_monitor.py）

`ci_monitor.py`はCI監視とBEHIND時の自動リベースを担当:

| オプション | 効果 |
|------------|------|
| `--max-rebase N` | リベース上限（デフォルト3回） |
| wait-stable機能 | mainが安定するまで待機（デフォルト有効、`--no-wait-stable`で無効化） |

```bash
# CI監視（BEHIND時は自動リベース）
python3 .claude/scripts/ci_monitor.py {PR}

# マージはgh pr mergeで直接実行（PostToolUseフックが発火する）
gh pr merge {PR} --squash
```

**注意**: `--merge`オプションは廃止されました（Issue #2399）。マージは`gh pr merge`で直接実行してください。

### BEHINDループが頻繁に発生する場合

| 対策 | 説明 |
|------|------|
| **時間帯分散** | 他セッションとマージ時間が重ならないよう調整 |
| **wait-stable機能** | デフォルト有効。max_rebase到達時にmainの更新が収まるまで待機 |
| **軽量PRを先に** | CI時間が短いPRを優先的にマージ |
| **バッチマージ回避** | 一度に多数のPRをマージしない |

### リベース回数の警告

`ci_monitor.py`は2回以上のリベースが必要だった場合に警告を表示:

```
⚠️ 2回目のリベースが必要でした（並行作業が多い可能性。merge queue検討を推奨）
```

この警告が頻出する場合は、作業の時間帯分散を検討する。

## マージ手順

### マージ前チェックリスト

```bash
# 1. PR description確認（レビュー対応後・追加コミット後は必須）
gh pr view {PR} --json body,commits,additions,deletions
```

**description更新が必要なケース**:

| 状況 | 対応 |
|------|------|
| レビュー対応で実装方針変更 | ✅ description更新必須 |
| 追加機能・スコープ変更 | ✅ description更新必須 |
| Test plan項目の増減 | ✅ description更新必須 |
| typo修正・軽微な修正のみ | ❌ 更新不要 |

```bash
# description更新（必要時）
gh pr edit {PR} --body "$(cat <<'EOF'
## Summary
...

## Test plan
...

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### Issue要件の完全転記確認（Issue #2402）

**ドキュメント更新や設定値追加時は必須**:

```bash
# Issue本文を再読（記憶からではなく原文を確認）
gh issue view {Issue番号}
```

| チェック項目 | 確認内容 |
|-------------|----------|
| **対応案の値リスト** | Issue「対応案」に記載した値・設定を全て実装に反映したか |
| **チェックリスト** | Issue本文の `- [ ]` 項目を全て実装したか |
| **テーブルの列/行** | 表形式で列挙した項目を全て含めたか |

**背景**: PR #2397でIssue #2392の対応案に記載した値（UNKNOWN, REVIEW_REQUIRED）がAGENTS.mdへの反映時に漏れ、Copilotレビューで2回指摘された。

**禁止**: Issue内容を記憶から転記すること。必ず `gh issue view` で原文を再確認する。

### マージ実行

```bash
# ブランチ更新（必要時）
gh pr update-branch {PR} --rebase

# CI + レビュー待機（バックグラウンドで実行し、待ち時間タスクを並行処理）
# Issue #1637: --wait-review と --json はデフォルトで有効
python3 .claude/scripts/ci_monitor.py {PR}

# マージ（リモートブランチはGitHub設定で自動削除される）
gh pr merge {PR} --squash

# ★必須: マージ後のローカル更新（オリジナルディレクトリで実行）
cd <オリジナル> && git pull origin main
# worktree削除（必要に応じて）
git worktree unlock .worktrees/<name> && git worktree remove .worktrees/<name>
```

**重要**: `gh pr merge`で終わらない。必ず`git pull origin main`でローカルを更新する。

## マージ後の本番確認

1. GitHub Actions成功確認（バックグラウンドで待機）

   ```bash
   gh run list --branch main --limit 1
   gh run watch <run_id>  # run_in_background=true で実行
   # → TaskOutput で完了を確認後、本番確認へ
   ```

2. Chrome DevTools MCPで本番確認

   ```bash
   # CUSTOMIZE: 本番URLを自分のプロジェクトに合わせて変更
   mcp__chrome-devtools__navigate_page url="https://dekita.app/"
   mcp__chrome-devtools__take_screenshot
   ```

## Dependabot PRのマージ

Dependabot PRは **リスクの低い順** に1つずつマージする。

### マージ順序（リスク順）

| 優先度 | 種別 | 例 | リスク |
|--------|------|-----|--------|
| 1 | dev依存 | `@types/*`, テストツール | 最低 |
| 2 | インフラ | GitHub Actions | 低 |
| 3 | 本番依存 (patch) | `4.11.0→4.11.1` | 中 |
| 4 | 本番依存 (minor) | `4.11→4.12` | 中〜高 |
| 5 | 本番依存 (major) | `4.x→5.x` | 高（要変更履歴確認） |

### マージ手順

```bash
# 1. CI SUCCESS確認
gh pr view {PR} --json statusCheckRollup

# 2. 競合確認・リベース（UNKNOWNの場合のみ。CONFLICTINGは手動解決）
gh pr update-branch {PR} --rebase

# 3. 本番依存の場合: ローカルE2Eテスト実行（CIでは未実行のため）
gh pr checkout {PR}
pnpm install
pnpm test:e2e:chromium

# 4. マージ（1つずつ）
gh pr merge {PR} --squash

# 5. main CI確認（本番依存の場合、バックグラウンドで待機）
gh run list --branch main --limit 1
gh run watch <run_id>  # run_in_background=true で実行

# 6. 次のPRへ（TaskOutput で main CI パス確認後）
```

### 本番依存マージ後の確認

本番依存（hono等）のマージ後は自動デプロイ完了を待ち、動作確認:

```bash
# デプロイ完了待機（バックグラウンドで実行）
gh run watch <run_id>  # run_in_background=true で実行
# → TaskOutput で完了を確認後、本番確認へ

# 本番確認
# CUSTOMIZE: 本番URLを自分のプロジェクトに合わせて変更
mcp__chrome-devtools__navigate_page url="https://dekita.app/"
mcp__chrome-devtools__take_screenshot
```

### 注意事項

- **E2Eはローカル実行**: Dependabot PRではCIでE2Eテストを実行しない（Pwn Request攻撃対策）。本番依存はマージ前にローカルでE2E実行
- **並列マージ禁止**: 1つずつマージしてmain CIを確認
- **major更新**: CHANGELOGで破壊的変更を必ず確認

## Sub-Issue管理

大きなタスクを分割する場合、sub-issueで親子関係を作成する。

### 使用場面

| 場面 | 例 |
|------|-----|
| 大規模機能 | 「認証システム」→ログイン、登録、リセットを子に |
| 複数ステップ調査 | 「パフォーマンス改善」→各ボトルネックを子に |
| リリース管理 | 「v2.0」→各機能を子に |

### 操作方法

```bash
# Issue番号からnode_idを取得
parent_id=$(gh issue view <親番号> --json id -q .id)
child_id=$(gh issue view <子番号> --json id -q .id)

# sub-issue追加
gh api graphql \
  -H "GraphQL-Features:sub_issues" \
  -f query='mutation($parent: ID!, $child: ID!) {
    addSubIssue(input: {issueId: $parent, subIssueId: $child}) {
      issue { number title }
      subIssue { number title }
    }
  }' \
  -f parent="$parent_id" \
  -f child="$child_id"

# sub-issue削除
gh api graphql \
  -H "GraphQL-Features:sub_issues" \
  -f query='mutation($parent: ID!, $child: ID!) {
    removeSubIssue(input: {issueId: $parent, subIssueId: $child}) {
      issue { number }
      subIssue { number }
    }
  }' \
  -f parent="$parent_id" \
  -f child="$child_id"
```

### 制限

- 親1つに最大100個のsub-issue
- 最大8階層までネスト可能
- クロスリポジトリ対応（他リポジトリのIssueも子にできる）

## トラブルシューティング

問題発生時は `troubleshooting` Skill を参照。
