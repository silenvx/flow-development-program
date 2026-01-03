# トラブルシューティング

このドキュメントは開発中に発生する可能性のある問題とその解決策をまとめています。

## 目次

- [Worktree関連](#worktree関連)
  - [Worktree削除後にシェルが操作不能になる](#worktree削除後にシェルが操作不能になる)
  - [Bashコマンドでカレントディレクトリが変わる](#bashコマンドでカレントディレクトリが変わる)
  - [Worktree使用中にgh pr mergeがエラーになる](#worktree使用中にgh-pr-mergeがエラーになる)
  - [Worktree内でフック変更が反映されない](#worktree内でフック変更が反映されない)
  - [git rev-parseの戻り値がworktreeで異なる](#git-rev-parseの戻り値がworktreeで異なる)
- [Claude Code関連](#claude-code関連)
  - [削除済みフックでエラーが発生し続ける](#削除済みフックでエラーが発生し続ける)
- [Agent CLI関連](#agent-cli関連)
  - [Gemini CLI で 404 エラーが発生する](#gemini-cli-で-404-エラーが発生する)
- [CI/CD関連](#cicd関連)
  - [Cloudflare Pages デプロイで UTF-8 エラーが発生する](#cloudflare-pages-デプロイで-utf-8-エラーが発生する)
  - [パッケージマネージャー移行時のCI失敗](#パッケージマネージャー移行時のci失敗)
  - [Status Check と skipped ジョブの動作](#status-check-と-skipped-ジョブの動作)
  - [ワークフローファイル変更時の制限](#ワークフローファイル変更時の制限)
  - [CIが開始されない場合](#ciが開始されない場合)

---

## Worktree関連

### Worktree削除後にシェルが操作不能になる

**症状**: Worktree内で作業中にディレクトリが削除されると、シェルセッションが壊れて全コマンドが `Exit code 1` で失敗する。

**原因**: カレントディレクトリが存在しなくなり、シェルが `getcwd()` に失敗する。

**解決策**:

- **根本対策**: Claude Codeを再起動する
- **予防策**: Worktreeを削除する前に、必ずオリジナルディレクトリからコマンドを実行する

```bash
# NG: worktree内から自分自身を削除しようとする
cd .worktrees/feature && git worktree remove .  # 失敗

# OK: オリジナルディレクトリから削除する
cd /path/to/original && git worktree remove .worktrees/feature  # 成功
```

### Bashコマンドでカレントディレクトリが変わる

**症状**: `cd dir && command` を実行後、hookが動作しなくなる。またはツールがブロックされる。

**原因**: Claude Codeのシェルセッションは永続的であり、`cd` コマンドでカレントディレクトリが変わると、以降のコマンドも変更後のディレクトリで実行される。hookが `.claude/hooks/` を相対パスで参照している場合、見つけられなくなる。

**根本対策（設定済み）**: フック設定で `$CLAUDE_PROJECT_DIR` 環境変数を使用する。これにより、cwdに関係なくフックが正しく見つかる。

```json
"command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/xxx.py"
```

**運用での回避策**:

- **pnpm `-C` / `--dir` オプション**: 指定ディレクトリでコマンドを実行
- **pnpm `--filter` オプション**: ワークスペースの特定パッケージを対象
- **サブシェル**: `(cd dir && command)` で囲むとcwdが変わらない
- **発生した場合**: Claude Codeの再起動が必要

```bash
# NG: カレントディレクトリが永続的に変わる
cd frontend && pnpm add xxx

# OK: -C / --dir オプションを使用
pnpm add xxx -C frontend
pnpm add xxx --dir frontend

# OK: --filter を使用（monorepo）
pnpm --filter frontend add xxx

# OK: サブシェルで実行
(cd frontend && pnpm add xxx)
```

**参考**:

- [pnpm CLI Global Options](https://pnpm.io/pnpm-cli) - `-C` / `--dir` オプション
- [Claude Code Hooks Reference](https://code.claude.com/docs/en/hooks.md) - `$CLAUDE_PROJECT_DIR` 環境変数

### Worktree使用中にgh pr mergeがエラーになる

**症状**: `gh pr merge --delete-branch` を実行すると、マージ自体は成功しているのにエラーが発生する。

```text
failed to run git: fatal: 'main' is already used by worktree at '/path/to/worktree'
```

**原因**: `--delete-branch` オプションはマージ後にローカルブランチも削除しようとするが、worktreeで使用中のブランチは削除できない。ただし、**GitHub上でのマージは成功している**。

**確認方法**:

```bash
# マージが成功しているか確認
gh pr view {PR} --json state,mergedAt
```

**解決策**:

1. **`--delete-branch` を使わない**: worktreeで作業中は `gh pr merge --squash` のみを使用
2. **worktreeを先に削除**: マージ前にworktreeを削除・アンロックしてからマージ

```bash
# 方法1: --delete-branch を使わずにマージ
gh pr merge {PR} --squash

# マージ後にworktreeを手動で削除
git worktree unlock .worktrees/{name}
git worktree remove .worktrees/{name}

# 方法2: 先にworktreeを削除してからマージ
git worktree unlock .worktrees/{name}
git worktree remove .worktrees/{name}
gh pr merge {PR} --squash --delete-branch
```

**注意**: エラーメッセージが出てもパニックにならないこと。`gh pr view` で状態を確認すれば、マージが成功しているかどうかがわかる。

### Worktree内でフック変更が反映されない

**症状**: worktreeで`.claude/hooks/`のファイルを変更しても、変更後のフックが実行されない。例えば、merge-check.pyを修正しても、`gh pr merge`実行時に古い動作のままになる。

**原因**: Claude Codeは`CLAUDE_PROJECT_DIR`環境変数をメインリポジトリのパスに設定する。`settings.json`のフックパスは`$CLAUDE_PROJECT_DIR/.claude/hooks/xxx.py`で参照されるため、worktreeで変更してもメインリポジトリのフックが実行される。

**影響**: フック自体を変更するPRでは「鶏と卵」問題が発生する。変更したフックをテストするにはPRをマージする必要があるが、マージ前にテストしたい場合がある。

**解決策**:

1. **PRをマージする**: 変更をマージすれば、メインリポジトリに反映される（推奨）
2. **環境変数をオーバーライド**: 開発中にworktreeのフックをテストしたい場合

   ```bash
   # worktreeのパスを指定してClaude Codeを起動
   CLAUDE_PROJECT_DIR=/path/to/.worktrees/issue-1132 claude
   ```

3. **コマンド単位でオーバーライド**: 特定のコマンドのみworktreeのフックを使用

   ```bash
   # gh pr mergeを新しいフックでテスト
   CLAUDE_PROJECT_DIR="$PWD/.worktrees/issue-1132" gh pr merge ...
   ```

**検知**: SessionStart時に`hook-dev-warning.py`フックが自動的に警告を表示する。worktree内で`.claude/hooks/`に変更がある場合、上記の情報が案内される。

**関連Issue**: #1132

### git rev-parseの戻り値がworktreeで異なる

**症状**: `git rev-parse --git-common-dir` などのコマンドを使用するスクリプトが、worktree内で正しく動作しない。パス解決に失敗したり、予期せぬディレクトリを参照したりする。

**原因**: `git rev-parse` の戻り値は実行環境（メインリポジトリ vs worktree）によって異なる。

**主要オプションと戻り値パターン**:

| オプション | メインリポジトリ | worktree内 |
| ---------- | --------------- | ---------- |
| `--show-toplevel` | `/path/to/repo` (絶対) | `/path/to/.worktrees/name` (絶対) |
| `--git-dir` | `.git` (相対) | `/path/to/repo/.git/worktrees/name` (絶対) |
| `--git-common-dir` | `.git` (相対) | `/path/to/repo/.git` (絶対) |

**注意**: メインリポジトリでは**相対パス**が返されることがある。`Path.resolve()` を使用すると、スクリプトの実行ディレクトリ（CWD）を基準に解決されるため、意図しないパスになる可能性がある。

**ベストプラクティス**:

```python
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()

def get_main_project_root() -> Path:
    """メインリポジトリのルートを取得（worktree内でも正しく動作）"""
    result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        capture_output=True, text=True, timeout=5,
        cwd=SCRIPT_DIR,  # スクリプトのディレクトリを基準に
    )
    if result.returncode == 0:
        git_common_dir = Path(result.stdout.strip())

        # 相対パスの場合は SCRIPT_DIR を基準に解決
        if not git_common_dir.is_absolute():
            git_common_dir = (SCRIPT_DIR / git_common_dir).resolve()

        # メインリポジトリ／Worktree 共通:
        # "--git-common-dir" はメインリポジトリの ".git" を指すので、その親がルート
        return git_common_dir.parent

    # フォールバック
    return SCRIPT_DIR.parent.parent.resolve()
```

**チェックリスト**（`git rev-parse` を使用する前に）:

- [ ] 戻り値が相対パス/絶対パスのどちらかを確認
- [ ] 相対パスの場合、基準ディレクトリを明示的に指定
- [ ] worktree環境でテスト実行

**関連Issue**: #2198, #2200

---

## Claude Code関連

### 削除済みフックでエラーが発生し続ける

**症状**: フックファイルを削除し、`settings.json`からも参照を削除したにも関わらず、Stopフック実行時などにエラーが発生し続ける。

```text
Stop hook error: [python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/xxx.py]:
/opt/.../Python: can't open file '.../xxx.py': [Errno 2] No such file or directory
```

**原因**: Claude Codeはセッション開始時にフック設定をキャッシュする。セッション中に`settings.json`を変更しても、キャッシュされた古い設定が使われ続ける。

**解決策**:

1. **即時解決**: Claude Codeセッションを再起動する
2. **予防策**: フックファイルを削除する前に、必ず`settings.json`から参照を削除し、セッションを再起動してから削除する

**検証方法**:

```bash
# settings.jsonに存在しないファイルへの参照がないか確認
python3 .claude/scripts/validate-hooks-settings.py
```

**注意**: CIでもこの検証が実行されるため、参照が残っている状態でPRを作成するとCIが失敗する。

**関連Issue**: #199, #200

---

## Agent CLI関連

### Gemini CLI で 404 エラーが発生する

**症状**: Gemini CLIを実行すると以下のエラーが発生:

```text
[API Error: [{
  "error": {
    "code": 404,
    "message": "Requested entity was not found.",
    "status": "NOT_FOUND"
  }
}]]
```

**原因**: Gemini CLIのデフォルトモデルが未設定で、ハードコードされた古いモデル名が使用されている。これは[既知のバグ](https://github.com/google-gemini/gemini-cli/issues/5373)。

**解決策**:

1. **セットアップスクリプトを実行**（推奨）:

   ```bash
   .claude/scripts/setup-agent-cli.sh
   ```

1. **手動で設定**:

   ```bash
   # ~/.gemini/settings.json に以下を追加
   {
     "model": {
       "name": "gemini-2.5-pro"
     }
   }
   ```

1. **コマンドラインで毎回指定**:

   ```bash
   gemini --model gemini-2.5-pro "your prompt"
   ```

**利用可能なモデル**（2025年12月現在）:

| モデル | model ID | 特徴 |
| ------ | -------- | ---- |
| Gemini 2.5 Pro | `gemini-2.5-pro` | 最高性能、複雑なタスク向け |
| Gemini 2.5 Flash | `gemini-2.5-flash` | 高速・低コスト |
| Gemini 2.5 Flash Lite | `gemini-2.5-flash-lite` | 最速・最低コスト |

**最新モデルの確認**: <https://ai.google.dev/gemini-api/docs/models>

**関連Issue**: [google-gemini/gemini-cli#5373](https://github.com/google-gemini/gemini-cli/issues/5373)

---

## CI/CD関連

### Cloudflare Pages デプロイで UTF-8 エラーが発生する

**症状**: デプロイ時に以下のエラーが発生:

```text
Invalid commit message, it must be a valid UTF-8 string. [code: 8000111]
```

**原因**: `wrangler pages deploy` がgitのコミットメッセージを自動取得し、日本語や絵文字を含むメッセージをCloudflare Pages APIに送信すると拒否される。

**解決策**: `--commit-message` オプションでASCIIのみのメッセージを明示的に指定する。

```yaml
# .github/workflows/_deploy.yml
command: pages deploy frontend/dist --project-name=dekita --commit-message=${{ github.sha }}
```

**注意**: GitHub Actionsの `command` パラメータでは、`${{ github.sha }}` をダブルクォートで囲む必要はない。

### パッケージマネージャー移行時のCI失敗

パッケージマネージャー（npm → pnpm など）を移行した後にCIが失敗する場合がある。

**よくある原因と確認事項**:

1. **設定ファイル内のコマンド**
   - `playwright.config.ts`, `jest.config.ts` 等のテスト設定
   - `package.json` の scripts
   - 確認: `grep -rE "npm run|npm install|yarn" --include="*.ts" --include="*.json"`

2. **CI/CDワークフロー内の直接的なコマンド**
   - `.github/workflows/*.yml` 内の `npm` / `yarn` コマンド
   - 確認: `grep -rE "npm|yarn" .github/workflows/`

3. **外部GitHub Actionsのpnpm互換性**
   - `cloudflare/wrangler-action` 等は pnpm workspace で問題が発生する場合がある
   - **症状**: `ERR_PNPM_ADDING_TO_ROOT`（pnpm 9.13.2+でworkspace root への直接インストールが禁止）
   - **解決策**: 該当ツールをルートの `package.json` に事前インストール

**wrangler-action の場合**:

```bash
# ルート package.json に wrangler を追加
pnpm add -wD wrangler
```

これにより、wrangler-action が wrangler を自動インストールしようとしてエラーになるのを防ぐ。

**参考**: [cloudflare/wrangler-action#338](https://github.com/cloudflare/wrangler-action/issues/338)

### Status Check と skipped ジョブの動作

**現在の設計**:

- `status-check` ジョブは失敗/キャンセル時のみ実行（成功時は skipped）
- この設計は [DevOps Directive の推奨パターン](https://devopsdirective.com/posts/2025/08/github-actions-required-checks-for-conditional-jobs/) に基づく

**推奨フロー**:

```bash
# 1. CI + AIレビュー完了を待機（バックグラウンド実行推奨）
python3 .claude/scripts/ci-monitor.py {PR} --wait-review --json

# 2. 手動マージ
gh pr merge {PR} --squash
```

**注意**: `--auto`オプションは`merge-check.py`フックでブロックされます。これはAIレビュー完了前の自動マージを防ぐための意図的な設計です。

**過去の問題（解決済み）**: 以前は手動マージで"Required status check is expected"エラーが発生するケースがありましたが、現在は正常に動作しています。問題が再発した場合は、PRの状態（`gh pr view {PR} --json mergeable,mergeStateStatus`）を確認してください。

### ワークフローファイル変更時の制限

PRでワークフローファイル（`.github/workflows/*.yml`）を変更した場合、そのPR上では変更後のワークフローは自動実行されない（`action_required`状態になる）。

**理由**: GitHubのセキュリティ機能。PRからの悪意あるワークフロー変更を防ぐため。

**対応**:

1. GitHub UIでワークフローを手動承認する、または
2. PRをマージして、次のPRで動作確認する

**注意**: 手動承認しても、承認時点のイベントコンテキストが異なるため、期待通りに動作しない場合がある。

### CIが開始されない場合

PRを作成したがCIが開始されない場合、以下の順序で確認する:

```bash
# 1. まずPRの状態を確認（最重要）
gh pr view {PR番号} --json mergeable,mergeStateStatus

# 2. 結果に応じて対応
# - mergeable: CONFLICTING → コンフリクトを解決（リベース）
# - mergeable: UNKNOWN → 少し待って再確認
# - mergeable: MERGEABLE → 他の原因を調査
```

**よくある原因と対応**:

| `mergeable` | `mergeStateStatus` | 原因 | 対応 |
| ----------- | ------------------ | ---- | ---- |
| CONFLICTING | DIRTY | コンフリクト | `git fetch origin main && git rebase origin/main` |
| MERGEABLE | BLOCKED | チェック待ち | CIの開始を待つ、またはワークフロー承認を確認 |
| UNKNOWN | UNKNOWN | GitHub処理中 | 数秒待って再確認 |

**注意**: 「ワークフローファイル変更による制限」と「コンフリクト」は異なる問題だが、どちらもCIが開始されない症状を示す。先入観で判断せず、必ず `mergeable` 状態を最初に確認すること。
