---
name: code-review
description: AIレビュー（Copilot/Codex）の確認方法、コメント対応、Resolve手順。レビュー、Copilot、Codex、コメント確認、Resolve時に使用。
---

# コードレビュー対応

GitHub Copilot / Codex Cloud / Codex CLI のレビュー対応手順。

## AIレビュー一覧

| レビュアー      | 実行場所   | トリガー                  |
| --------------- | ---------- | ------------------------- |
| GitHub Copilot  | GitHub     | 自動（PR ready時）        |
| Codex Cloud     | GitHub     | `@codex review` コメント  |
| Codex CLI       | ローカル   | `codex review --base main` |

## レビュー確認方法

```bash
# レビューコメント確認（必須）
gh api /repos/{owner}/{repo}/pulls/{PR}/comments --jq '.[] | {path, line, body}'

# レビュー進行中確認（Copilot/Codexがいたらマージ待機）
gh api /repos/{owner}/{repo}/pulls/{PR} --jq '.requested_reviewers[].login'
```

**重要**: `requested_reviewers` に `Copilot` や `codex` がいたらレビュー進行中。マージを待つこと。

> **注**: `{owner}`, `{repo}`, `{PR}` は実際の値に置き換え。`gh api` は `{owner}/{repo}` を自動解決するため、そのまま使用可能。

## Codex CLIローカルレビュー

PRプッシュ前に実行:

```bash
# mainとの差分をレビュー
codex review --base main

# 未コミット変更をレビュー
codex review --uncommitted

# 特定観点でレビュー
codex review --base main --instructions "セキュリティを重点チェック"
```

### 設計品質レビュー（推奨）

結合度・凝集度・SRPなどの設計原則を重点的にチェック:

```bash
# 設計品質レビュー（結合度・凝集度・SRP含む）
.claude/scripts/codex-design-review.sh

# セキュリティ重点レビュー
.claude/scripts/codex-design-review.sh --security

# 結合度重点レビュー
.claude/scripts/codex-design-review.sh --coupling

# 凝集度重点レビュー
.claude/scripts/codex-design-review.sh --cohesion

# 未コミット変更のレビュー
.claude/scripts/codex-design-review.sh --uncommitted
```

プロンプトは `.claude/docs/design-review-prompt.txt` で定義。

## レビューコメントの対応

### 範囲内/範囲外の判断基準

**最重要原則: このPRで導入したバグは、このPRで修正する**

レビューで発見されたバグを別Issueにしてマージするのは間違い。バグ込みでマージすることになる。

#### バグの発生源で判断

| バグの発生源 | 対応 | 理由 |
| ------------ | ---- | ---- |
| **このPRで書いたコード** | 同じPRで修正（必須） | バグ込みでマージしない |
| **既存コード（偶然発見）** | 別Issue作成 | PRスコープ外 |

**具体例**:
- ✅ このPRで追加した関数にバグ → 同じPRで修正
- ✅ このPRで追加した関数のテスト不足 → 同じPRでテスト追加
- ✅ このPRで追加した機能のエッジケース未対応 → 同じPRで対応
- ❌ 既存の認証処理にバグ発見 → 別Issue

**アンチパターン（実際に発生した問題: PR #1126）**:
```
1. PR #100 で新機能を実装
2. レビューで「このコードにバグがある」と指摘
3. 「範囲外」としてIssue #101 を作成  ← ❌ 間違い
4. PR #100 をバグ込みでマージ         ← ❌ 問題
5. Issue #101 は未解決のまま          ← ❌ 放置
```

正しいフロー:
```
1. PR #100 で新機能を実装
2. レビューで「このコードにバグがある」と指摘
3. 同じPR #100 でバグを修正           ← ✅ 正しい
4. 修正後にマージ                     ← ✅ 品質担保
```

#### 範囲外と判断してよいケース

`ci_monitor.py` が自動分類するが、手動判断が必要な場合:

| 分類 | 条件 | 対応 |
| ---- | ---- | ---- |
| **範囲内** | PRで変更したファイルへの指摘 | このPRで修正 |
| **範囲内** | PR目的に直接関係する改善 | このPRで修正 |
| **範囲外** | 未変更ファイルへの既存バグ | Issue作成 |
| **範囲外** | 大規模リファクタリング提案 | Issue作成 |
| **範囲外** | アーキテクチャ変更提案 | Issue作成 |

