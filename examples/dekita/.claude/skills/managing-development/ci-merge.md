# CI監視・マージ

CI監視、マージ手順、本番確認、Dependabotの対応。

## CI監視

`gh pr checks --watch` の代わりに `ci_monitor`（TypeScript版）を使用。

### シフトレフト原則

**完了を待たず、問題を早期検知して即対応**:

| イベント | 対応 |
|----------|------|
| CI失敗を検知 | 即座に修正開始（完了を待たない） |
| レビューコメント検知 | 即座に対応開始（CI完了を待たない） |
| BEHIND検知 | 自動リベース（ci_monitorが処理） |

### バックグラウンド実行（シフトレフト推奨）

```bash
# バックグラウンドでCI監視開始（--early-exit でシフトレフト有効化）
# Issue #1637: --wait-review と --json はデフォルトで有効
bun run .claude/scripts/ci_monitor_ts/main.ts {PR} --session-id <SESSION_ID> --early-exit

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

**重要**: 全てのci_monitor呼び出しには `--session-id <SESSION_ID>` を付ける（ログのセッション紐付けに必要）

※ `<SESSION_ID>`はUserPromptSubmit hookで提供されるセッションID

```bash
# Issue #2454: 簡素化されたオプション（JSON出力とレビュー待機は常に有効）

# 基本（CI + レビュー待機、JSON出力）
bun run .claude/scripts/ci_monitor_ts/main.ts {PR} --session-id <SESSION_ID>

# シフトレフト推奨: CI失敗/レビュー検知で即座に終了
bun run .claude/scripts/ci_monitor_ts/main.ts {PR} --session-id <SESSION_ID> --early-exit

# タイムアウト指定（デフォルト: 30分）
bun run .claude/scripts/ci_monitor_ts/main.ts {PR} --session-id <SESSION_ID> --timeout 60
```

### ci_monitor 機能

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
bun run .claude/scripts/ci_monitor_ts/main.ts {PR} --session-id <SESSION_ID> --early-exit
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

### 既存の対策（ci_monitor）

`ci_monitor`はCI監視とBEHIND時の自動リベースを担当:

| オプション | 効果 |
|------------|------|
| `--max-rebase N` | リベース上限（デフォルト3回） |
| wait-stable機能 | mainが安定するまで待機（デフォルト有効、`--no-wait-stable`で無効化） |

```bash
# CI監視（BEHIND時は自動リベース）
bun run .claude/scripts/ci_monitor_ts/main.ts {PR} --session-id <SESSION_ID>

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

`ci_monitor`は2回以上のリベースが必要だった場合に警告を表示:

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
| **How section** | Issue「How」に記載した値・設定を全て実装に反映したか |
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
bun run .claude/scripts/ci_monitor_ts/main.ts {PR} --session-id <SESSION_ID>

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
