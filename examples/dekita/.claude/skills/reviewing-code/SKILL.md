---
name: reviewing-code
description: Handles AI code reviews from Copilot, Codex, Gemini, and others. Covers comment response, resolution procedures, and merge checklists. Use when reviewing PR comments, responding to AI reviewers, or resolving review threads.
---

# コードレビュー対応

GitHub上のAIレビュアーへの対応手順。

## 目次

| ファイル | 内容 |
|----------|------|
| [ai-reviewers.md](ai-reviewers.md) | AIレビュアー一覧、特性、Codex CLI |
| [review-process.md](review-process.md) | 範囲判断、対応フロー、品質記録 |
| [response-templates.md](response-templates.md) | 返信テンプレート、署名ルール、Resolve手順 |
| [false-positives.md](false-positives.md) | 既知の誤検知パターン |
| [merge-checklist.md](merge-checklist.md) | マージ前チェックリスト |

## クイックスタート: レビュー確認

**重要**: PRには**3種類**のコメントAPIがある。全て確認すること。

```bash
# 1. コード行コメント（インライン指摘）
gh api /repos/{owner}/{repo}/pulls/{PR}/comments --jq '.[] | {user: .user.login, path, line, body}'

# 2. PRレビュー全体（"No issues found" 等）
gh api /repos/{owner}/{repo}/pulls/{PR}/reviews --jq '.[] | {user: .user.login, state, body}'

# 3. 会話コメント（qodo Suggestions、greptile Overview 等）
gh api /repos/{owner}/{repo}/issues/{PR}/comments --jq '.[] | {user: .user.login, body}'

# レビュー進行中確認（Copilot/Codexがいたらマージ待機）
gh api /repos/{owner}/{repo}/pulls/{PR} --jq '.requested_reviewers[].login'
```

**重要**: `requested_reviewers` に `Copilot` や `codex` がいたらレビュー進行中。マージを待つこと。

## 対応フロー概要

```
1. コメント内容を確認
2. AIレビュー指摘を検証（response-templates.mdのチェックリスト参照）
3. 範囲内/範囲外を判断（review-process.md参照）
4. ultrathink して評価
5. 修正 or 却下理由をコメント
6. Resolveする
7. レビュー品質を記録
```

## 最重要原則

**このPRで導入したバグは、このPRで修正する**

レビューで発見されたバグを別Issueにしてマージするのは間違い。バグ込みでマージすることになる。

| バグの発生源 | 対応 | 理由 |
| ------------ | ---- | ---- |
| **このPRで書いたコード** | 同じPRで修正（必須） | バグ込みでマージしない |
| **既存コード（偶然発見）** | 別Issue作成 | PRスコープ外 |

## 返信時の署名ルール（必須）

**すべてのレビューコメントへの返信には `-- Claude Code` 署名を含める**。

```text
[対応内容や却下理由]

-- Claude Code
```

署名がないと `merge_check.py` がマージをブロックする。

## レビュー対応ツール

| 状況 | 推奨ツール |
| ---- | ---------- |
| 複数スレッドに同じメッセージ | `bun run .claude/scripts/batch_resolve_threads.ts {PR} "メッセージ"` |
| 個別スレッドに異なる対応 | `bun run .claude/scripts/review_respond.ts {PR} {COMMENT_ID} {THREAD_ID} "内容"` |

## 標準テンプレート

| 状況 | 形式 |
| ---- | ---- |
| 修正完了 | `修正しました。コミット [ハッシュ] で対応。Verified: [確認内容]` |
| 確認のみ | `Verified: [確認内容] (ファイル:行番号)` |
| 誤検知 | `False positive: [理由] (Issue #xxx)` |

**詳細は以下を参照**:

- [返信テンプレート](response-templates.md) - 詳細なテンプレートと例
- [誤検知パターン](false-positives.md) - 既知の誤検知一覧
- [マージ前チェック](merge-checklist.md) - マージ前の確認項目
