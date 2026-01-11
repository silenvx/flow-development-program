# dekita 機能カタログ

生成日時: 2026-01-12

## 概要

dekita! - ハンズオン・ワークショップ向けのリアルタイム進捗共有ツール

AIエージェント（Claude Code）による開発フローを186個のフックで制御。

## 統計

| カテゴリ | 数 |
|---------|---|
| フック | 186 |
| スクリプト | 47 |
| スキル | 9 |
| ドキュメント | 2 |

---

## フック一覧（全186件）

| フック | 説明 |
|-------|------|
| `acceptance_criteria_reminder` | PR作成時に対象Issueの受け入れ条件未完了を警告する。 |
| `active_worktree_check` | セッション開始時に作業中（未マージ）のworktree一覧を表示する。 |
| `ai_review_checker` | merge-check用のAIレビュー状態確認ユーティリティ。 |
| `api_operation_logger` | 外部APIコマンド（gh, git, npm）の実行詳細をログ記録する。 |
| `api_operation_timer` | 外部APIコマンドの開始時刻を記録する（api-operation-loggerと連携）。 |
| `askuser_suggestion` | 選択肢をテキストで列挙するパターンを検出し、AskUserQuestionツールの使用を提案する。 |
| `bash_failure_tracker` | 連続Bash失敗を検知し、シェル破損時に引き継ぎプロンプト生成を提案する。 |
| `block_improvement_reminder` | 同一フックの連続ブロックを検知し、フック改善を提案する。 |
| `block_response_tracker` | ブロック後にテキストのみ応答（ツール呼び出しなし）を検知し警告する。 |
| `branch_check` | セッション開始時にメインリポジトリのブランチ状態を確認する。 |
| `branch_rename_guard` | git branch -m/-Mコマンド（ブランチリネーム）をブロックする。 |
| `bug_issue_creation_guard` | PRスコープの問題に対する別Issue作成をブロックする。 |
| `checkout_block` | mainリポジトリでのブランチ操作をブロックする。 |
| `ci_monitor_session_id_check` | ci_monitor.pyのsession-idオプション指定を検出する。 |
| `ci_recovery_tracker` | CI失敗から復旧までの時間を追跡する。 |
| `ci_wait_check` | ci_monitor.pyを使用すべきコマンドをブロックする。 |
| `closes_keyword_check` | gh pr create時にClosesキーワードの有無をチェックし、追加を提案する。 |
| `closes_scope_check` | PR作成時に未完了タスクのあるIssueをCloseしようとしていないかチェックする。 |
| `closes_validation` | コミットメッセージのCloses/Fixesキーワードの整合性をチェックする。 |
| `codex_review_check` | Codex CLIレビュー実行をPR作成・push前に強制する。 |
| `codex_review_logger` | Codex CLIレビュー実行をログ記録する（codex-review-checkと連携）。 |
| `codex_review_output_logger` | Codex CLIレビュー出力をパースしてレビューコメントを記録する。 |
| `commit_amend_block` | mainリポジトリでのgit commit --amendをブロックする。 |
| `commit_marker_update` | git commit後にCodexレビューマーカーを更新する。 |
| `commit_message_template` | git commit時にコミットメッセージテンプレートを挿入する。 |
| `commit_message_why_check` | コミットメッセージに「なぜ」の背景が含まれているかをチェックする。 |
| `continuation_session_metrics` | 継続セッションを検出し、前セッションのメトリクス記録と開発フローリマインダーを表示する。 |
| `copilot_review_retry_suggestion` | Copilot reviewの繰り返しエラー時にPR作り直しを提案する。 |
| `cwd_check` | カレントディレクトリの存在を確認し、削除されていればセッションをブロックする。 |
| `date_context_injector` | セッション開始時に現在日時とセッションIDをコンテキストに注入する。 |
| `defer_keyword_check` | 「後で」キーワードを検出しIssue参照なしの場合に警告する。 |
| `dependabot_skill_reminder` | Dependabot PR操作時にdevelopment-workflowスキルの参照を促す。 |
| `dependency_check_reminder` | 依存関係追加時にContext7/Web検索での最新情報確認を促す。 |
| `development_workflow_tracker` | 開発ワークフローの開始を追跡する。 |
| `doc_edit_check` | 仕様ドキュメント編集時に関連コード・Issueの確認を促す。 |
| `doc_reference_warning` | Bash失敗時にドキュメント参照の古さを検出して警告する。 |
| `dogfooding_reminder` | スクリプト作成・変更時に実データでのテストを促す（Dogfooding）。 |
| `duplicate_issue_check` | Issue作成前に類似Issueを検索して重複を警告する。 |
| `e2e_test_check` | CI E2E失敗後のローカルテスト実行を強制する。 |
| `e2e_test_recorder` | E2Eテスト実行結果を記録する。 |
| `empty_return_check` | except内での空コレクション返却アンチパターンを検出する。 |
| `environment_integrity_check` | フック環境の整合性をチェックする。 |
| `existing_impl_check` | worktree作成時に既存実装の存在を警告し、検証を促す。 |
| `exploration_tracker` | 探索深度（Read/Glob/Grep使用回数）を追跡する。 |
| `false_positive_detector` | セッション終了時に誤検知パターンを検出して警告する。 |
| `feedback_detector` | ユーザーフィードバック（問題指摘・懸念）を検出し、セッション状態に記録する。 |
| `file_overwrite_warning` | Bashでの既存ファイル上書き時に警告を表示する。 |
| `file_size_warning` | 大きすぎるファイルの読み込み時にリファクタリングを促す警告を表示する。 |
| `fix_verification_checker` | 修正主張と検証チェックの関数群（merge-check用モジュール）。 |
| `flow_constants` | Flow関連の共通定数モジュール。 |
| `flow_definitions` | Flowの定義モジュール - 全フロー設定の単一真実源。 |
| `flow_effect_verifier` | 未完了フローがある場合にセッション終了をブロックする。 |
| `flow_progress_tracker` | フローステップの完了を追跡する。 |
| `flow_state_updater` | 開発ワークフローのフェーズ遷移を追跡する。 |
| `flow_verifier` | ワークフロー追跡を検証し、レポートを生成する。 |
| `followup_issue_guard` | Issue参照なしの「フォローアップ」発言をブロックする。 |
| `force_push_guard` | 危険な`git push --force`をブロックし、`--force-with-lease`を推奨する。 |
| `fork_session_collaboration_advisor` | Fork-session開始時に独立したIssue候補を提案する。 |
| `fork_session_id_updater` | UserPromptSubmit時にsession_idをコンテキストに出力する。 |
| `git_config_check` | セッション開始時にgit設定の整合性を確認し、問題があれば自動修正する。 |
| `git_operations_tracker` | Git操作メトリクスを追跡してログに記録する。 |
| `git_status_check` | セッション開始時にGitの状態を確認し、未コミット変更を警告する。 |
| `hook_behavior_evaluator` | フックの期待動作と実際の動作のギャップを自動検知する。 |
| `hook_change_detector` | フックファイルと非フックファイルが同時にステージされた際に警告する。 |
| `hook_dev_warning` | worktree内でのフック開発時に変更が反映されない問題を警告する。 |
| `hook_effectiveness_evaluator` | セッション中のフック実行を分析し、改善提案を出力する。 |
| `hook_return_format_check` | フック種類に応じた返却形式の誤用を検出する。 |
| `hooks_design_check` | フック設計のSRP遵守と品質チェックを行う。 |
| `immediate_action_check` | PRマージ後のreflect実行を強制する。 |
| `immediate_pending_check` | [IMMEDIATE]タグの実行漏れを早期検出する。 |
| `issue_ai_review` | Issue作成後にAIレビュー（Gemini/Codex）を実行し結果を通知する。 |
| `issue_auto_assign` | worktree作成時にブランチ名からIssue番号を抽出し自動アサイン・競合チェック。 |
| `issue_body_requirements_check` | gh issue create時にIssue本文の必須項目をチェックする。 |
| `issue_branch_check` | worktree作成時にブランチ名にIssue番号を含むことを強制。 |
| `issue_checker` | merge-checkフックのIssue・受け入れ基準チェック機能。 |
| `issue_comments_check` | gh issue viewコマンド実行時にコメントを自動表示する。 |
| `issue_creation_tracker` | セッション内で作成されたIssue番号を記録し、実装を促す。 |
| `issue_incomplete_close_check` | gh issue close時に未完了チェックボックスを検出してブロック。 |
| `issue_investigation_tracker` | gh issue view実行時に別セッションの調査を検知し警告する。 |
| `issue_label_check` | gh issue create時に--labelオプションの指定を強制する。 |
| `issue_multi_problem_check` | Issue作成時に複数問題を1Issueにまとめていないかチェックする。 |
| `issue_priority_label_check` | gh issue create時に優先度ラベル（P0-P3）の指定を強制する。 |
| `issue_reference_check` | 存在しないIssue参照をブロックする。 |
| `issue_review_response_check` | gh issue close時にAIレビューへの対応状況を確認してブロック。 |
| `issue_scope_check` | Issue編集時のスコープ確認を強制する。 |
| `lesson_issue_check` | 振り返り時に発見した教訓がIssue化されているか確認する。 |
| `locked_worktree_guard` | 他セッションが所有するPRとworktreeへの操作をブロックする。 |
| `log_health_check` | セッション終了時にログの健全性を自動検証する。 |
| `main_sync_check` | セッション開始時にローカルmainブランチの同期状態を確認する。 |
| `merge_check` | マージ前の安全性チェックを強制する。 |
| `merge_commit_quality_check` | gh pr mergeでの--bodyオプション使用をブロックする。 |
| `merge_conditions` | merge-checkフックのマージ条件チェックを集約・オーケストレーションする。 |
| `merge_confirmation_warning` | 「マージしますか？」パターンを検出し、原則違反を警告する。 |
| `merged_worktree_check` | セッション開始時にマージ済みPRのworktreeを検知して警告する。 |
| `merit_demerit_check` | AskUserQuestionの選択肢にメリット/デメリット分析が含まれているか確認する。 |
| `multi_issue_guard` | 1つのworktree/PRで複数Issueを同時に対応しようとした場合に警告する。 |
| `observation_auto_check` | 操作成功時に動作確認Issueのチェック項目を自動更新する。 |
| `observation_reminder` | マージ成功後に未確認の動作確認Issueをリマインドする。 |
| `observation_session_reminder` | セッション開始時に未確認の動作確認Issueをリマインドする。 |
| `open_issue_reminder` | セッション開始時にオープンIssueをリマインド表示する。 |
| `open_pr_warning` | セッション開始時にオープンPRと関連worktreeを表示し介入を防止する。 |
| `orphan_worktree_check` | セッション開始時に孤立したworktreeディレクトリを検知して警告する。 |
| `parallel_edit_conflict_check` | sibling fork-session間での同一ファイル編集を警告する。 |
| `plan_file_updater` | gh pr merge成功後に計画ファイルのチェックボックスを自動更新する。 |
| `planning_enforcement` | PreToolUse hook: Enforce plan file before Issue work. |
| `post_merge_flow_completion` | PostToolUse hook to auto-complete flow steps after PR merge. |
| `post_merge_observation_issue` | PostToolUse hook to create observation issues after PR merge. |
| `post_merge_reflection_enforcer` | PRマージ成功後に振り返りを即時実行させる。 |
| `pr_body_quality_check` | PRボディに「なぜ」と「参照」が含まれているかチェックする。 |
| `pr_issue_alignment_check` | PreToolUse hook to verify Issue acceptance criteria when creating PRs. |
| `pr_issue_assign_check` | Hook to check and auto-assign issues referenced in PR body. |
| `pr_merge_pull_reminder` | PostToolUse hook to auto-pull main after PR merge. |
| `pr_metrics_collector` | PRメトリクス自動収集フック（PostToolUse） |
| `pr_overlap_check` | git push/gh pr create時に他PRとのファイル重複を警告。 |
| `pr_related_issue_check` | Hook to warn about related open Issues when creating a PR. |
| `pr_scope_check` | gh pr create時に1 Issue = 1 PRルールを強制。 |
| `pr_test_coverage_check` | gh pr create時に変更されたフックのテストカバレッジを確認。 |
| `problem_report_check` | セッション終了時に問題報告とIssue作成の整合性を確認。 |
| `production_url_warning` | 本番環境URLへのアクセス前に警告・確認を促す。 |
| `python_lint_check` | git commit前にPythonコードのフォーマット・lintを自動修正。 |
| `recurring_problem_block` | 繰り返し発生する問題を検出し、Issue作成を強制してからマージを許可。 |
| `reference_comment_check` | Edit時に「〜と同じ」参照スタイルのコメントを検出して警告。 |
| `reflection_completion_check` | PRマージ後または/reflect skill invoke後の振り返り完了を検証する。 |
| `reflection_log_collector` | 振り返りスキル実行時にセッションログを自動集計して提供。 |
| `reflection_progress_tracker` | 振り返り中のIssue作成を検出して進捗を追跡。 |
| `reflection_quality_check` | 振り返りの形式的評価を防ぐ（ブロック回数との矛盾検出、改善点Issue化強制）。 |
| `reflection_reminder` | PRマージや一定アクション後に振り返りをリマインド。 |
| `reflection_self_check` | 振り返りの観点網羅性を確認し、抜けがあればブロック。 |
| `regex_pattern_reminder` | 正規表現パターン実装時にAGENTS.mdチェックリストを表示。 |
| `related_task_check` | セッション終了時にセッション内作成Issueのステータスを確認し、未完了ならブロック。 |
| `research_requirement_check` | Issue/PR作成前にWeb調査を強制。 |
| `research_tracker` | セッション内のWebSearch/WebFetch使用を追跡。 |
| `resolve_thread_guard` | レビュースレッドResolve時に応答コメントを強制。 |
| `review_checker` | merge-checkフックのレビューコメント・スレッド検証機能。 |
| `review_promise_tracker` | レビュー返信で「別Issue対応」と約束した場合のIssue作成追跡フック。 |
| `reviewer_removal_check` | PreToolUse hook: Block removal of AI reviewers from PRs. |
| `rework_tracker` |  |
| `script_test_reminder` | PostToolUse hook: Remind to add tests when new functions are added to scripts. |
| `secret_deploy_check` | Stop hook to verify frontend secrets have been deployed. |
| `secret_deploy_trigger` | PostToolUse hook to track frontend secret updates. |
| `security_bypass_test_reminder` | セキュリティガードファイル編集時にバイパステストの追加を促す。 |
| `session_end_main_check` | Stop hook to verify main branch is up-to-date at session end. |
| `session_end_worktree_cleanup` | Session end worktree cleanup hook. |
| `session_file_state_check` | セッション再開時ファイル状態検証フック（SessionStart） |
| `session_handoff_reader` | セッション開始時に前回の引き継ぎメモを読み込み表示。 |
| `session_handoff_writer` | セッション終了時に引き継ぎメモを生成。 |
| `session_issue_integrity_check` | セッション別Issue追跡データの整合性を検証。 |
| `session_log_compressor` | セッション終了時にローテート済みログを圧縮。 |
| `session_marker_refresh` | worktree内のセッションマーカーのmtimeを定期更新。 |
| `session_marker_updater` | セッション開始時にworktree内のセッションマーカーを更新。 |
| `session_metrics_collector` | セッションメトリクス収集フック（Stop） |
| `session_outcome_collector` | セッション終了時に成果物（PR、Issue、コミット）を収集。 |
| `session_resume_warning` | セッション再開時に競合状況警告を表示。 |
| `session_todo_check` | セッション終了時に未完了TODOを検出して警告。 |
| `session_worktree_status` | セッション開始時に既存worktreeの状況を確認し警告する。 |
| `shell_tokenizer` | locked-worktree-guard用の低レベルシェルトークン化ユーティリティ。 |
| `signature_change_check` | Pythonの関数シグネチャ変更時にテスト更新漏れを検出。 |
| `similar_code_check` | 新規フック作成時に類似コードを検索して参考情報を提供。 |
| `similar_pattern_search` | PRマージ後にコードベース内の類似パターンを検索し修正漏れを防ぐ。 |
| `skill_failure_detector` | Skill呼び出し失敗を検出して調査・Issue化を促す。 |
| `skill_usage_reminder` | 特定操作の前にSkill使用を強制。 |
| `stop_auto_review` | セッション終了時に未レビューの変更を検出しレビュー実行を促す。 |
| `subprocess_lint_check` | Pythonフック内の問題のあるsubprocess使用パターンを検出。 |
| `systematization_check` | セッション終了時に教訓が仕組み化されたか確認する。 |
| `systematization_issue_close_check` | 仕組み化Issueクローズ時にフック/ツール実装を検証。 |
| `task_start_checklist` | タスク開始時に確認チェックリストをリマインド表示する。 |
| `test_deletion_check` | Python関数/クラス削除時のテスト参照漏れを検出。 |
| `tool_efficiency_tracker` | ツール呼び出しパターンを追跡し非効率なパターンを検出。 |
| `tool_substitution_detector` | パッケージマネージャの実行とツール代替パターンを追跡。 |
| `ui_check_reminder` | フロントエンド変更時にブラウザ確認を強制。 |
| `user_feedback_systematization_check` | ユーザーフィードバック検出時の仕組み化を確認する。 |
| `uv_run_guard` | worktree内でのuv run使用を防止。 |
| `vague_action_block` | 曖昧な対策表現（精神論）を検出してブロック。 |
| `workflow_skill_reminder` | worktree作成・PR作成時にdevelopment-workflow Skillを参照するようリマインド。 |
| `workflow_verifier` | ワークフロー実行を検証するユーティリティモジュール。 |
| `worktree_auto_cleanup` | PRマージ成功後にworktreeを自動削除。 |
| `worktree_auto_setup` | worktree作成成功後にsetup-worktree.shを自動実行。 |
| `worktree_cleanup_suggester` | セッション終了時にマージ/クローズ済みPRのworktreeクリーンアップを提案。 |
| `worktree_commit_integrity_check` | セッション開始時にworktree内のコミット整合性をチェック。 |
| `worktree_creation_marker` | worktree作成時にセッションIDをマーカーファイルとして記録する。 |
| `worktree_main_freshness_check` | worktree作成前にmainブランチが最新か確認。 |
| `worktree_manager` | worktree状態管理のユーティリティモジュール。 |
| `worktree_path_guard` | worktree作成先が.worktrees/内かを検証。 |
| `worktree_removal_check` | worktree削除前にアクティブな作業やcwd衝突を検出。 |
| `worktree_session_guard` | 別セッションが作業中のworktreeへの誤介入を防止する。 |
| `worktree_warning` | mainブランチでの編集をブロックし、worktreeでの作業を強制する。 |

