# dekita ファイル説明

生成日時: Sun Jan  4 14:45:32 JST 2026

## プロジェクト概要

dekita! - ハンズオン・ワークショップ向けのリアルタイム進捗共有ツール

## ファイル数統計

| カテゴリ | ファイル数 |
|----------|-----------|
| ルートファイル（設定・ドキュメント） | 19個 |
| .claude/hooks（実装） | 190個 |
| .claude/hooks/lib | 31個 |
| .claude/hooks/tests | 227個（テストディレクトリ） |
| .claude/scripts（実装） | 45個 |
| .claude/scripts/ci_monitor | 14個 |
| .claude/scripts/ci_monitor/tests | 12個（テストディレクトリ） |
| .claude/scripts/tests | 50個（テストディレクトリ） |
| .claude/scripts/dashboard | 3個 |
| .claude/skills | 8個 |
| .claude/prompts | 3個 |
| .claude/docs | 2個 |
| .claude/commands | 2個 |
| .claude/settings.json | 1個 |
| .github | 13個 |
| scripts（ルートレベル） | 8個 |
| tests（E2E） | 9個（テストディレクトリ） |
| **合計（__pycache__除外）** | **637個** |

## カテゴリ別ファイル

| ファイル | 説明 |
|----------|------|
| [01-root-files.md](./01-root-files.md) | ルートファイル（設定・ドキュメント）19個 |
| [02-claude-hooks.md](./02-claude-hooks.md) | .claude/hooks 実装ファイル 190個 |
| [03-claude-hooks-lib.md](./03-claude-hooks-lib.md) | .claude/hooks/lib ライブラリ 31個 |
| [04-claude-scripts.md](./04-claude-scripts.md) | .claude/scripts 実装ファイル 45個 |
| [05-claude-scripts-ci-monitor.md](./05-claude-scripts-ci-monitor.md) | .claude/scripts/ci_monitor モジュール 14個 |
| [06-claude-other.md](./06-claude-other.md) | .claude/その他（skills, prompts, docs, commands, settings）19個 |
| [07-github.md](./07-github.md) | .github/ ファイル 13個 |
| [08-scripts.md](./08-scripts.md) | scripts/ シェルスクリプト 8個 |
| [flows.md](./flows.md) | 実行フロー図（Mermaid） |

## テストディレクトリ

| ディレクトリ | ファイル数 | 種類 | 実行コマンド |
|-------------|-----------|------|--------------|
| `.claude/hooks/tests/` | 227個 | Pythonユニットテスト | `pytest .claude/hooks/tests/` |
| `.claude/scripts/tests/` | 50個 | Python/Bats テスト | `pytest .claude/scripts/tests/` |
| `.claude/scripts/ci_monitor/tests/` | 12個 | Pythonユニットテスト | `pytest .claude/scripts/ci_monitor/tests/` |
| `tests/` | 9個 | Playwright E2Eテスト | `pnpm test:e2e` |