**判断に迷ったら**:
- 修正が5行以内 → 範囲内として対応
- 修正が複数ファイルに波及 → Issue作成
- PRスコープが大きくなりすぎる → Issue作成

### 「範囲外」判断の厳格化（Issue #1192）

**原則**: 迷ったら修正を優先

「範囲外」と判断する前に以下の基準で評価:

| 状況 | 対応 | 理由 |
| ---- | ---- | ---- |
| **修正可能（数分で対応可能）** | 修正する | 先送りは非効率 |
| **設計変更が必要** | Issue作成 | PRスコープを超える |
| **本当にPRスコープ外** | 「範囲外」+ Issue参照必須 | merge-checkがブロック |

**「範囲外」にしてはいけないケース**:

以下の理由で「範囲外」とするのは不適切:

| 安易な理由 | なぜダメか | 正しい対応 |
| ---------- | ---------- | ---------- |
| 「ドキュメントだから省略形でOK」 | ドキュメントも品質基準の対象 | 修正する |
| 「サンプルコードだから厳密でなくてよい」 | サンプルは模範を示すべき | 修正する |
| 「軽微な問題だから後で」 | 軽微なら数分で修正可能 | 修正する |
| 「今回は時間がない」 | 技術的負債の先送り | 修正する |

**背景**: PR #1185で、修正可能なレビュー指摘を安易に「対象外」としてマージ後に別PRで修正する非効率な流れが発生した

**「範囲外」と判断する前のチェック**:

以下に該当する場合は「範囲外」ではなく「このPRで対応すべき」:

- [ ] このPRで書いたコードのバグではないか？
- [ ] このPRで追加した機能のテストではないか？
- [ ] このPRで追加した機能のエッジケース対応ではないか？
- [ ] このPRで追加した機能の誤検知防止ではないか？

例: 新規関数を追加した場合、その関数のバグ修正・テスト追加は「範囲外」ではない

### 対応フロー

1. コメント内容を確認
2. **AIレビュー指摘の検証**（下記チェックリスト参照）
3. **範囲内/範囲外を判断**（ci_monitor.pyの分類を参照）
4. **ultrathink** して評価（妥当か、修正すべきか、却下すべきか）
5. 修正 or 却下理由をコメント
6. Resolveする
7. **レビュー品質を記録**（下記参照）

### レビュー品質の記録（Issue #610）

レビューコメントへの対応結果を記録し、AIレビューの品質を追跡する。

**自動記録**: 以下は自動でログに記録される
- Copilot/Codex Cloud のコメント → `ci_monitor.py` が REVIEW_COMPLETED 時に記録
- Codex CLI のコメント → `codex_review_output_logger.py` が実行後に記録

**手動記録**: 対応結果（resolution/validity）は以下のスクリプトで記録

```bash
# 採用した場合
python3 .claude/scripts/record_review_response.py \
  --pr {PR} --comment-id {COMMENT_ID} --resolution accepted

# 却下した場合（理由付き）
python3 .claude/scripts/record_review_response.py \
  --pr {PR} --comment-id {COMMENT_ID} --resolution rejected \
  --validity invalid --reason "誤検知: 既存コードで問題なし"

# Issue作成した場合
python3 .claude/scripts/record_review_response.py \
  --pr {PR} --comment-id {COMMENT_ID} --resolution issue_created --issue {ISSUE_NUMBER}

# カテゴリを上書き（自動推定が不正確な場合）
python3 .claude/scripts/record_review_response.py \
  --pr {PR} --comment-id {COMMENT_ID} --resolution accepted --category security
```

**分析**: レビュー品質の統計を確認

```bash
# サマリー表示
python3 .claude/scripts/analyze_review_quality.py

# レビュアー別統計
python3 .claude/scripts/analyze_review_quality.py --by-reviewer

# カテゴリ別統計
python3 .claude/scripts/analyze_review_quality.py --by-category

# 期間指定
python3 .claude/scripts/analyze_review_quality.py --since 2025-12-01

# JSON出力
python3 .claude/scripts/analyze_review_quality.py --json
```

