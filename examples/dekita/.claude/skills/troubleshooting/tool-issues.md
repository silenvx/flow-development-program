# ツール関連のトラブルシューティング

Claude Code、Gemini CLI、Codex等のツールに関連する問題と解決策。

## Claude Code: 削除済みフックでエラーが発生し続ける

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
bun run .claude/scripts/validate_hooks_settings.ts
```

**注意**: CIでもこの検証が実行されるため、参照が残っている状態でPRを作成するとCIが失敗する。

**関連Issue**: #199, #200

## Gemini CLI で 404 エラーが発生する

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
   .claude/scripts/setup_agent_cli.sh
   ```

2. **手動で設定**:

   ```bash
   # ~/.gemini/settings.json に以下を追加
   {
     "model": {
       "name": "gemini-2.5-pro"
     }
   }
   ```

3. **コマンドラインで毎回指定**:

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

## Codexがレート制限に達した場合

**症状**: `codex review --base main` を実行すると以下のエラーが発生:

```text
Usage limit reached, please try again after Jan 24, 2026, 12:00:00 PM PST
```

**原因**: Codex CLIの使用量制限に達した。無料枠では一定期間内のリクエスト数に制限がある。

**対応**:

1. **ユーザーに確認**: レート制限到達を報告し、対応方針を確認する
2. **Geminiのみで続行**: 以下の手順でマーカーファイルを手動作成し、`codex-review-check`をバイパス

   ```bash
   # 前提: リポジトリに main ブランチが存在すること
   # リモートの最新情報を取得（ローカルmainが古い場合のハッシュズレを防ぐ）
   git fetch origin

   # 現在のブランチ名とコミットハッシュを取得
   branch=$(git branch --show-current)
   commit=$(git rev-parse HEAD)
   # フックと同じロジック（SHA256 of full diff, first 12 chars）でハッシュを生成
   diff_hash=$(git diff origin/main | python3 -c "import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest()[:12])")

   # マーカーファイルを作成
   safe_branch=$(echo "$branch" | tr '/' '-')
   # 重要: CLAUDE_PROJECT_DIRが設定されていることを確認（worktree内では必須）
   markers_dir="${CLAUDE_PROJECT_DIR}/.claude/logs/markers"
   mkdir -p "$markers_dir"
   echo "${branch}:${commit}:${diff_hash}" > "${markers_dir}/codex-review-${safe_branch}.done"
   ```

3. **Geminiレビューを実行**: `gemini "/code-review" --yolo -e code-review`
4. **通常通りプッシュ**: マーカーファイルにより`codex-review-check`がパスする

**注意事項**:

| 項目 | 説明 |
| ---- | ---- |
| **使用条件** | 低リスク変更（ドキュメント修正等）に限定 |
| **Gemini必須** | Codexをスキップしても、Geminiレビューは必ず実行する |
| **ユーザー承認** | この回避策を使用する前に、必ずユーザーの承認を得る |
| **CLAUDE_PROJECT_DIR** | worktree内では必須。未設定だとマーカーが間違った場所に書かれる |

**背景**: PR #3277でCodexがレート制限に達し、`SKIP_CODEX_REVIEW=1`も`skip-review-env-check`でブロックされたため、マーカーファイル手動作成で回避した。

**関連Issue**: #3278

## Claude Code: サマリー処理がハングする

**症状**: セッション終了時にサマリー処理が開始され、数十分経っても完了しない。

**原因**: Claude Code側の既知バグ（[anthropics/claude-code#19567](https://github.com/anthropics/claude-code/issues/19567)）。特定の条件下でサマリー処理が無限ループに入る。

**診断方法**:

1. セッションが10分以上レスポンスなしの場合、ハングを疑う
2. プロセスを確認: `ps aux | grep [c]laude`

**解決策**:

1. セッションを強制終了（Ctrl+C 複数回）
2. 新しいセッションを開始

**予防策**:

- 長時間のセッションは定期的にforkして分割
- 大量のコンテキストを持つセッションは早めに終了

**関連Issue**: [anthropics/claude-code#19567](https://github.com/anthropics/claude-code/issues/19567)
