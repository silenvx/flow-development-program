---
name: exploring-claude-code
description: Explores Claude Code features including Skills, Hooks, Slash Commands, and Subagents. Provides design guidelines for feature proposals. Use when investigating new features, proposing extensions, or confirming design principles.
---

# Claude Code機能調査ガイド

新機能調査・提案時のチェックリストと設計方針確認ガイド。

## 機能一覧

| カテゴリ | 機能 | 場所 | 実行方式 | 用途 |
|---------|------|------|----------|------|
| メモリ | CLAUDE.md | ルート（`.claude/`も可） | 自動適用 | プロジェクト指示 |
| 機能拡張 | **Skills** | `.claude/skills/` | 自動発見 | 文脈に応じた能力 |
| 機能拡張 | **Slash Commands** | `.claude/commands/` | 手動 `/cmd` | クイック操作 |
| 機能拡張 | **Subagents** | `.claude/agents/` | 自動+手動 | 専門化エージェント |
| イベント | **Hooks** | `.claude/settings.json` | 自動トリガー | 検証・フォーマット |
| 外部連携 | **MCP** | Claudeアプリ側設定 | ツール拡張 | 外部API連携 |

## 本プロジェクトの設計方針

### 自動実行優先

- **推奨**: Skills、Hooks（自動で発動）
- **非推奨**: Slash Commands、手動Subagents（ユーザー介入が必要）

### 現在使用中の機能

| 機能 | 使用状況 |
|------|----------|
| Skills | `managing-development`, `reviewing-code`, `reflecting-sessions`, `applying-standards`, `implementing-hooks` |
| Hooks | 多数設定済み（詳細は `implementing-hooks` Skill参照） |
| Slash Commands | `/reflecting-sessions`（振り返り実行） |
| Subagents | 未使用 |

## 新機能提案時のチェックリスト

### 1. 設計方針確認

- [ ] プロジェクトは「自動実行」方針か「手動実行」方針か？
- [ ] 既存の Skills/Hooks の設計パターンを確認したか？
- [ ] 提案する機能はその方針に適合するか？

### 2. 機能選択ガイド

```
Q: ユーザーが明示的に呼び出す必要があるか？
├─ はい → Slash Command（本プロジェクトでは非推奨）
└─ いいえ
    Q: イベント駆動で自動実行するか？
    ├─ はい → Hook
    └─ いいえ → Skill（コンテキストに応じて自動発見）
```

### 3. 実装前確認

- [ ] 既存機能で代替できないか確認したか？
- [ ] 責務の重複がないか確認したか？
- [ ] 必要最小限の実装になっているか？

## 各機能の詳細

### Skills（推奨）

**用途**: 文脈に応じて自動で発見・使用される能力

**構造**:
```
.claude/skills/my-skill/
├── SKILL.md (必須)
├── reference.md (オプション)
└── examples/ (オプション)
```

**SKILL.md形式**:
```yaml
---
name: skill-name
description: いつ使うか、何ができるか（発見に重要）
---

# スキル名

詳細な説明とガイダンス
```

**ポイント**:
- `description` は具体的に（発見精度に影響）
- 単一責任：1 Skill = 1 責務

### Hooks（推奨）

**用途**: イベント駆動の自動検証・フォーマット

**設定**: `.claude/settings.json`

**主要イベント**:
- `PreToolUse`: ツール実行前（ブロック可能）
- `PostToolUse`: ツール実行後（検証・記録）
- `Stop`: レスポンス完了時（評価・クリーンアップ）

**ポイント**:
- 単一責任：1 Hook = 1 責務
- 既存Hooksとの責務重複を避ける
- 詳細は `implementing-hooks` Skill参照

### Slash Commands（限定的使用）

**用途**: ユーザーが `/command` で明示的に呼び出す操作

**本プロジェクトでの使用**:
- 基本方針は「自動実行」のため、新規追加は慎重に
- `/reflecting-sessions`: 振り返りの明示的実行（意図的なタイミングで使用するため例外）

**追加基準**: ユーザーが「今このタイミングで」明示的に実行したい操作のみ

### Subagents（限定的使用）

**用途**: 専門化されたサブタスクの委譲

**本プロジェクトでの使用**:
- 組み込み Subagent（Explore、Plan）のみ使用
- カスタム Subagent は未使用

## 関連Skill

- `implementing-hooks`: Hook詳細仕様、設計原則
- `managing-development`: 開発フロー
- `applying-standards`: コーディング規約
