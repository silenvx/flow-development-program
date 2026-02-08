# 返信テンプレートとResolve手順

レビューコメントへの返信テンプレート、署名ルール、Resolve手順。

## 修正後の即時対応（重要）

**コード修正後、必ず以下を実行する**（バッチ処理せず、各修正の直後に行う）:

```bash
# 1. コメントIDとスレッドIDを取得
gh api graphql -f query='
query($pr: Int!) {
  repository(owner: "{owner}", name: "{repo}") {
    pullRequest(number: $pr) {
      reviewThreads(first: 50) {
        nodes {
          id
          isResolved
          comments(first: 1) {
            nodes { id databaseId body }
          }
        }
      }
    }
  }
}' -F pr={PR} --jq '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved == false)'

# 2. 該当スレッドに返信（{THREAD_ID}は手順1で取得したid）
gh api graphql -f query='
mutation {
  addPullRequestReviewThreadReply(input: {
    pullRequestReviewThreadId: "{THREAD_ID}",
    body: "修正済み: [修正内容を簡潔に記載]\n\n-- Claude Code"
  }) {
    comment { id }
  }
}'

# 3. スレッドをResolve（{THREAD_ID}は手順2と同じ）
gh api graphql -f query='
mutation {
  resolveReviewThread(input: {threadId: "{THREAD_ID}"}) {
    thread { isResolved }
  }
}'
```

**なぜ修正作業をバッチ処理しないのか**:
- 複数コメント対応時、どの修正がどのコメントに対応するか不明確になる
- 漏れが発生しやすい
- 後からまとめて対応すると、コミットとの対応関係が曖昧になる

**自動化スクリプト**:

```bash
# review_respond.ts を使用（PR番号、コメントID、スレッドID、返信内容を指定）
bun run .claude/scripts/review_respond.ts {PR} {COMMENT_ID} {THREAD_ID} "修正内容"
```

## レビュー対応ツールの選択（Issue #1011, #2633）

| 状況 | 推奨ツール | 理由 |
| ---- | ---------- | ---- |
| **複数スレッドに同じメッセージ** | `batch_resolve_threads.ts` | 一括解決、署名自動付与 |
| **個別スレッドに異なる対応** | `review_respond.ts` | 個別対応、署名自動付与 |
| **上記で対応できない特殊ケース** | GraphQL API | 柔軟だがブロックリスク |

**重複コメント防止**:

以下のシナリオで重複が発生する:
1. REST API でコメント投稿（署名なし）
2. `resolve-thread-guard` がブロック
3. GraphQL で署名付きコメントを再投稿
4. → 同じ内容のコメントが2件

**対策**: 最初から署名付きでスクリプトを使用する。

```bash
# 状況: 複数のスレッドに同じ内容で一括返信・解決する場合
bun run .claude/scripts/batch_resolve_threads.ts {PR} "修正しました。Verified: [内容]"

# 状況: スレッドごとに異なる内容で返信する場合
bun run .claude/scripts/review_respond.ts {PR} {COMMENT_ID} {THREAD_ID} "対応内容"
```

## レビュー対応コメントの標準テンプレート（Issue #1182）

**1コメントで修正報告と検証報告を完結させる**。

