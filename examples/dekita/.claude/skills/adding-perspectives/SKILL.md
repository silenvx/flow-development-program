---
name: adding-perspectives
description: Adds reflection perspectives to detect similar issues in future sessions. Learns from user feedback and systematizes detection. Use when [ACTION_REQUIRED: /adding-perspectives] appears, user points out overlooked issues, or new check criteria are needed.
---

# 振り返り観点追加ガイド

ユーザーからの指摘や問題発見時に、類似問題を将来の振り返りで検出できるよう観点を追加する手順。

## 使用タイミング

- `[ACTION_REQUIRED: /adding-perspectives]` が表示されたとき
- ユーザーから「動いてる？」「正常？」等の指摘を受けたとき
- 振り返りで新しいチェック観点が必要と判断したとき

## 手順

### 1. 問題の分析

まず、指摘された問題の根本原因を特定する。

```markdown
| 項目 | 内容 |
|------|------|
| 問題の概要 | 何が起きたか |
| 根本原因 | なぜ発生したか |
| 検出方法 | どうすれば事前に気づけたか |
```

### 2. 既存観点の確認

新しい観点が本当に必要か確認する。

```bash
# 既存の観点を確認（PERSPECTIVES配列全体を表示）
grep -A 100 "PERSPECTIVES = \[" .claude/hooks/reflection_self_check.py | head -150
```

既存観点で検出可能な場合は追加不要。キーワードの拡充で対応できる場合はキーワード追加のみ。

#### キーワード重複チェック

新しい観点のキーワード候補を既存観点と比較:

```bash
# 既存観点のキーワードを一覧
grep -A 20 '"keywords"' .claude/hooks/reflection_self_check.py | grep '"'

# 重複がないか目視確認
```

**重複がある場合**:

1. 既存観点の強化で対応できないか検討
2. 新規追加が必要な理由を明確化

**チェック観点**:

- 同じキーワードが既存観点に含まれていないか
- 類似の目的を持つ観点が既に存在しないか
- 既存観点のdescriptionを拡張することで対応可能か

### 3. 観点の定義

新しい観点が必要な場合、以下を定義する。

| フィールド | 説明 | 例 |
|-----------|------|-----|
| `id` | 一意の識別子（snake_case） | `ci_failure_analysis` |
| `name` | 日本語の表示名 | `CI失敗分析` |
| `description` | 確認すべき内容 | `CI失敗時に根本原因を分析したか` |
| `keywords` | 検出用キーワード（正規表現） | `[r"CI.*失敗", r"根本原因"]` |

### 4. reflection_self_check.py への追加

`.claude/hooks/reflection_self_check.py` の `PERSPECTIVES` 配列に追加:

```python
# Issue #XXXX: [問題の説明]
{
    "id": "new_perspective_id",
    "name": "観点の表示名",
    "description": "確認すべき内容の説明",
    "keywords": [
        r"キーワード1",
        r"キーワード2",
        r"複合.*パターン",
    ],
},
```

### 5. reflecting-sessions 観点チェックテーブルへの追加

`reflection_self_check.py`のPERSPECTIVESに追加した新観点を、`.claude/skills/reflecting-sessions/improvement-actions.md`のセクション6「改善点の洗い出し」の観点チェックテーブルにも反映する（以下の形式で行を追加）:

```markdown
| **新観点名** | 確認内容 | [ ] |
```

### 6. テストの追加

`.claude/hooks/tests/test_reflection_self_check.py` にテストを追加:

```python
def test_detects_new_perspective(self):
    """新観点が正しく検出される."""
    transcript = "キーワード1を含むテキスト"
    missing = get_missing_perspectives(transcript)
    perspective_ids = [p["id"] for p in missing]
    assert "new_perspective_id" not in perspective_ids
```

#### 6.1 既存テストへの影響確認（必須）

**重要**: 新観点追加後、以下の包括テストに新観点のキーワードが必要か確認する。

```bash
# 全観点をカバーするテストを検索
grep -n "all_perspectives_addressed" .claude/hooks/tests/test_reflection_self_check.py
```

**該当テストがある場合**:

- `test_all_perspectives_addressed`
- `test_no_warning_when_all_perspectives_addressed`

これらのテストは「全ての観点がカバーされている」ことを検証するため、テストのcontent文字列に新観点のキーワードを追加する必要がある。

**例**: `issue_auto_start_check`観点を追加した場合

```python
# 既存のcontent末尾に、新観点のキーワードを含む1行を追加
content = """...
該当プロンプトなし。
目的との整合性に問題なし。
Issue作成後に確認を求めることなし。  # ← 新観点用の行を追加
"""
```

**背景**: PR #2953 で新観点追加時にこの確認を怠り、CI失敗を招いた（Issue #2956）。

### 7. Issueの作成（任意）

大きな変更の場合はIssueを作成してからworktreeで作業する。

## キーワード設計のベストプラクティス

| 項目 | 推奨 |
|------|------|
| **複合パターン** | 単一キーワードより `r"CI.*失敗"` のような複合が誤検知を減らす |
| **正規表現** | `r"(Pre|Post|Stop)"` でOR条件も可能 |
| **網羅性** | 同じ意味の異なる表現を含める（例: 失敗、エラー、問題） |
| **テスト** | 実際のtranscriptでキーワードが検出されることを確認 |

## 追加しない方が良いケース

| ケース | 理由 |
|--------|------|
| 一回限りの特殊な問題 | 再発可能性が低い |
| 既存観点のキーワード拡充で対応可能 | 観点の重複を避ける |
| 主観的な評価基準 | キーワードで検出困難 |

## 実例

### Issue #2289: 「対応済み」判断の検証

**問題**: 「既に対応済み」と判断したが、実際には仕組みが有効に機能していなかった。

**追加した観点**:

```python
{
    "id": "already_handled_check",
    "name": "「対応済み」判断の検証",
    "description": "「対応済み」と判断した場合、その仕組みの実行タイミング（Pre/Post/Stop）を確認し、実際に有効か検証したか",
    "keywords": [
        r"対応済み.*検証",
        r"実行タイミング",
        r"(Pre|Post|Stop)",
        r"フック.*確認",
        r"仕組み.*有効",
        r"対応済み.*なし",
    ],
},
```

## チェックリスト

- [ ] 既存観点で対応できないか確認した
- [ ] `id`, `name`, `description`, `keywords` を定義した
- [ ] `reflection_self_check.py` の PERSPECTIVES に追加した
- [ ] `execute.md` のセクション8に追加した
- [ ] テストを追加した
- [ ] Pythonの構文エラーがないことを確認した