### 修正後の即時対応（重要）

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

**なぜ修正作業をバッチ処理しないのか**（返信ツールの話ではない）:
- 複数コメント対応時、どの修正がどのコメントに対応するか不明確になる
- 漏れが発生しやすい
- 後からまとめて対応すると、コミットとの対応関係が曖昧になる

**注**: 「返信の一括送信」（`batch_resolve_threads.py`）は別の話。全スレッドに同じメッセージで返信する場合は一括ツールが効率的。

**自動化スクリプト**:

```bash
# review_respond.py を使用（PR番号、コメントID、スレッドID、返信内容を指定）
python3 .claude/scripts/review_respond.py {PR} {COMMENT_ID} {THREAD_ID} "修正内容"
```

### レビュー対応ツールの選択（Issue #1011, #2633）

**状況に応じて適切なツールを選択する**:

| 状況 | 推奨ツール | 理由 |
| ---- | ---------- | ---- |
| **複数スレッドに同じメッセージ** | `batch_resolve_threads.py` | 一括解決、署名自動付与 |
| **個別スレッドに異なる対応** | `review_respond.py` | 個別対応、署名自動付与 |
| **上記で対応できない特殊ケース** | GraphQL API | 柔軟だがブロックリスク |

**⚠️ 重要**: 「レビュースレッド解決の優先順位」セクション（後述）も参照。

**重複コメント防止**:

以下のシナリオで重複が発生する:
1. REST API でコメント投稿（署名なし）
2. `resolve-thread-guard` がブロック
3. GraphQL で署名付きコメントを再投稿
4. → 同じ内容のコメントが2件

**対策**: 最初から署名付きでスクリプトを使用する。

```bash
# 状況: 複数のスレッドに同じ内容で一括返信・解決する場合
python3 .claude/scripts/batch_resolve_threads.py {PR} "修正しました。Verified: [内容]"

# 状況: スレッドごとに異なる内容で返信する場合
python3 .claude/scripts/review_respond.py {PR} {COMMENT_ID} {THREAD_ID} "対応内容"
```

**⚠️ GraphQL/REST APIを直接使う場合の注意**:
- 必ず署名を含める: `body: "対応内容\n\n-- Claude Code"`
- ブロックされた場合、同じコメントを再投稿しない
- エラー時はスクリプトに切り替える

### レビュー対応コメントの標準テンプレート（Issue #1182）

**1コメントで修正報告と検証報告を完結させる**。

現状の問題:
- 「対応済み」と「Verified」を別コメントで追加 → 非効率
- merge-checkは両方を要求 → 1スレッドに2回コメントが必要

**解決策: 統合テンプレート**

