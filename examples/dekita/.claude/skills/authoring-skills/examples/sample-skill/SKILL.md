---
name: greeting-users
description: Responds to user greetings with appropriate responses based on time of day and context. Use when the user says hello, good morning, or similar greetings.
---

# ユーザー挨拶対応

**対象モデル**: Claude Opus 4.5, Sonnet 4, Haiku 3.5
**最終更新**: 2026-01-20

ユーザーからの挨拶に対して、時間帯やコンテキストに応じた適切な応答を行う。

---

## クイックスタート

ユーザーが「おはよう」「こんにちは」などの挨拶をしたら、このSkillが自動的に発動する。

---

## 応答パターン

| 挨拶 | 応答 |
|------|------|
| おはよう | 「おはようございます！今日も良い一日を。」 |
| こんにちは | 「こんにちは！何かお手伝いできることはありますか？」 |
| こんばんは | 「こんばんは！お疲れ様です。」 |
| Hello | 「Hello! How can I help you today?」 |

---

## チェックリスト

- [ ] 挨拶の言語を検出したか
- [ ] 時間帯に応じた応答を選択したか
- [ ] フォローアップの質問を含めたか

---

## Evaluation（このSkillの評価）

### Level 1: チェックリスト

- [ ] 「おはよう」→ 朝の挨拶で応答
- [ ] 「Hello」→ 英語で応答
- [ ] 挨拶以外の入力 → このSkillは発動しない

### Level 2: 入力/出力ペア

| 入力 | 期待する出力 |
|------|-------------|
| 「おはよう」 | 「おはようございます！今日も良い一日を。」 |
| 「Hello there」 | 「Hello! How can I help you today?」 |
| 「コードを書いて」 | このSkillは発動しない（挨拶ではない） |

---

## このサンプルのポイント

1. **description**: 第三者視点 + "Use when"トリガー条件
2. **行数**: 約60行（500行以下）
3. **構造**: クイックスタート → 詳細 → チェックリスト → Evaluation
4. **Evaluation含有**: Level 1/2の評価をSkill内に記載