| 状況 | 形式 |
| ---- | ---- |
| 修正完了 | 修正しました。コミット [ハッシュ] で対応。Verified: [確認した修正内容] |
| 確認のみ | Verified: [確認内容] (ファイル:行番号) |
| 却下 | False positive: [理由] (Issue #xxx) |

※ 全テンプレートに `-- Claude Code` 署名が必要

**例（推奨形式）**:

```text
修正しました。コミット abc1234 で対応。

Verified: タブ区切りパースの修正を確認
- ファイル: .claude/hooks/worktree_removal_check.py:91-110
- 変更: `split("\t", 2)` でタブ区切りを正しくパース

-- Claude Code
```

**必須要素**（Issue #2988）:
- **コミットハッシュ**: 修正報告には必ずコミットハッシュを含める（`git log -1 --format=%h` で取得）
- **Verified:**: 検証内容を具体的に記述
- **署名**: `-- Claude Code`

## 返信時の署名ルール（必須）

**すべてのレビューコメントへの返信には `-- Claude Code` 署名を含める**。

署名がないと `merge_check.py` がマージをブロックする。

```text
[対応内容や却下理由]

-- Claude Code
```

**返信なしでResolveしない**:

- **修正した場合**: 何を修正したか記載 + 署名
- **却下する場合**: 却下理由を明記 + 署名
- **「範囲外」で却下**: **必ずIssueを作成**してからResolve + 署名

**重要: 「修正しない」判断 = Dismiss = Issue必須**

以下のケースは全て「Dismiss」としてmerge-checkがブロックする:

| ケース | 例 | 対応 |
| ------ | -- | ---- |
| 誤検知（False Positive） | AIが誤った指摘をした | Issue作成必須 |
| 現状で十分 | 「修正不要と判断」「現状で問題なし」 | Issue作成必須 |
| スコープ外 | 「本PRのスコープ外」「将来の改善」 | Issue作成必須 |
| 設計判断 | 「意図的にこの実装」 | Issue作成必須 |

## False Positive（誤検知）対応

**重要**: false positiveでもIssue作成が必須。merge-checkがIssue参照のない却下をブロックする。

### 対応手順

1. **Issueを作成**（誤検知パターンの記録）
   ```bash
   gh issue create --title "AIレビュー誤検知: [パターン概要]" \
     --body "## 誤検知パターン\n[詳細]\n\n関連PR: #xxx" \
     --label "documentation"
   ```

2. **却下コメント自体にIssue参照を含める**
   ```text
   False positive: [理由の説明] (Issue #xxx)

   -- Claude Code
   ```

3. **スレッドをResolve**

### 大量の同一パターン（例: 17件）の場合

1. 1つのIssueで全件をカバー
2. 各コメントに同じIssue番号を参照
3. コメント編集で追記可能:
   ```bash
   gh api /repos/:owner/:repo/pulls/comments/{id} -X PATCH \
     -f body=$'False positive: [理由] (Issue #xxx)\n\n-- Claude Code'
   ```

> **⚠️ 重要: `gh pr comment` は使用禁止**
>
> `gh pr comment` で追加したPRコメントは、レビュースレッドへの返信として認識されない。
> `merge_check.py` は GraphQL の `reviewThreads.comments` のみをチェックするため、
> 通常のPRコメントでは「インライン返信なし」と判断されマージがブロックされる。

## 「Verified」コメント時の検証要件（重要）

**「Verified」と返信する前に、必ず実際のコードを読んで確認すること。**

「Verified」は「修正を確認した」という意味。コードを読まずに「Verified」と書くのは虚偽報告。

### 必須手順

1. **対象ファイルをReadツールで読む**
2. **指摘箇所の修正を目視で確認**
3. **以下の形式でコメント**:

```text
Verified: [具体的に何を確認したか]
- ファイル: [パス]:[行番号]
- 確認内容: [修正が適用されていることの説明]

-- Claude Code
```

### 良い例

```text
Verified: タブ区切りによるパース修正を確認
- ファイル: .claude/hooks/worktree_removal_check.py:91-110
- 確認内容: `--format=%ct\t%ar\t%s` でタブ区切り、`split("\t", 2)` で正しくパース

-- Claude Code
```

### 悪い例（禁止）

```text
Verified: 確認しました

-- Claude Code
```

↑コードを読んでいないことが明らか。具体的な確認内容がない。

### チェックリスト

「Verified」と書く前に:

- [ ] 対象ファイルをReadツールで読んだか
- [ ] 指摘された行番号/関数を確認したか
- [ ] 修正内容を具体的に説明できるか

## 設計判断で却下する場合のVerified形式（Issue #2490）

**修正せずに設計判断で却下する場合**も、`Verified:` 形式を使用する。

merge-checkは「Verified」または「False positive」キーワードで検証済みと判断する。

### 形式

```text
Verified: [設計判断の理由]
- 理由: [なぜ修正しないか]
- 確認: [現状の実装が適切である根拠]

-- Claude Code
```

### 使い分け

| 状況 | 形式 |
| ---- | ---- |
| コード修正した | `修正しました。Verified: [確認内容]` |
| 設計判断で却下 (修正しない) | `Verified: [設計判断の理由]` |
| 誤検知 (False Positive) | `False positive: [理由] (Issue #xxx)` |

## AIレビュー指摘への対応前チェックリスト（必須）

AIレビュー（Copilot/Codex）の指摘に対応する**前**に確認:

| チェック項目 | 確認方法 |
| ------------ | -------- |
| 指摘内容は正確か？ | 実際のコードを読んで確認 |
| 数値は正しいか？ | 自分で計算・カウントする |
| 行番号は正しいか？ | ファイルを開いて確認 |
| 変更の影響範囲は？ | 関連コードを確認 |
| **パラメータ削除提案の場合** | `rg "関数名"` で全ファイルを検索 |

**特に注意が必要なケース**:
- 文字数、行数、配列長などの数値指摘
- 「〜すべき」「〜が間違っている」などの断定的指摘
- 複数ファイルにまたがる変更提案
- **「未使用パラメータを削除すべき」「このパラメータは不要」などの提案**

## レビュースレッド解決の優先順位（Issue #2517, #2633）

複数のレビュースレッドを解決する際は、以下の優先順位でツールを選択する:

| 優先度 | ツール | 用途 | 理由 |
| ------ | ------ | ---- | ---- |
| 1 | **batch_resolve_threads.ts** | 全未解決スレッドに同じメッセージで一括解決 | 署名自動付与、resolve-thread-guardを回避 |
| 2 | review_respond.ts | 個別スレッドに異なるメッセージを投稿 | スレッドごとに対応内容が異なる場合 |
| 3 | GraphQL API | 最終手段 | resolve-thread-guardがブロックする可能性 |

**batch_resolve_threads.ts の使用例**:

```bash
# すべての未解決スレッドに同じメッセージを投稿してResolve
bun run .claude/scripts/batch_resolve_threads.ts {PR} "修正しました。Verified: [内容]"

# dry-runで対象スレッドを確認
bun run .claude/scripts/batch_resolve_threads.ts {PR} --dry-run
```

## 一括Resolve（GraphQL API）

```bash
# 未解決スレッドID取得
gh api graphql -f query='
query {
  repository(owner: "{owner}", name: "{repo}") {
    pullRequest(number: {PR}) {
      reviewThreads(first: 20) {
        nodes { id isResolved }
      }
    }
  }
}' --jq '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved == false) | .id'

# Resolve実行
for id in PRRT_xxx PRRT_yyy; do
  gh api graphql -f query="mutation { resolveReviewThread(input: {threadId: \"$id\"}) { thread { isResolved } } }"
done
```

## force-push/リベース後の対応

force-push/リベース後はCopilot/Codexが同じ内容で再レビューし、新スレッドが作成される。

### 自動重複解決機能（ci-monitor）

`ci_monitor_ts`はリベース前後で同一内容のスレッドを自動検出し、自動でResolveする機能を持つ。

**自動解決されないケース**:
- 行番号が変わった場合（正規化で対応するが限界あり）
- コメント内容が微妙に異なる場合
- 人間のレビュアーのコメント

### 手動対応が必要な場合

| 状況 | 対応 |
| ---- | ---- |
| 1-2件の重複 | 個別に返信してResolve |
| 3件以上の重複 | 一括対応スクリプトを使用 |
| 同一パターンが多数 | 1つのIssueで全件をカバー |

### 重要: 対応済みの指摘は再対応不要

リベース後に以前と同じ指摘が出た場合:

1. **前回対応済みか確認**（コミット履歴やResolve済みスレッドを確認）
2. **対応済みなら**: 同じ修正を再度行う必要はない
3. **未対応なら**: 通常通り対応

### 返信テンプレート（対応済みの場合）

**重要**: 必ず「Verified:」キーワードを含める。

```text
Verified: 既にコミット [コミットハッシュ] で対応済み。

-- Claude Code
```

または:

```text
Verified: 前回のレビューで対応済み（Issue #xxx として登録）。

-- Claude Code
```

### 一括対応スクリプト

```bash
# 未解決AIレビュースレッドを一覧取得
gh api graphql -f query='
query {
  repository(owner: "{owner}", name: "{repo}") {
    pullRequest(number: {PR}) {
      reviewThreads(first: 50) {
        nodes {
          id
          isResolved
          comments(first: 1) {
            nodes { author { login } body }
          }
        }
      }
    }
  }
}' --jq '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved == false) | select(.comments.nodes[0].author.login? | test("github-actions\\[bot\\]|codex"; "i")) | .id'
```

```bash
# 一括返信とResolve（対応済みの場合）
set -e
for id in PRRT_xxx PRRT_yyy PRRT_zzz; do
  # 返信
  gh api graphql -f query="mutation { addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: \"$id\", body: \"リベース前に対応済み。\n\n-- Claude Code\"}) { comment { id } } }"
  # Resolve
  gh api graphql -f query="mutation { resolveReviewThread(input: {threadId: \"$id\"}) { thread { isResolved } } }"
done
```