| 状況 | 形式 |
| ---- | ---- |
| 修正完了 | 修正しました。Verified: [確認した修正内容] |
| 確認のみ | Verified: [確認内容] (ファイル:行番号) |
| 却下 | False positive: [理由] (Issue #xxx) |

※ 全テンプレートに `-- Claude Code` 署名が必要（下記の例を参照）

**例（推奨形式）**:

```text
修正しました。Verified: タブ区切りパースの修正を確認
- ファイル: .claude/hooks/worktree_removal_check.py:91-110
- 変更: `split("\t", 2)` でタブ区切りを正しくパース

-- Claude Code
```

**効率化のポイント**:
- 「対応済み」と「Verified」を別々にコメントする必要なし
- 1コメントで `merge-check` の両方の要件を満たす
- 具体的な確認内容を含めることで検証の質も担保

### 返信時の署名ルール（必須）

**すべてのレビューコメントへの返信には `-- Claude Code` 署名を含める**。

署名がないと `merge_check.py` がマージをブロックする（Claude Codeの返信として認識されないため）。

```text
[対応内容や却下理由]

-- Claude Code
```

**署名の目的**:
- Claude Codeによる対応であることを明示
- `merge_check.py` がスレッドの対応状況を追跡可能に
- レビュー履歴の一貫性を保つ

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

**「Verified」でも修正しない場合はIssue必須**。検証した結果「修正不要」と判断した場合も、その判断を記録するためにIssue作成が必要。

参照: PR #957で「Verified: ... 本PRのスコープ外」と書いてブロックされた事例

### False Positive（誤検知）対応

AIレビューが誤った指摘をした場合の対応手順。

**重要**: false positiveでもIssue作成が必須。merge-checkがIssue参照のない却下をブロックする。

#### 対応手順

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

#### 大量の同一パターン（例: 17件）の場合

1. 1つのIssueで全件をカバー
2. 各コメントに同じIssue番号を参照
3. コメント編集で追記可能:
   ```bash
   gh api /repos/:owner/:repo/pulls/comments/{id} -X PATCH \
     -f body=$'False positive: [理由] (Issue #xxx)\n\n-- Claude Code'
   ```

#### 注意事項

- **PRコメントや返信の追加では不十分**: 元の却下コメント自体にIssue参照が必要
- merge-checkは個別コメントをチェックするため、元コメントの編集が必須
- 参照: Issue #793（PR #782での誤検知対応事例）

> **⚠️ 重要: `gh pr comment` は使用禁止**
>
> `gh pr comment` で追加したPRコメントは、レビュースレッドへの返信として認識されない。
> `merge_check.py` は GraphQL の `reviewThreads.comments` のみをチェックするため、
> 通常のPRコメントでは「インライン返信なし」と判断されマージがブロックされる。
>
> **必ず以下のいずれかを使用**:
> - `review_respond.py` スクリプト（推奨）
> - GraphQL の `addPullRequestReviewThreadReply` mutation
>
> **注**: REST API の `in_reply_to` パラメータは動作しない（HTTP 422）。`/replies` エンドポイント（`review_respond.py` が使用）は動作する。（PR #957で確認）

### 「Verified」コメント時の検証要件（重要）

**「Verified」と返信する前に、必ず実際のコードを読んで確認すること。**

「Verified」は「修正を確認した」という意味。コードを読まずに「Verified」と書くのは虚偽報告。

#### 必須手順

1. **対象ファイルをReadツールで読む**
2. **指摘箇所の修正を目視で確認**
3. **以下の形式でコメント**:

```text
Verified: [具体的に何を確認したか]
- ファイル: [パス]:[行番号]
- 確認内容: [修正が適用されていることの説明]

-- Claude Code
```

#### 良い例

```text
Verified: タブ区切りによるパース修正を確認
- ファイル: .claude/hooks/worktree_removal_check.py:91-110
- 確認内容: `--format=%ct\t%ar\t%s` でタブ区切り、`split("\t", 2)` で正しくパース

-- Claude Code
```

#### 悪い例（禁止）

```text
Verified: 確認しました

-- Claude Code
```

↑コードを読んでいないことが明らか。具体的な確認内容がない。

#### チェックリスト

「Verified」と書く前に:

- [ ] 対象ファイルをReadツールで読んだか
- [ ] 指摘された行番号/関数を確認したか
- [ ] 修正内容を具体的に説明できるか

### 設計判断で却下する場合のVerified形式（Issue #2490）

**修正せずに設計判断で却下する場合**も、`Verified:` 形式を使用する。

merge-checkは「Verified」または「False positive」キーワードで検証済みと判断する。設計判断による却下は`Verified:`を使用する（誤検知の場合は「False Positive対応」セクション参照）。

#### 形式

```text
Verified: [設計判断の理由]
- 理由: [なぜ修正しないか]
- 確認: [現状の実装が適切である根拠]

-- Claude Code
```

#### 良い例

```text
Verified: プロンプトベース検出を意図的に選択
- 理由: LLMの柔軟な判断力を活用するため、正規表現パターンマッチングではなくプロンプトフックを採用
- 確認: settings.jsonでtype: "prompt"として実装済み、テストで動作確認済み

-- Claude Code
```

```text
Verified: 4スペースインデントは設計上の選択
- 理由: 複数行JSONの可読性向上のため
- 確認: 同ファイル内の他の複数行JSON（L50, L80）と統一されている

-- Claude Code
```

#### 悪い例（禁止）

```text
設計判断として現状を維持します。

-- Claude Code
```

↑ 「Verified」がないため、merge-checkが「検証コメントなし」と判断してブロックする。

#### 使い分け

| 状況 | 形式 |
| ---- | ---- |
| コード修正した | `修正しました。Verified: [確認内容]` |
| 設計判断で却下 (修正しない) | `Verified: [設計判断の理由]` |
| 誤検知 (False Positive) | `False positive: [理由] (Issue #xxx)` |

**背景**: PR #2482でmerge-checkが2回ブロック。設計判断による却下コメントに「Verified」が含まれていなかったため。

### AIレビュー指摘への対応前チェックリスト（必須）

AIレビュー（Copilot/Codex）の指摘に対応する**前**に確認:

| チェック項目 | 確認方法 |
| ------------ | -------- |
| 指摘内容は正確か？ | 実際のコードを読んで確認 |
| 数値は正しいか？ | 自分で計算・カウントする |
| 行番号は正しいか？ | ファイルを開いて確認 |
| 変更の影響範囲は？ | 関連コードを確認 |

**特に注意が必要なケース**:
- 文字数、行数、配列長などの数値指摘
- 「〜すべき」「〜が間違っている」などの断定的指摘
- 複数ファイルにまたがる変更提案

**対応コメントの書き方**（数値指摘の場合）:

```text
検証済み: [AIの指摘内容] → [実際に確認した結果]
- 確認方法: [どうやって確認したか]
修正内容: [具体的な変更]

-- Claude Code
```

**背景**: PR #851でCopilotが「33文字」と指摘したが実際は32文字だった。AIレビューを盲信して修正→テスト失敗。数値を含む指摘は必ず自分で検証すること。

### レビュースレッド解決の優先順位（Issue #2517, #2633）

**注**: 「レビュー対応ツールの選択」セクション（前述）と統一された基準です。

複数のレビュースレッドを解決する際は、以下の優先順位でツールを選択する:

| 優先度 | ツール | 用途 | 理由 |
| ------ | ------ | ---- | ---- |
| 1 | **batch_resolve_threads.py** | すべての未解決スレッドに同じメッセージで一括解決 | 署名自動付与、resolve-thread-guardを回避 |
| 2 | review_respond.py | 個別スレッドに異なるメッセージを投稿 | スレッドごとに対応内容が異なる場合 |
| 3 | GraphQL API | 最終手段 | resolve-thread-guardがブロックする可能性 |

**batch_resolve_threads.py の使用例**:

```bash
# すべての未解決スレッドに同じメッセージを投稿してResolve
python3 .claude/scripts/batch_resolve_threads.py {PR} "修正しました。Verified: [内容]"

# dry-runで対象スレッドを確認
python3 .claude/scripts/batch_resolve_threads.py {PR} --dry-run
```

**注意**: スレッドごとに異なるメッセージが必要な場合は `review_respond.py` を使用する。

**なぜbatch_resolve_threads.pyを優先するか**:

- `resolve-thread-guard` はコメントなしのResolveをブロックする
- GraphQL APIで直接Resolveすると、署名忘れやコメント漏れでブロックされる
- `batch_resolve_threads.py` は署名を自動付与し、返信とResolveを一括実行

**背景**: PR #2514のセッションでresolve-thread-guardに12秒差で連続ブロックされた。GraphQL APIで直接Resolveを試みたが、batch_resolve_threads.pyを最初から使うべきだった。

### 一括Resolve（GraphQL API）

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

`ci_monitor.py`はリベース前後で同一内容のスレッドを自動検出し、自動でResolveする機能を持つ。

**仕組み**:
1. リベース前にResolve済みスレッドのハッシュを記録
2. リベース後に新スレッドをスキャン
3. 同一ハッシュのAIレビューコメントを自動Resolve

**ログ例**:
```json
{"message": "Auto-resolved duplicate thread: path/to/file.py", "path": "path/to/file.py", "hash": "abc123..."}
```

**自動解決されないケース**:
- 行番号が変わった場合（正規化で対応するが限界あり）
- コメント内容が微妙に異なる場合
- 人間のレビュアーのコメント

### 手動対応が必要な場合

自動解決されなかった重複コメントは手動で対応する。

#### 効率化のポイント（Issue #1674）

| 状況 | 対応 |
| ---- | ---- |
| 1-2件の重複 | 個別に返信してResolve |
| 3件以上の重複 | 一括対応スクリプトを使用 |
| 同一パターンが多数 | 1つのIssueで全件をカバー |

#### 一括対応スクリプト

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

### 重要: 対応済みの指摘は再対応不要

リベース後に以前と同じ指摘が出た場合:

1. **前回対応済みか確認**（コミット履歴やResolve済みスレッドを確認）
2. **対応済みなら**:
   - 同じ修正を再度行う必要はない
   - 「返信テンプレート（対応済みの場合）」を参照してコメントを残し、スレッドをResolveしてください。
3. **未対応なら**: 通常通り対応

**なぜ重複するのか**: GitHub Copilot/Codexは、リベースによって新しくなったコミットを過去のレビュー履歴とは無関係に評価するため、重複した指摘が発生します。これはGitHubの仕様に起因するもので、ツール側での制御は困難です。

### 重複コメント対応手順

1. 新コメントを確認（元のコメントと同じ内容か確認）
2. **返信コメントを追加**（既に対応済みでもコメント必須）
3. Resolveする

**重要**: コメントなしでResolveするとmerge-checkがブロックする。

### 返信テンプレート（対応済みの場合）

```text
既にコミット [コミットハッシュ] で対応済み。

-- Claude Code
```

または:

```text
前回のレビューで対応済み（Issue #xxx として登録）。

-- Claude Code
```

### 手順例（個別対応）

```bash
# 1. 未解決スレッドとそのIDを取得
gh api graphql -f query='
query {
  repository(owner: "{owner}", name: "{repo}") {
    pullRequest(number: {PR}) {
      reviewThreads(first: 50) {
        nodes {
          id
          isResolved
          comments(first: 1) {
            nodes { databaseId body }
          }
        }
      }
    }
  }
}' --jq '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved == false) | {threadId: .id, body: .comments.nodes[0].body[0:50]}'

# 2. 各スレッドに返信を追加（{THREAD_ID}は手順1で取得したthreadId）
gh api graphql -f query='
mutation {
  addPullRequestReviewThreadReply(input: {
    pullRequestReviewThreadId: "{THREAD_ID}",
    body: "既にコミット [ハッシュ] で対応済み。\n\n-- Claude Code"
  }) {
    comment { id }
  }
}'

# 3. スレッドをResolve（{THREAD_ID}は手順2と同じ）
gh api graphql -f query="mutation { resolveReviewThread(input: {threadId: \"{THREAD_ID}\"}) { thread { isResolved } } }"
```

**注**: 複数スレッドがある場合は、手順2-3を各スレッドに対して繰り返す。

## 冗長コード検出パターン

レビュー時に注意すべき冗長なコードパターン。

### Python

| パターン | 問題 | 修正 |
| -------- | ---- | ---- |
| `patch.dict(os.environ, {}, clear=True)` + `os.environ.pop(key, None)` | `clear=True`で既に空 | `pop`を削除 |
| `if x is not None: return x` + `return None` | 冗長な分岐 | `return x`のみ |
| `x = []; for item in items: x.append(item)` | リスト生成を簡潔に | `x = list(items)` |
| `try: ... except Exception: pass` | 例外を握りつぶし | 適切なログ/エラー処理を追加 |

### TypeScript/JavaScript

| パターン | 問題 | 修正 |
| -------- | ---- | ---- |
| `if (x !== null && x !== undefined)` | オプショナルチェーンで簡潔に | `if (x != null)` または `x?.` |
| `arr.filter(x => x).length > 0` | `some`で簡潔に | `arr.some(x => x)` |
| `Object.keys(obj).length === 0` | 直接判定可能 | `!Object.keys(obj).length` |
| `async () => { return await ... }`（※try/catch外） | 不要なawait | `async () => { return ... }` |

> **注意**: try-catch内での `return await` は例外を正しく捕捉するために必要な場合があります。

### テストコード

| パターン | 問題 | 修正 |
| -------- | ---- | ---- |
| モック設定後に同じ値を再設定 | 冗長な設定 | 一度だけ設定 |
| `setUp`で設定したものを各テストで再設定 | 重複設定 | `setUp`のみで設定 |
| 使われない変数の宣言 | 未使用コード | 削除または`_`プレフィックス |

### レビュー時のチェックポイント

- [ ] `clear=True` や `reset=True` 後の手動クリア処理はないか
- [ ] 同じ処理を複数回実行していないか
- [ ] ワンライナーで書けるものを複数行で書いていないか
- [ ] 到達不能なコード（early returnの後のコード等）はないか

## 既知の誤検知パターン

AIレビューで発生した誤検知パターンを記録する。同様のパターンが検出された場合、参照して適切に対応する。

### ドキュメント内のコード例を誤検知

| 発生 | パターン | 詳細 |
| ---- | -------- | ---- |
| PR #1651 | 「関数名が存在しません」 | ドキュメント内のコード例で使用された `print_continue_and_log_skip` を誤検知。実際には `.claude/hooks/common.py` に定義されている正しい関数名 |

**対応**: False positiveとして却下（修正不要）

**回避策**: ドキュメント内のコード例に対する「存在しない」系の指摘は、実際のコードベースで関数が定義されているか確認してから判断する。

### 静的解析の誤検知（dict is unhashable）

| 発生 | パターン | 詳細 |
| ---- | -------- | ---- |
| PR #1762 | 「dict is unhashable」 | `flow_def = definitions[flow_id]` のような辞書アクセスで、dict がキーとして使われていると誤認。実際は `flow_id`（文字列）がキー |

**対応**: False positiveとして却下（修正不要）

**回避策**: 「unhashable」系の静的解析警告は、実際に dict/list をキーとして使用しているか確認する。変数名やコンテキストから誤検知を判断できることが多い。

### ラッパー関数の引数順序スワップを誤検知

| 発生 | パターン | 詳細 |
| ---- | -------- | ---- |
| PR #2512 | 「Incorrect argument order」 | ラッパー関数で意図的に引数順序を変更している場合、AIレビューが「引数順序が間違っている」と誤検知。コメントで説明済みでも検出される |

**対応**: False positiveとして却下（修正不要）

**回避策**: ラッパー関数で引数順序を変更する場合、コードコメントで明示的に説明する。AIレビューが同様の指摘をした場合は、コメントを確認して誤検知と判断する。

### 異なる目的の関数間のパラメータ差異を誤検知

| 発生 | パターン | 詳細 |
| ---- | -------- | ---- |
| PR #2512 | 「Inconsistent parameter ordering」 | 異なる目的を持つ関連関数（例: `record_block` と `check_block_resolution`）のパラメータが異なることを「inconsistent」と誤認識 |

**対応**: False positiveとして却下（修正不要）

**回避策**: 関連する関数でパラメータセットが異なる場合、その理由（目的の違い）をdocstringで説明する。`record_block`は`reason`が必要（ブロック理由を記録）、`check_block_resolution`は`reason`不要（解決チェックのみ）のように、目的に応じたパラメータ差異は正しい設計。

### JSONLファイルにJSON配列対応を求める指摘

| 発生 | パターン | 詳細 |
| ---- | -------- | ---- |
| PR #2725 | 「Handle JSON-array transcripts」 | JSONL形式（.jsonl、1行1JSON）のファイルを処理するコードに対し、「JSON配列形式にも対応すべき」と指摘。Claude Codeのトランスクリプトは常にJSONL形式で保存されるため不要 |

**対応**: False positiveとして却下（修正不要）

**回避策**: JSONL形式のファイル処理では、ファイル拡張子（.jsonl）と用途（Claude Codeトランスクリプト）を確認する。`lib.transcript.load_transcript`のような別ライブラリがJSON配列をサポートしていても、直接ファイルを読む場合はJSONL前提で問題ない。

## マージ前チェックリスト

- [ ] `requested_reviewers` にCopilot/Codexがいない（レビュー完了）
- [ ] 全レビューコメントを確認
- [ ] 全スレッドがResolve済み
- [ ] 対応コメントに `-- Claude Code` 署名あり
