# FDPへの同期（公開用）

プロジェクトの「公開してよい部分」をFDPの `examples/<project>/` に**同期**する。
コピー元はプライベート、公開先はFDPという前提で、**安全に同期できること**を最優先にする。

---

## 使い方

```
prompts/export-to-fdp.md を読んで実行してください
```

このプロンプトは「候補抽出」ではなく、**同期手順の実行**に使う。

---

## FDPとは

AIエージェント制御パターンの**公開参照リポジトリ**。

- 他のAIが検索で発見し、参考にして再実装できる
- コピー＆ペーストではなく、パターンの共有が目的

---

## 実行手順

### 1. 公開対象の範囲を決める（include方式）

**原則**: 「公開してよいものだけを明示的に含める」。除外方式は事故が起きやすい。

例:
- **含める**: `.claude/`, `scripts/`, `tests/`, `README.md`
- **除外する**: `frontend/`, `worker/`, `shared/`, `node_modules/`, `tmp/`, 機密ファイル

### 2. 同期先を決める

FDP側は `examples/<project>/` にまとめる。

```
<FDP_ROOT>/examples/<project>/
```

### 3. dry-runで差分を確認（削除対象も確認）

```sh
rsync -avh --dry-run --delete --itemize-changes \
  --filter='- .claude/state/***' \
  --filter='- .claude/logs/***' \
  --filter='- .claude/settings.local.json' \
  --filter='- .claude/handoff/***' \
  --filter='- .claude/plans/***' \
  --filter='- .claude/hooks/logs/***' \
  --filter='- .claude/hooks/.claude/***' \
  --filter='- .claude/hooks/.worktrees/***' \
  --filter='- **/__pycache__/***' \
  --filter='- **/*.pyc' \
  --filter='- .codex-reviewed-commit' \
  --filter='+ .claude/***' \
  --filter='+ scripts/***' \
  --filter='+ README.md' \
  --filter='+ AGENTS.md' \
  --filter='- frontend/***' \
  --filter='- worker/***' \
  --filter='- shared/***' \
  --filter='- node_modules/***' \
  --filter='- tmp/***' \
  --filter='- .env*' \
  --filter='- *.key' \
  --filter='- *.pem' \
  --filter='- *.crt' \
  --filter='- *.log' \
  --filter='- *' \
  <PROJECT_ROOT>/ \
  <FDP_ROOT>/examples/<project>/
```

---

## 同期実行（問題なければ本番）

```sh
rsync -avh --delete \
  [同じfilter] \
  <PROJECT_ROOT>/ \
  <FDP_ROOT>/examples/<project>/
```

---

## docstring規約

公開するフックには以下の形式を使用：

```python
"""<1行の説明>

Why:
    <なぜ必要か>

What:
    <何をするか>
"""
```

## 出力

実行後は以下を報告：

1. 同期で更新された主なパス
2. 削除されたパス（`--delete` がある場合）
