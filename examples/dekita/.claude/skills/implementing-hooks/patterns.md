# パターン検出ガイド

パターン検出フックの作成・変更時のガイドラインとチェックリスト。

## パターン検出フック作成ガイドライン

キーワードリストや正規表現パターンを使用してテキストを検出するフック（例: `defer_keyword_check.py`）を作成・変更する際のガイドライン。

### 実データ分析の重要性

仮説ベースでパターンを選定すると:
- **誤検知が多い**: 実際には問題ないケースをブロック
- **漏れが多い**: 本当に検出すべきパターンを見逃す
- **メンテナンス負荷**: 後から修正が必要になる

### 必須チェックリスト

パターン検出フックの作成・変更時は以下を確認:

- [ ] **実データソースを特定したか**
  - GitHub PR comments: `gh api repos/{owner}/{repo}/pulls/{pr}/comments`
  - Issue comments: `gh api repos/{owner}/{repo}/issues/{issue}/comments`
  - セッションログ: `~/.claude/logs/*.jsonl`

- [ ] **実データからパターンを抽出したか**
  - 仮説ベースではなく実際のデータを分析
  - 頻度・コンテキストを確認
  - 最低10件以上の実例を収集

- [ ] **作成したパターンをテストしたか**
  - 検出率（実際に検出すべきものを検出できているか）
  - 誤検知率（検出すべきでないものを検出していないか）
  - 目標: 検出率 > 90%、誤検知率 < 10%

### 分析ツール

`.claude/scripts/analyze_pattern_data.py` を使用:

```bash
# パターン検索（実データからマッチを確認）
python3 .claude/scripts/analyze_pattern_data.py search \
  --pattern "後で|将来|フォローアップ" \
  --show-matches

# 頻度分析（パターンの出現頻度を確認）
python3 .claude/scripts/analyze_pattern_data.py analyze \
  --pattern "スコープ外" \
  --days 30

# パターンリスト検証（複数パターンの精度を一括チェック）
python3 .claude/scripts/analyze_pattern_data.py validate \
  --patterns-file my-patterns.txt
```

### 実装時のベストプラクティス

1. **パターンリスト変数の命名**:
   ```python
   # 明確な命名で目的を示す
   DEFER_KEYWORDS = [...]  # 「後で」系キーワード
   SCOPE_OUT_PATTERNS = [...]  # スコープ外パターン
   ```

2. **実データ分析の証跡をコメントに残す**:
   ```python
   # 実データ分析: PR comments from 2025-12-30
   # 検出対象: Issue参照なしで使われると問題になるパターン
   # 分析結果: 30件中28件検出、誤検知2件
   DEFER_KEYWORDS = [...]
   ```

3. **除外コンテキストを考慮**:
   ```python
   # 誤検知防止: コードブロック、ドキュメント参照、ルール説明
   EXCLUDE_CONTEXTS = [
       r"```",  # コードブロック
       r"AGENTS\.md",  # ドキュメント参照
   ]
   ```

### 自動検出

`hook_change_detector.py` がパターン検出フックの変更を検知し、実データ分析チェックリストをリマインドします。

検出条件:
- `*_KEYWORDS`, `*_PATTERNS`, `*_REGEX` 変数を含む
- 正規表現パターンリストを含む
- `re.compile()` を含む

### 関連Issue

- Issue #1910: AskUserQuestion検出フック
- Issue #1911: 「後で」キーワード検出フック
- Issue #1912: パターン検出フック作成時の実データ分析強制

## ブロックパターン追加時のチェックリスト

新しいブロックパターンをフックに追加する際のチェックリスト。引用符内での誤検知問題を踏まえた防止策。

### 誤検知防止

- [ ] **引用符内のパターン**: `--body "..."` や `--title "..."` 内での言及を除外
  - 対策: 引用符内のコンテンツを除去するヘルパー関数を使用（下記実装例参照）
  - ❌ 悪い例: `if "gh run watch" in command:`
  - ✅ 良い例: `if "gh run watch" in strip_quoted_content(command):`

- [ ] **コメント内**: `# command は使わない` のような文脈
  - 対策: 行頭が `#` の場合は無視する処理を検討

- [ ] **変数展開**: `$command` が対象パターンを含む場合
  - 対策: `\b` で単語境界を明確にする