---

## スクリプト一覧（全47件）

| スクリプト | 説明 |
|----------|------|
| `analyze_api_operations` | API操作ログの分析ツールを提供する。 |
| `analyze_false_positives` | フック誤検知の分析と改善提案を生成する。 |
| `analyze_flow_effectiveness` | 開発フローの効果を評価・分析する。 |
| `analyze_fork_tree` | fork-sessionの親子関係を分析・可視化する。 |
| `analyze_hook_dependencies` | フックのlib/依存関係を分析しMermaid図を生成する。 |
| `analyze_hook_timing` | フック実行時間を分析する。 |
| `analyze_interrupts` | ユーザー中断・バックグラウンド化・ツール拒否を分析する。 |
| `analyze_pattern_data` | パターン検出フック作成のための実データ分析を行う。 |
| `analyze_review_quality` | レビュー品質メトリクスを分析する。 |
| `analyze_session_outcomes` | セッション成果データを分析する。 |
| `analyze_stale_hooks` | フックの有効性を分析し陳腐化候補を特定する。 |
| `background_task_logger` | バックグラウンドタスクのログを永続化する。 |
| `batch_resolve_threads` | PRの未解決レビュースレッドを一括resolveする。 |
| `block_evaluator` | フックブロック判断を評価・記録する。 |
| `check_hook_patterns` | フック実装パターンの一貫性をチェックする。 |
| `check_hook_test_coverage` | フックのテストカバレッジをチェックする。 |
| `check_locale_privacy_terms` | プライバシー/利用規約セクションがja.jsonのみに存在するか確認する。 |
| `check_sentry_usage` | Sentryスコープリークパターンを検出する。 |
| `check_workflow_definitions` | ワークフロー定義の整合性をチェックする。 |
| `ci_monitor` | PRのCI・レビュー状態を監視する。 |
| `collect_pr_metrics` | PRライフサイクルメトリクスを収集する。 |
| `collect_session_metrics` | セッション終了時のメトリクスを収集する。 |
| `confirm_ui_check` | UI確認完了を記録しコミットをアンブロックする。 |
| `dependabot_batch_merge` | Dependabot PRを一括処理する。 |
| `evaluate-issue-decisions` | Issue判定の妥当性を評価する。 |
| `flow_status` | セッション内のフロー進捗状況を表示する。 |
| `generate_dashboard` | 開発フローログからHTMLダッシュボードを生成する。 |
| `generate_index` | 開発フローファイルからインデックスを自動生成する。 |
| `hook_lint` | フック専用のカスタムLintルールを適用する。 |
| `migrate_hook_context` | フックをHookContextパターンに移行する。 |
| `pr_merge_workflow` | PRライフサイクル全体を自動化する統合ワークフロー。 |
| `record-issue-decision` | Issue作成/不作成の判定を記録する。 |
| `record_review_response` | レビューコメントへの対応を記録する。 |
| `review_contradiction_check` | AIレビューコメントの矛盾可能性を検出する。 |
| `review_respond` | レビューコメントに返信してスレッドをresolveする。 |
| `session_report_generator` | セッション終了時に統合レポートを生成する。 |
| `session_summary` | セッション活動サマリーを生成する。 |
| `track_hook_removal_impact` | フック削除・非推奨化の影響を追跡する。 |
| `update_secret` | GitHub Secretを更新し本番デプロイを実行する。 |
| `validate_hooks_settings` | settings.json内のフックファイル参照を検証する。 |
| `validate_lefthook` | lefthook.yml設定を検証する。 |
| `codex-design-review` | Codex CLIで設計品質重視のコードレビューを実行する。 |
| `issue-ai-review` | GeminiとCodexでIssueをレビューし結果をコメント投稿する。 |
| `setup-agent-cli` | Agent CLI（Gemini/Codex）の初期セットアップ。 |
| `setup-worktree` | Worktree作成後の自動セットアップ。 |
| `statusline` | Claude Codeステータスラインの動的生成。 |
| `update-codex-marker-on-rebase` | リベース/amend後にCodexレビューマーカーを自動更新する。 |

---

## スキル一覧（全9件）

| スキル | 説明 |
|-------|------|
| `add-perspective` | 振り返り観点追加ガイド |
| `claude-code-features` | Claude Code機能調査ガイド |
| `code-review` | コードレビュー対応 |
| `coding-standards` | コーディング規約 |
| `development-workflow` | 開発ワークフロー |
| `hooks-reference` | フックリファレンス |
| `reflect` | 振り返り実行ガイド |
| `reflect-trends` | 傾向分析 |
| `troubleshooting` | troubleshooting |

---

## 詳細情報

各フックの詳細（Why/What/keywords）は `index.json` を参照:

```bash
jq '.hooks[] | select(.name == "merge_check")' .claude/index.json
```
