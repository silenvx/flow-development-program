---
name: authoring-skills
description: Guides the user through the end-to-end process of creating high-quality Agent Skills. Enforces evaluation-first design, checks for progressive disclosure compliance, and validates metadata. Use when you need to create a new Skill or refactor an oversized existing Skill.
---

# Skill作成ガイド

**対象モデル**: Claude Opus 4.5, Sonnet 4, Haiku 3.5
**最終更新**: 2026-01-20

Agent SkillsをAnthropicのBest practicesに準拠して作成・改善するためのガイド。

## Skill作成フロー

### 1. Evaluation first（必須）

**コードを書く前に評価基準を定義する**。これがBest practicesの最重要原則。

```bash
# 評価ガイドを確認
cat .claude/skills/authoring-skills/evaluation-guide.md
```

Evaluationレベル:

| レベル | 内容 | 必須度 |
|--------|------|--------|
| Level 1 | 期待する動作のチェックリスト | **必須** |
| Level 2 | 入力/出力ペア例（3つ程度） | 推奨 |
| Level 3 | モデル別テスト結果表 | 理想 |

### 2. Skillなしでタスク完了

Skill作成前に、一度Skillなしでタスクを完了する。これにより:

- 必要なコンテキストを特定できる
- 繰り返し使う指示を抽出できる
- 実際の課題を理解できる

### 3. SKILL.md作成

```bash
# ディレクトリ作成 + テンプレートをコピー
mkdir -p .claude/skills/my-skill
cp .claude/skills/authoring-skills/templates/SKILL-template.md .claude/skills/my-skill/SKILL.md
```

### 4. 品質チェック

```bash
# 自動チェック実行
.claude/skills/authoring-skills/check-skill.sh .claude/skills/my-skill/SKILL.md
```

### 5. テスト

実際のタスクでSkillを使用し、複数モデルで検証する。

---

## 品質チェックリスト

### 構造（必須）

- [ ] `name`: 64文字以内、小文字・数字・ハイフンのみ
- [ ] `description`: 1024文字以内、第三者視点、「what」+「when to use」
- [ ] SKILL.md本体: **500行以下**（推奨）、**1000行超は分割必須**

### 内容（必須）

- [ ] Evaluationを先に作成したか
- [ ] 具体的なワークフロー/ステップがあるか
- [ ] テンプレートまたは例を含むか

### 推奨事項

- [ ] 時間依存情報（「〜以降」「〜以前」）を避けているか
- [ ] 一貫した用語を使用しているか
- [ ] 対象モデルバージョンと最終更新日を記載したか

---

## 命名規則

| 形式 | 例 | 推奨度 |
|------|-----|--------|
| Gerund (verb+-ing) | `processing-pdfs`, `reviewing-code` | ✅ Good |
| Noun phrases | `pdf-processing`, `code-review` | ⚠️ Acceptable |
| Imperative | `process-pdfs`, `review-code` | ⚠️ Acceptable |

**禁止**:
- `anthropic`、`claude` を含む名前
- XMLタグを含む名前
- 64文字超

---

## Description作成

**形式**: 第三者視点 + 「what」 + 「when to use」

```text
# ❌ Bad（第一人称的）
セッションの行動を振り返り、五省で自己評価し、改善点をIssue化する。

# ✅ Good（第三者視点）
Reflects on session behavior using the Five Reflections framework,
performs self-evaluation, and creates Issues for improvements.
Use when session ends, /reflecting-sessions is invoked, or periodic self-assessment is needed.
```

**チェックポイント**:
1. 主語を明示しない（Claudeが主語と想定）
2. 「Use when...」でトリガー条件を明記
3. 具体的な機能を列挙

---

## Progressive Disclosure

500行を超える場合は分割が必要。詳細は:

```bash
cat .claude/skills/authoring-skills/progressive-disclosure.md
```

### 分割パターン

```
my-skill/
├── SKILL.md (概要 + ナビゲーション、~200行)
├── workflows.md (ワークフロー詳細)
├── templates.md (テンプレート集)
└── reference.md (リファレンス)
```

### 目次パターン（親ファイルに記載）

```markdown
## 詳細ガイド

| トピック | ファイル |
|----------|----------|
| ワークフロー | [workflows.md](workflows.md) |
| テンプレート | [templates.md](templates.md) |
| リファレンス | [reference.md](reference.md) |
```

---

## サンプルSkill

Best practicesに準拠したサンプルSkillを参照:

```bash
ls .claude/skills/authoring-skills/examples/sample-skill/
```

---

## 関連ファイル

| ファイル | 内容 |
|----------|------|
| [evaluation-guide.md](evaluation-guide.md) | 評価作成の詳細手順 |
| [progressive-disclosure.md](progressive-disclosure.md) | 分割ガイド |
| [check-skill.sh](check-skill.sh) | 自動チェックスクリプト |
| [templates/SKILL-template.md](templates/SKILL-template.md) | 新規Skill用テンプレート |

---

## 参考資料

- [Best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- [Agent Skills Overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