- [ ] **パイプ/リダイレクト**: `echo "..." | grep ...`
  - 対策: `strip_quoted_content()` でカバー

### パターン設計

- [ ] 正規表現を使用する場合、エスケープ漏れがないか
- [ ] 大文字小文字の区別が必要か確認
- [ ] 複数行コマンド対応が必要か

### テスト

- [ ] **正常系**: ブロックすべきコマンドがブロックされる
- [ ] **誤検知テスト**: 引用符内での言及がブロックされない
  ```python
  def test_approves_quoted_mention(self):
      """引用符内でのパターン言及は承認される."""
      command = 'gh pr comment --body "使用禁止: gh run watch"'
      result = should_block(command)
      assert result is False
  ```
- [ ] テスト追加先: `.claude/hooks/tests/test_<hook名>.py`

### 実装例

```python
import re

# NOTE: この関数はドキュメント用の簡略化サンプルです。
# 実際のフック実装では、.claude/hooks/ci_wait_check.py 内の strip_quoted_content を参照してください。
# そちらは文字単位のパースにより、エスケープされた引用符や未閉じ引用符などのエッジケースに対応しています。
def strip_quoted_content(text: str) -> str:
    """引用符内のコンテンツを簡易的に除去する.

    ダブルクォート/シングルクォートで囲まれた内容を空文字に置き換える。
    例: 'gh pr comment --body "使用禁止: gh run watch"' -> 'gh pr comment --body ""'

    注意: この実装は正規表現による簡略版であり、以下のエッジケースには対応していません:
        - 文字列外のエスケープされた引用符（例: \"foo\"）
        - 閉じられていない引用符
    実運用時は .claude/hooks/ci_wait_check.py の実装を使用してください。
    """
    return re.sub(r'(["\'])(?:\\.|(?!\1).)*\1', r'\1\1', text)

def should_block(command: str) -> bool:
    """Check if command should be blocked."""
    # 引用符内のコンテンツを除去してからチェック
    clean_command = strip_quoted_content(command)

    # ブロック対象パターン
    blocked_patterns = [
        r"\bgh\s+run\s+watch\b",
        r"\bgh\s+pr\s+checks\s+--watch\b",
    ]

    for pattern in blocked_patterns:
        if re.search(pattern, clean_command):
            return True
    return False
```

### 関連Issue

- Issue #1621: ブロックパターンチェックリストの追加

## git rev-list差分チェックのガイドライン

`git rev-list` でブランチ間の差分をチェックする場合、以下の3ケースを必ずテストする。

### 必須テストケース

| ケース | 状態 | 期待動作 |
|--------|------|----------|
| **ahead** | ローカルがリモートより進んでいる | 通常は許可 |
| **behind** | ローカルがリモートより遅れている | ブロックまたは警告 |
| **same** | 同一コミット | 許可 |

### アンチパターン

❌ **ハッシュ不一致でブロック**:
```python
# 悪い例: aheadでも誤ってブロック
if local_hash != remote_hash:
    return block()
```

✅ **behind_countでブロック**:
```python
# 良い例: behindの場合のみブロック
behind_count = get_behind_count()  # git rev-list main..origin/main
if behind_count > 0:
    return block()
```

### テストコード例

```python
def test_approves_when_local_is_ahead(self):
    """ローカルが進んでいる場合は許可"""
    with patch.object(hook, "get_behind_count", return_value=0):
        # behind=0 means local is same or ahead
        result = hook.main()
        self.assertEqual(result["decision"], "approve")

def test_blocks_when_local_is_behind(self):
    """ローカルが遅れている場合はブロック"""
    with patch.object(hook, "get_behind_count", return_value=3):
        result = hook.main()
        self.assertEqual(result["decision"], "block")

def test_approves_when_same(self):
    """同一コミットの場合は許可（behind_count=0で判定）"""
    # Note: behind_countベースの実装では、aheadとsameは同じ条件（behind_count=0）
    with patch.object(hook, "get_behind_count", return_value=0):
        result = hook.main()
        self.assertEqual(result["decision"], "approve")
```

### 関連Issue
- Issue #755: worktree-main-freshness-checkでの発見
- Issue #760: 本ガイドライン追加
