# マージ前チェックリスト

PRマージ前に確認すべき項目。

## チェックリスト

- [ ] `requested_reviewers` にCopilot/Codexがいない（レビュー完了）
- [ ] 全レビューコメントを確認
- [ ] 全スレッドがResolve済み
- [ ] 対応コメントに `-- Claude Code` 署名あり

## 確認コマンド

```bash
# レビュー進行中確認
gh api /repos/{owner}/{repo}/pulls/{PR} --jq '.requested_reviewers[].login'

# 未解決スレッド確認
gh api graphql -f query='
query {
  repository(owner: "{owner}", name: "{repo}") {
    pullRequest(number: {PR}) {
      reviewThreads(first: 50) {
        nodes {
          isResolved
          comments(first: 1) {
            nodes { body }
          }
        }
      }
    }
  }
}' --jq '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved == false)'
```

## マージブロックの原因

`merge_check.py` がマージをブロックする主な原因:

| 原因 | 解決策 |
| ---- | ------ |
| 未解決スレッドあり | スレッドをResolve |
| 署名なしの返信 | `-- Claude Code` 署名を追加 |
| Issue参照なしの却下 | Issueを作成して参照を追加 |
| レビュー進行中 | レビュー完了を待つ |
