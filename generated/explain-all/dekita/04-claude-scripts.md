# .claude/scripts 実装ファイル

Claude Code開発支援スクリプト。分析、監視、自動化ツールを提供。

## ファイル一覧（45個）

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|------------|------------------|-------------------|--------|
| `analyze-api-operations.py` | 開発ワークフローの洞察を得るため、gh/git APIの操作履歴を分析する機能が必要 | timeline、pr-lifecycle、issue-lifecycle、errors、duration-stats、summaryの6種類の分析機能を提供 | `python3 .claude/scripts/analyze-api-operations.py timeline --since 2h --session-id <ID>` |
| `analyze-false-positives.py` | フックの誤検知（false positive）を減らすため、評価ログから改善ポイントを抽出する機能が必要 | load_evaluations()で評価ログを読み込み、analyze_false_positives()で誤検知パターンを分析、generate_suggestions()で改善提案を生成 | `python3 .claude/scripts/analyze-false-positives.py --hook <hook_name>` |
| `analyze-flow-effectiveness.py` | フック・ワークフローの改善点を特定するため、メトリクスを自動収集し改善提案を生成する機能が必要 | report（全体レポート）、weekly（週次サマリー）、check（問題検出CI用）、create-issues（改善Issue自動作成）を提供 | `python3 .claude/scripts/analyze-flow-effectiveness.py report --json` |
| `analyze-fork-tree.py` | 複数のClaude Codeセッション間の関係を把握するため、transcriptからfork-sessionツリーを構築する機能が必要 | get_project_id()でプロジェクトIDを取得、build_fork_tree()でツリー構築、display_tree()で可視化 | `python3 .claude/scripts/analyze-fork-tree.py --json` |
| `analyze-hook-dependencies.py` | フック間の依存関係を可視化し、リファクタリングの影響範囲を把握するため | extract_imports()でlib.*インポートを抽出、analyze_dependencies()で全フックの依存関係を分析、generate_mermaid()でMermaid図を生成 | `python3 .claude/scripts/analyze-hook-dependencies.py --output deps.md` |
| `analyze-hook-timing.py` | フックのパフォーマンスボトルネックを特定し、遅いフックを改善するためのデータが必要 | parse_log_entries()で実行ログをパース、analyze_hooks()でフック実行統計を計算、show_slow_hooks()で遅いフックを表示 | `python3 .claude/scripts/analyze-hook-timing.py --top 10 --slow 100` |
| `analyze-interrupts.py` | ユーザーの不満や改善ポイントを特定するため、中断（Escape）、Ctrl+B、ツール拒否を検出・統計化 | detect_interrupts()で中断イベントを検出、detect_backgrounds()でバックグラウンド化を検出、detect_denials()でツール拒否を検出 | `python3 .claude/scripts/analyze-interrupts.py --all --summary` |
| `analyze-pattern-data.py` | フックのパターン設計時に、実際のPR/Issueコメントからパターンの有効性を検証するデータが必要 | search（パターン検索）、analyze（頻度分析）、validate（検出率・誤検知率検証）の3モードを提供 | `python3 .claude/scripts/analyze-pattern-data.py search --pattern "regex"` |
| `analyze-review-quality.py` | AIレビュワー（Copilot/Codex）の有効性を評価し、カテゴリ別の妥当性を分析するため | summary（全体統計）、--by-reviewer（レビュワー別）、--by-category（カテゴリ別）、--detail（詳細表示）を提供 | `python3 .claude/scripts/analyze-review-quality.py --by-reviewer --json` |
| `analyze-session-outcomes.py` | セッションの生産性とタスク種別の分布を把握し、改善ポイントを特定するため | load_outcomes()で成果ログを読み込み、show_sessions()でセッション一覧を表示、show_summary()で統計サマリーを表示 | `python3 .claude/scripts/analyze-session-outcomes.py --days 7 --summary` |
| `analyze-stale-hooks.py` | 不要なフックを特定・削除するため、承認率・ブロック頻度・経過時間を分析する | load_metadata()でメタデータ読み込み、analyze_effectiveness()で有効性スコア計算、identify_stale()で陳腐化候補を特定 | `python3 .claude/scripts/analyze-stale-hooks.py --weeks 4 --output markdown` |
| `background-task-logger.py` | コンテキスト要約時にバックグラウンドタスクの出力が失われる問題を解決し、セッション終了後もログを確認可能にするため | log_background_event()でイベントをファイルに記録、list_events()で一覧表示、filter_by_session()でセッション別フィルタリング | `python3 .claude/scripts/background-task-logger.py --list --session-id <ID> --since 2024-01-01` |
| `batch_resolve_threads.py` | 複数のレビュースレッドに同一メッセージで返信し、一括でresolveする作業を自動化するため | list_unresolved_threads()で未解決スレッドを取得、reply_and_resolve()で返信してresolve | `python3 .claude/scripts/batch_resolve_threads.py 1234 "修正しました" --dry-run` |
| `block-evaluator.py` | フックの誤検知（false positive）を特定し、改善フィードバックループを確立するため | list（直近ブロック表示）、evaluate（特定ブロックを評価）、summary（評価サマリー表示）の3モードを提供 | `python3 .claude/scripts/block-evaluator.py list` / `evaluate <block_id> valid` |
| `check-hook-patterns.py` | PostToolUseフックのtool_result/tool_response両対応や、類似フックの存在を検出し、実装漏れを防ぐため | PATTERN001（tool_response未対応）、PATTERN002（類似フック警告）、PATTERN003（同パターン漏れ警告）を検出 | `python3 .claude/scripts/check-hook-patterns.py --all` |
| `check-hook-test-coverage.py` | PRで追加・変更されたフックにテストがあるか確認し、テスト不足を検出するため | get_changed_files()でPR内の変更ファイルを取得、get_hook_files()でフック一覧取得、check_test_coverage()でテストファイル存在確認 | CI内で自動実行、または `python3 .claude/scripts/check-hook-test-coverage.py` |
| `check-locale-privacy-terms.py` | プライバシーポリシーと利用規約は日本語のみ対応であり、他のロケールファイルに誤って追加されることを防ぐため | main()でロケールファイルをスキャンしprivacy/termsセクションを検出、ja.json以外で検出されたらエラー | CI内で自動実行、または `python3 .claude/scripts/check-locale-privacy-terms.py` |
| `check-sentry-usage.py` | Cloudflare WorkersのisolateモデルでSentry.setTag()をwithScope()外で使用するとリクエスト間でリークするため | check_file()でファイル内の禁止パターン（setTag, setContext, setUser, setExtra）を検出、main()でworker/src配下をスキャン | CI内で自動実行、または `python3 .claude/scripts/check-sentry-usage.py` |
| `check-workflow-definitions.py` | settings.jsonとflow_definitions.pyの定義漏れを防ぎ、フック追加時の一貫性を保証するため | extract_hook_names_from_settings()でsettings.jsonからフック名を抽出、check_consistency()で定義の整合性を検証 | CI内で自動実行、または `python3 .claude/scripts/check-workflow-definitions.py` |
| `ci-monitor.py` | CI待機中のBEHIND検知・自動リベース、レビュー完了検知を自動化し、開発フローを効率化するため | monitor_pr()でPR状態を監視、handle_behind()でBEHIND時自動リベース、check_reviews()でレビュー完了検知、emit_event()で構造化イベント出力 | `python3 .claude/scripts/ci-monitor.py <PR番号> --session-id <SESSION_ID> --early-exit` |
| `codex-design-review.sh` | 結合度・凝集度・単一責任原則の観点で自動コードレビューを行い、設計品質を向上させるため | codex reviewで設計品質観点でレビュー実行、--security/--coupling/--cohesionで観点を絞り込み | `.claude/scripts/codex-design-review.sh --uncommitted` / `--base main` |
| `collect-pr-metrics.py` | PRのサイクルタイム、レビュー時間、CI時間を分析し、開発プロセスの改善ポイントを特定するため | collect_pr_metrics()でPRメトリクスを収集、collect_recent()で最近マージされたPRを一括収集 | `python3 .claude/scripts/collect-pr-metrics.py --recent 10` |
| `collect-session-metrics.py` | セッション単位のフック実行統計・成果を記録し、改善分析に活用するため | collect_metrics()でセッションメトリクスを収集、write_metrics()でメトリクスをログファイルに書き込み | Stop hookから自動呼び出し、または `python3 .claude/scripts/collect-session-metrics.py --session-id <ID>` |
| `confirm-ui-check.py` | UI変更時のブラウザ確認完了を記録し、commit-msg-checkerのブロックを解除するため | main()で確認完了マーカーファイル（.claude/logs/markers/ui-check-{branch}.done）を作成 | `python3 .claude/scripts/confirm-ui-check.py` |
| `dependabot-batch-merge.py` | 複数のDependabot PRを効率的に処理し、リベース→CI待機→マージのフローを自動化するため | list_dependabot_prs()でオープンなDependabot PRを取得、process_pr()で1PRを処理、batch_merge()で複数PRを順次処理 | `python3 .claude/scripts/dependabot-batch-merge.py --dry-run --group production-patch` |
| `deprecate-hook.py` | フックのライフサイクル管理を標準化し、非推奨化・削除の履歴を追跡可能にするため | deprecate()でmetadata.jsonに非推奨情報を記録、remove_from_settings()でsettings.jsonから削除、undo()で非推奨を取り消し | `python3 .claude/scripts/deprecate-hook.py <hook_name> --remove --dry-run` |
| `flow-status.py` | 現在のセッションで進行中・未完了のフローを把握し、作業の継続性を確保するため | get_all_flows()で全フローの進捗を取得、get_incomplete_flows()で未完了フローを取得、display_status()で進捗状況を表示 | `python3 .claude/scripts/flow-status.py --all --json` |
| `generate-dashboard.py` | フック実行統計・セッションメトリクスを可視化し、改善ポイントを視覚的に把握するため | collect_data()でダッシュボードデータを収集、generate_html()でHTMLダッシュボードを生成 | `python3 .claude/scripts/generate-dashboard.py --days 7 --open` |
| `hook_lint.py` | フック実装の一貫性を保証し、よくあるミスやアンチパターンを検出するため | check_parse_hook_input()でjson.loads(stdin)使用を検出、check_except_pass()でexcept-passにコメント必須を検証、check_hardcoded_paths()でハードコード検出 | `python3 .claude/scripts/hook_lint.py --check-only` / `python3 .claude/scripts/hook_lint.py <file.py>` |
| `issue-ai-review.sh` | AIによるIssueレビューを自動化し、問題の明確さや技術的実現性を事前に検証するため | run_gemini_review()でGeminiレビュー実行、run_codex_review()でCodexレビュー実行、gh issue commentで結果投稿 | `.claude/scripts/issue-ai-review.sh <issue_number>` |
| `migrate_hook_context.py` | グローバル状態（get_claude_session_id）を廃止し、依存性注入パターン（HookContext）に統一するため | is_simple_migration()で単純移行か判定、migrate_file()でファイルを移行、migrate_all()で全フックを移行 | `python3 .claude/scripts/migrate_hook_context.py --dry-run` |
| `pr-merge-workflow.py` | Codexレビュー→プッシュ→CI待機→レビュー対応→マージ→worktree削除の一連のフローを自動化し、手作業を削減するため | run_codex_review()でCodex CLIレビュー実行、wait_for_ci()でCI完了待機、handle_reviews()でレビュー処理、merge_pr()でマージ、cleanup_worktree()で削除 | `python3 .claude/scripts/pr-merge-workflow.py --skip-codex --auto-verify --force` |
| `prompt-generator.py` | フック実装時のプロンプト生成を自動化し、一貫した品質のフックを効率的に作成するため | generate_prompt()で実装プロンプトを生成、add_context()でコンテキストを追加、format_output()で出力をフォーマット | `python3 .claude/scripts/prompt-generator.py --hook <hook_name>` |
| `record-review-response.py` | レビューコメントの対応状況を追跡し、品質分析に活用するため | record_response()で対応結果を記録、update_comment()で既存レコードを更新 | `python3 .claude/scripts/record-review-response.py --resolution accepted --reason "修正完了"` |
| `reflection-check.py` | 振り返りの実施状況を確認し、定期的な振り返りを促進するため | check_reflection()で振り返り実施状況を確認、get_last_reflection()で最終振り返り日時を取得 | `python3 .claude/scripts/reflection-check.py` |
| `review_contradiction_check.py` | 同一ファイルの近接行に複数コメントがある場合、矛盾の可能性を警告しレビュー品質を向上させるため | detect_potential_contradictions()で10行以内の近接コメントを矛盾候補として検出（意味解析なし、フラグのみ） | ci-monitor.pyから自動呼び出し |
| `review-respond.py` | レビュー対応を効率化し、返信・resolve・品質記録を一括で行うため | post_reply()でコメントに返信、resolve_thread()でスレッドをresolve、record_response()で対応を品質ログに記録 | `python3 .claude/scripts/review-respond.py --verified --resolution accepted` |
| `rework-tracker.py` | 手戻り発生を追跡し、プロセス改善のためのデータを収集するため | track_rework()で手戻りイベントを記録、analyze_rework()で手戻りパターンを分析 | `python3 .claude/scripts/rework-tracker.py --event <type>` |
| `session-handoff.py` | セッション間の引き継ぎ情報を管理し、コンテキストの継続性を確保するため | write_handoff()で引き継ぎ情報を記録、read_handoff()で引き継ぎ情報を読み込み | `python3 .claude/scripts/session-handoff.py --write` |
| `session-report-generator.py` | セッション単位の活動サマリーを生成し、成果と改善点を把握するため | collect_session_data()でセッションデータを収集、generate_report()で統合レポート（Markdown）を生成 | Stop hookから自動呼び出し、または `python3 .claude/scripts/session-report-generator.py --session-id <ID>` |
| `session-summary.py` | フック実行ログからセッション単位の活動を分析し、ブロック・トリガー・時間を可視化するため | analyze_session()でセッションを分析、generate_summary()でサマリーを生成、list_sessions()で最近のセッションを一覧表示 | `python3 .claude/scripts/session-summary.py --session <ID> --json --list` |
| `setup-agent-cli.sh` | Gemini/Codex CLIのデフォルト設定を適用し、404エラーの初期設定問題を回避するため | setup_gemini_cli()でGemini設定ファイルを作成/更新、verify_gemini_cli()で動作確認テストを実行、setup_codex_cli()でCodex CLI存在確認 | `.claude/scripts/setup-agent-cli.sh --verify` |
| `setup-worktree.sh` | worktree作成後に依存関係インストールの初期化を自動実行し、作業開始までの手順を簡略化するため | pnpm install（package.json存在時）で依存インストール、uvx連携（pyproject.toml存在時）はuvxに委譲 | `.claude/scripts/setup-worktree.sh .worktrees/issue-123` |
| `statusline.sh` | 現在のworktree/Issue/PR/フロー状態をステータスラインに表示し、作業状況を可視化するため | get_worktree_info()でworktree/ブランチ/PR情報を取得、get_flow_state()でフローフェーズ情報を取得、get_session_id()でセッションIDを取得 | Claude Codeのstatusline hookから自動呼び出し |
| `track-hook-removal-impact.py` | フック削除後のリグレッションを検出し、復元または完全削除の判断材料を提供するため | analyze_impact()で削除後の影響を分析、check_related_issues()で関連Issueを検索、check_error_patterns()でエラーパターンを検出 | `python3 .claude/scripts/track-hook-removal-impact.py --weeks 4 --hook <hook_name>` |
| `trending-issues.py` | 頻出する問題パターンを特定し、優先的に対応すべきIssueを可視化するため | analyze_trends()で問題パターンを分析、show_trending()でトレンドIssueを表示 | `python3 .claude/scripts/trending-issues.py --days 7` |
| `update_secret.py` | Secret更新→デプロイ→本番確認の一連の作業を自動化し、手作業ミスを防ぐため | update_secret()でGitHub Secretを更新、trigger_deploy()でデプロイワークフローを起動、wait_for_deploy()で完了待機、verify_production()で本番確認 | `python3 .claude/scripts/update_secret.py <SECRET_NAME> <VALUE>` |
| `update-cloudflare-secrets.sh` | Cloudflare Workers環境のシークレットを一括更新し、手動設定ミスを防ぐため | wrangler secret putでシークレットを設定、確認プロンプトで誤操作を防止 | `.claude/scripts/update-cloudflare-secrets.sh` |
| `update-codex-marker-on-rebase.sh` | リベースでコミットハッシュが変わった際にCodexレビュー記録を自動更新し、手動更新を不要にするため | sanitize_branch_name()でブランチ名をサニタイズ、マーカーファイル（branch:commit:diff_hash形式）を更新 | lefthook post-rewriteから自動呼び出し |
| `validate_lefthook.py` | lefthook設定の誤りを事前に検出し、pre-pushでの{staged_files}使用のミスを防ぐため | validate()で設定を検証、check_pre_push_staged_files()でpre-pushでの{staged_files}使用を検出（LEFTHOOK001） | `python3 .claude/scripts/validate_lefthook.py lefthook.yml` |
| `validate-hook-docstrings.py` | フックのドキュメント品質を確保し、メンテナンス性を向上させるため | check_docstring()でdocstring存在を確認、validate_format()でフォーマットを検証 | `python3 .claude/scripts/validate-hook-docstrings.py` |
| `validate-hooks-settings.py` | 削除されたフックへの参照やtypoを検出し、実行時エラーを事前に防ぐため | extract_hook_paths()でsettings.jsonからフックパスを抽出、validate_paths()でファイル存在を確認 | CI内で自動実行、または `python3 .claude/scripts/validate-hooks-settings.py` |
| `worktree-status.py` | 現在のworktree状態を把握し、作業状況を可視化するため | get_status()でworktree状態を取得、display_status()で状態を表示 | `python3 .claude/scripts/worktree-status.py` |

## テストディレクトリ

| ディレクトリ | ファイル数 | 実行コマンド |
|-------------|-----------|--------------|
| `.claude/scripts/tests/` | 50個 | `pytest .claude/scripts/tests/` |
