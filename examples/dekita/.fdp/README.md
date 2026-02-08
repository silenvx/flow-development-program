# dekita 機能カタログ

生成日時: 2026-02-02

## 概要
このプロジェクトの開発フロー構成要素。

## 統計
| カテゴリ | 数 |
|---------|---|
| フック | 216 |
| ライブラリ | 51 |
| スクリプト | 36 |
| スキル | 11 |

## フック一覧（全216件）
| フック | 説明 | 種別 |
|-------|------|------|
| `abstract_issue_suggester` | 具体的Issue作成時に抽象的対策Issueを自動提案する | warning |
| `acceptance_criteria_reminder` | PR作成時に対象Issueの受け入れ条件未完了を警告する。 | warning |
| `active_worktree_check` | セッション開始時に作業中（未マージ）のworktree一覧を表示する。 | warning |
| `ai_review_followup_check` | AIレビュー未対応コメント検知・Issue自動作成フック | warning |
| `api_operation_logger` | 外部APIコマンド（gh, git, npm）の実行詳細をログ記録する。 | warning |
| `api_operation_timer` | 外部APIコマンドの開始時刻を記録する（api-operation-loggerと連携）。 | logging |
| `askuser_suggestion` | 選択肢をテキストで列挙するパターンを検出し、AskUserQuestionツールの使用を提案する。 | warning |
| `bash_failure_tracker` | 連続Bash失敗を検知し、シェル破損時に引き継ぎプロンプト生成を提案する。 | warning |
| `block_improvement_reminder` | 同一フックの連続ブロックを検知し、フック改善を提案する。 | logging |
| `block_response_tracker` | ブロック後にテキストのみ応答（ツール呼び出しなし）を検知し警告する。 | warning |
| `branch_check` | セッション開始時にメインリポジトリのブランチ状態を確認する。 | blocking |
| `branch_rename_guard` | git branch -m/-Mコマンド（ブランチリネーム）をブロックする。 | logging |
| `bug_issue_creation_guard` | PRスコープの問題に対する別Issue作成をブロックする。 | blocking |
| `bypass_analysis` | セッション終了時に回避パターンを分析し、警告を表示する。 | logging |
| `bypass_detector` | 回避行動を検知してセッションログに記録する。 | warning |
| `checkout_block` | mainリポジトリでのブランチ操作をブロックする。 | logging |
| `ci_monitor_session_id_check` | ci_monitor（TypeScript版）のsession-idオプション指定を検出する。 | warning |
| `ci_recovery_tracker` | CI失敗から復旧までの時間を追跡する。 | logging |
| `ci_wait_check` | ci_monitor（TypeScript版）を使用すべきコマンドをブロックする。 | warning |
| `closed_issue_in_options_check` | AskUserQuestionの選択肢にクローズ済みIssueが含まれていないか確認する。 | logging |
| `closes_keyword_check` | gh pr create時にClosesキーワードの有無をチェックし、追加を提案する。 | warning |
| `closes_scope_check` | PR作成時に未完了タスクのあるIssueをCloseしようとしていないかチェックする。 | warning |
| `closes_validation` | コミットメッセージのCloses/Fixesキーワードの整合性をチェックする。 | warning |
| `code_simplifier_check` | simplifying-code Skill実行をPR作成前に強制する | warning |
| `code_simplifier_logger` | code-simplifier実行をログ記録する | warning |
| `coderabbit_review_check` | CodeRabbitのactionable commentsをマージ前にチェックする。 | warning |
| `codex_review_check` | Codex CLIレビュー実行をPR作成・push前に強制する。 | warning |
| `codex_review_logger` | Codex CLIレビュー実行をログ記録する（codex-review-checkと連携）。 | warning |
| `codex_review_output_logger` | Codex CLIレビュー出力をパースしてレビューコメントを記録する。 | warning |
| `commit_amend_block` | mainリポジトリでのgit commit --amendをブロックする。 | warning |
| `commit_marker_update` | git commit後にCodexレビューマーカーを更新する。 | logging |
| `commit_message_template` | git commit時にコミットメッセージテンプレートを挿入する。 | info |
| `commit_message_why_check` | コミットメッセージに「なぜ」の背景が含まれているかをチェックする。 | warning |
| `conservative_behavior_check` | 保守的行動パターンを検出してブロック。 | blocking |
| `continuation_session_metrics` | 継続セッションを検出し、前セッションのメトリクス記録と開発フローリマインダーを表示する。 | warning |
| `copilot_review_retry_suggestion` | Copilot reviewの繰り返しエラー時にPR作り直しを提案する。 | logging |
| `cwd_check` | カレントディレクトリの存在を確認し、削除されていればセッションをブロックする。 | warning |
| `date_context_injector` | セッション開始時に現在日時とセッションIDをコンテキストに注入する | warning |
| `defer_keyword_check` | 「後で」キーワードを検出しIssue作成との照合を行う。 | warning |
| `dependabot_skill_reminder` | Dependabot PR操作時にmanaging-developmentスキルの参照を促す。 | warning |
| `dependency_check_reminder` | 依存関係追加時にContext7/Web検索での最新情報確認を促す。 | warning |
| `development_workflow_tracker` | 開発ワークフローの開始を追跡する。 | logging |
| `doc_edit_check` | 仕様ドキュメント編集時に関連コード・Issueの確認を促す。 | warning |
| `doc_implementation_mismatch_detector` | ドキュメントと実装の乖離を検出してIssue作成を強制する | logging |
| `doc_reference_warning` | Bash失敗時にドキュメント参照の古さを検出して警告する。 | warning |
| `dogfooding_reminder` | スクリプト作成・変更時に実データでのテストを促す（Dogfooding）。 | logging |
| `duplicate_issue_check` | Issue作成前に類似Issueを検索して重複を警告する。 | warning |
| `duplicate_pr_check` | 同一Issueへの重複PR作成・マージを防止するフック。 | warning |
| `e2e_test_check` | CI E2E失敗後のローカルテスト実行を強制する。 | warning |
| `e2e_test_recorder` | E2Eテスト実行結果を記録する。 | warning |
| `empty_return_check` | except内での空コレクション返却アンチパターンを検出する。 | warning |
| `env_undefined_check` | Edit時に process.env への undefined 代入を検出して警告。 | warning |
| `environment_integrity_check` | フック環境の整合性をチェックする。 | warning |
| `existing_impl_check` | worktree作成時に既存実装の存在を警告し、検証を促す。 | warning |
| `exploration_tracker` | 探索深度（Read/Glob/Grep使用回数）を追跡する。 | warning |
| `false_positive_detector` | セッション終了時に誤検知パターンを検出して警告する。 | warning |
| `feedback_detector` | ユーザーフィードバック（問題指摘・懸念）を検出し、セッション状態に記録する。 | warning |
| `file_overwrite_warning` | Bashでの既存ファイル上書き時に警告を表示する。 | warning |
| `file_size_warning` | 大きすぎるファイルの読み込み時にリファクタリングを促す警告を表示する。 | warning |
| `flow_effect_verifier` | 未完了フローがある場合にセッション終了をブロックする。 | warning |
| `flow_progress_tracker` | フローステップの完了を追跡する。 | warning |
| `flow_state_updater` | 開発ワークフローのフェーズ遷移を追跡する。 | warning |
| `flow_verifier` | ワークフロー追跡を検証し、レポートを生成する。 | warning |
| `followup_issue_guard` | Issue参照なしの「フォローアップ」発言をブロックする。 | warning |
| `force_push_guard` | 危険な`git push --force`をブロックし、`--force-with-lease`を推奨する。 | warning |
| `fork_session_collaboration_advisor` | Fork-session開始時に独立したIssue候補を提案する。 | warning |
| `fork_session_id_updater` | UserPromptSubmit時にsession_idをコンテキストに出力する | warning |
| `fork_session_pr_guard` | Fork-sessionが親セッションのPRに介入することを防止する。 | warning |
| `gemini_review_check` | Gemini CLIレビュー実行をPR作成・push前に強制する | warning |
| `gemini_review_logger` | Gemini CLIレビュー実行をログ記録する（gemini-review-checkと連携） | warning |
| `git_config_check` | セッション開始時にgit設定の整合性を確認し、問題があれば自動修正する。 | logging |
| `git_operations_tracker` | Git操作メトリクスを追跡してログに記録する。 | warning |
| `git_status_check` | セッション開始時にGitの状態を確認し、未コミット変更を警告する。 | warning |
| `hook_behavior_evaluator` | フックの期待動作と実際の動作のギャップを自動検知する。 | warning |
| `hook_change_detector` | Check if command contains git add or git commit. | warning |
| `hook_dev_warning` | worktree内でのフック開発時に変更が反映されない問題を警告する。 | warning |
| `hook_effectiveness_evaluator` | セッション中のフック実行を分析し、改善提案を出力する。 | warning |
| `hook_return_format_check` | フック種類に応じた返却形式の誤用を検出する。 | warning |
| `hooks_design_check` | Get staged new hook files. | warning |
| `immediate_action_check` | PRマージ後のreflect実行を強制する。 | warning |
| `immediate_pending_check` | [IMMEDIATE]タグの実行漏れを早期検出する。 | logging |
| `issue_ai_review` | Issue作成後にAIレビュー（Gemini/Codex）を実行し結果を通知する。 | warning |
| `issue_auto_assign` | worktree作成時にブランチ名からIssue番号を抽出し自動アサイン・競合チェック。 | warning |
| `issue_body_requirements_check` | gh issue create時にIssue本文の必須項目をチェックする。 | blocking |
| `issue_branch_check` | worktree作成時にブランチ名にIssue番号を含むことを強制。 | logging |
| `issue_comments_check` | gh issue viewコマンド実行時にコメントを自動表示する。 | logging |
| `issue_creation_detector` | Issue作成必要性のキーワードを検出し、即座にIssue作成を強制する | warning |
| `issue_creation_request_check` | Issue作成依頼時の即時作成を強制するStop hook。 | blocking |
| `issue_creation_tracker` | セッション内で作成されたIssue番号を記録し、実装を促す。 | warning |
| `issue_existence_check` | 実装開始前にIssue存在を確認し、Issue作成を優先させる | logging |
| `issue_first_check` | 問題発見時にIssue作成を先に促すフック | warning |
| `issue_incomplete_close_check` | gh issue close時に未完了チェックボックスを検出してブロック。 | blocking |
| `issue_investigation_tracker` | gh issue view実行時に別セッションの調査を検知し警告する。 | warning |
| `issue_label_check` | gh issue create時に--labelオプションの指定を強制する。 | warning |
| `issue_multi_problem_check` | Issue作成時に複数問題を1Issueにまとめていないかチェックする。 | blocking |
| `issue_priority_label_check` | gh issue create時に優先度ラベル（P0-P3）の指定を強制する。 | warning |
| `issue_reference_check` | 存在しないIssue参照をブロックする。 | warning |
| `issue_requirements_reminder` | PRマージ前にIssue要件の未完了項目を警告する。 | warning |
| `issue_review_response_check` | gh issue close時にAIレビューへの対応状況を確認してブロック。 | blocking |
| `issue_scope_check` | Issue編集時のスコープ確認を強制する。 | blocking |
| `lesson_issue_check` | 振り返り時に発見した教訓がIssue化されているか確認する。 | warning |
| `locked_worktree_guard` | 他セッションが所有するPRとworktreeへの操作をブロックする。 | warning |
| `log_health_check` | セッション終了時にログの健全性を自動検証する。 | warning |
| `main_sync_check` | セッション開始時にローカルmainブランチの同期状態を確認する。 | warning |
| `merge_check` | マージ前の安全性チェックを強制する。 | blocking |
| `merge_commit_quality_check` | gh pr mergeでの--bodyオプション使用をブロックする。 | warning |
| `merge_confirmation_warning` | 「マージしますか？」パターンを検出し、原則違反を警告する。 | warning |
| `merge_result_check` | gh pr merge --delete-branch のworktree起因エラー検出フック | warning |
| `merged_worktree_check` | セッション開始時にマージ済みPRのworktreeを検知して警告する。 | warning |
| `merit_demerit_check` | AskUserQuestionの選択肢にメリット/デメリット分析が含まれているか確認する。 | logging |
| `migration_bug_check` | 移行PRでの移行先バグ検出フック | warning |
| `multi_issue_guard` | 1つのworktree/PRで複数Issueを同時に対応しようとした場合に警告する。 | warning |
| `new_python_hook_check` | 新規Pythonフック追加をブロックする | warning |
| `observation_auto_check` | 操作成功時に動作確認Issueのチェック項目を自動更新する。 | warning |
| `observation_reminder` | マージ成功後に未確認の動作確認Issueをリマインドする。 | logging |
| `observation_session_reminder` | セッション開始時に未確認の動作確認Issueをリマインドする。 | warning |
| `open_issue_reminder` | セッション開始時にオープンIssueをリマインド表示する。 | warning |
| `open_pr_warning` | セッション開始時にオープンPRと関連worktreeを表示し介入を防止する。 | warning |
| `orphan_worktree_check` | セッション開始時に孤立したworktreeディレクトリを検知して警告する。 | warning |
| `parallel_edit_conflict_check` | sibling fork-session間での同一ファイル編集を警告する。 | warning |
| `phase_issue_auto_continuation` | Phase Issueクローズ後に残作業があれば次Phase Issueを自動作成。 | warning |
| `phase_progression_guard` | Phase完了時に次Phaseの開始を強制する。 | warning |
| `plan_ai_review` | ExitPlanMode時にPlanファイルをGemini CLIでレビューする | warning |
| `plan_ai_review_iterative` | イテレーティブPlan AIレビューフック（PreToolUse:ExitPlanMode） | warning |
| `plan_checklist_guard` | 計画ファイルの未完了チェックリストを検出する。 | warning |
| `plan_file_updater` | gh pr merge成功後に計画ファイルのチェックボックスを自動更新する。 | warning |
| `plan_mode_exit_check` | planファイル作成後のExitPlanMode呼び出し検証フック | warning |
| `planning_enforcement` | PreToolUse hook: Enforce plan file before Issue work. | blocking |
| `post_merge_flow_completion` | PRマージ後のフローステップ（issue_updated）を自動完了。 | warning |
| `post_merge_observation_issue` | PRマージ後に動作確認Issueを自動作成。 | warning |
| `post_merge_reflection_enforcer` | PRマージ成功後に振り返りを即時実行させる。 | warning |
| `pr_body_quality_check` | PRボディに「なぜ」と「参照」が含まれているかチェックする。 | warning |
| `pr_defer_check` | PR/Issue説明文に「後で」系キーワードがIssue参照なしで含まれる場合にブロックする。 | warning |
| `pr_issue_alignment_check` | PR作成時に対象Issueの受け入れ条件を検証する。 | warning |
| `pr_issue_assign_check` | gh pr create 時に Closes で参照される Issue のアサイン確認・自動アサイン。 | warning |
| `pr_merge_pull_reminder` | PostToolUse hook to auto-pull main after PR merge. | logging |
| `pr_metrics_collector` | PRメトリクス自動収集フック（PostToolUse） | warning |
| `pr_overlap_check` | git push/gh pr create時に他PRとのファイル重複を警告。 | warning |
| `pr_related_issue_check` | gh pr create 時に関連オープンIssueの確認を促す。 | warning |
| `pr_scope_check` | gh pr create時に1 Issue = 1 PRルールを強制。 | blocking |
| `pr_test_coverage_check` | gh pr create時に変更されたフックのテストカバレッジを確認。 | warning |
| `problem_report_check` | セッション終了時に問題報告とIssue作成の整合性を確認。 | warning |
| `production_url_warning` | 本番環境URLへのアクセス前に警告・確認を促す。 | warning |
| `python_hook_guard` | Pythonフック新規作成をブロックし、TypeScript使用を推奨する | warning |
| `recurring_problem_block` | 繰り返し発生する問題を検出し、Issue作成を強制してからマージを許可。 | warning |
| `reference_comment_check` | Edit時に「〜と同じ」参照スタイルのコメントを検出して警告。 | warning |
| `reflection_completion_check` | PRマージ後または/reflecting-sessions skill invoke後の振り返り完了を検証する。 | warning |
| `reflection_log_collector` | 振り返りスキル実行時にセッションログを自動集計して提供。 | warning |
| `reflection_progress_tracker` | 振り返り中のIssue作成を検出して進捗を追跡。 | logging |
| `reflection_quality_check` | 振り返りの形式的評価を防ぐ（ブロック回数との矛盾検出、改善点Issue化強制）。 | warning |
| `reflection_reminder` | PRマージや一定アクション後に振り返りをリマインド。 | logging |
| `reflection_self_check` | 振り返りの観点網羅性を確認し、抜けがあればブロック。 | blocking |
| `regex_pattern_reminder` | 正規表現パターン実装時にAGENTS.mdチェックリストを表示。 | warning |
| `related_task_check` | セッション終了時にセッション内作成Issueのステータスを確認し、未完了ならブロック。 | warning |
| `reply_resolve_enforcer` | レビューコメント返信後のResolve実行を強制する。 | blocking |
| `research_requirement_check` | Issue/PR作成前にWeb調査を強制する。 | blocking |
| `research_tracker` | セッション内のWebSearch/WebFetch使用を追跡。 | logging |
| `resolve_thread_guard` | レビュースレッドResolve時に応答コメントを強制。 | blocking |
| `review_comment_action_reminder` | レビューコメント読み込み後にアクション継続を促すリマインダー。 | warning |
| `review_promise_tracker` | レビュー返信で「別Issue対応」と約束した場合のIssue作成追跡フック。 | warning |
| `review_response_check` | AIレビューのMEDIUM以上の指摘に対する対応を強制する。 | blocking |
| `reviewer_removal_check` | PreToolUse hook: Block removal of AI reviewers from PRs. | warning |
| `rework_tracker` | 同一ファイルへの短時間複数編集（手戻り）を追跡。 | warning |
| `rule_consistency_check` | AGENTS.md編集時にルール間の矛盾を検知する。 | warning |
| `rule_enforcement_check` | AGENTS.mdにルール追加時、対応する強制機構の存在を検証する。 | warning |
| `scope_check` | 作業中Issueへのスコープ外タスク混入を検出する | warning |
| `script_error_ignore_warning` | スクリプトエラー無視防止フック | warning |
| `script_test_reminder` | PostToolUse hook: Remind to add tests when new functions are added to scripts. | logging |
| `secret_deploy_check` | セッション終了時に未デプロイのフロントエンドシークレットがないか確認。 | warning |
| `secret_deploy_trigger` | VITE_プレフィックスのシークレット更新を記録。 | logging |
| `security_bypass_test_reminder` | セキュリティガードファイル編集時にバイパステストの追加を促す。 | logging |
| `session_end_main_check` | Stop hook to verify main branch is up-to-date at session end. | warning |
| `session_end_worktree_cleanup` | セッション終了時のworktree自動クリーンアップ。 | logging |
| `session_file_state_check` | セッション再開時ファイル状態検証フック（SessionStart） | warning |
| `session_handoff_reader` | セッション開始時に前回の引き継ぎメモを読み込み表示。 | logging |
| `session_handoff_writer` | セッション終了時に引き継ぎメモを生成。 | warning |
| `session_issue_integrity_check` | セッション別Issue追跡データの整合性を検証。 | warning |
| `session_log_compressor` | セッション終了時にローテート済みログを圧縮。 | warning |
| `session_marker_refresh` | worktree内のセッションマーカーのmtimeを定期更新。 | warning |
| `session_marker_updater` | セッション開始時にworktree内のセッションマーカーを更新。 | warning |
| `session_metrics_collector` | セッションメトリクス収集フック（Stop） | warning |
| `session_outcome_collector` | セッション終了時に成果物（PR、Issue、コミット）を収集。 | warning |
| `session_resume_warning` | セッション再開時に競合状況警告を表示。 | warning |
| `session_todo_check` | セッション終了時に未完了TODOを検出して警告。 | warning |
| `session_worktree_status` | セッション開始時に既存worktreeの状況を確認し警告する。 | warning |
| `signature_change_check` | Pythonの関数シグネチャ変更時にテスト更新漏れを検出。 | warning |
| `similar_code_check` | 新規フック作成時に類似コードを検索して参考情報を提供。 | warning |
| `similar_pattern_search` | PRマージ後にコードベース内の類似パターンを検索し修正漏れを防ぐ。 | warning |
| `skill_failure_detector` | Skill呼び出し失敗を検出して調査・Issue化を促す。 | logging |
| `skill_usage_reminder` | 特定操作の前にSkill使用を強制。 | warning |
| `skip_review_env_check` | SKIP_CODEX_REVIEW/SKIP_GEMINI_REVIEW環境変数の使用を禁止する。 | blocking |
| `stop_auto_review` | セッション終了時に未レビューの変更を検出しレビュー実行を促す。 | logging |
| `subprocess_lint_check` | Pythonフック内の問題のあるsubprocess使用パターンを検出。 | warning |
| `systematization_check` | セッション終了時に教訓が仕組み化されたか確認する。 | warning |
| `systematization_issue_close_check` | 仕組み化Issueクローズ時にフック/ツール実装を検証。 | blocking |
| `task_start_checklist` | タスク開始時に確認チェックリストをリマインド表示する。 | warning |
| `test_deletion_check` | Python関数/クラス削除時のテスト参照漏れを検出。 | warning |
| `tool_efficiency_tracker` | ツール呼び出しパターンを追跡し非効率なパターンを検出。 | warning |
| `tool_substitution_detector` | パッケージマネージャの実行とツール代替パターンを追跡。 | warning |
| `ui_check_reminder` | フロントエンド変更時にブラウザ確認を強制。 | warning |
| `user_feedback_systematization_check` | ユーザーフィードバック検出時の仕組み化を確認する。 | warning |
| `uv_run_guard` | worktree内でのuv run使用を防止 | warning |
| `vague_action_block` | 曖昧な対策表現（精神論）を検出してACTION_REQUIRED警告。 | warning |
| `workflow_skill_reminder` | worktree作成・PR作成時にmanaging-development Skillを参照するようリマインド。 | warning |
| `workflow_verifier` | ワークフロー実行を検証するユーティリティモジュール。 | warning |
| `worktree_auto_cleanup` | PRマージ成功後にworktreeを自動削除。 | warning |
| `worktree_auto_setup` | worktree作成成功後にsetup_worktree.shを自動実行。 | warning |
| `worktree_cleanup_suggester` | セッション終了時にマージ/クローズ済みPRのworktreeクリーンアップを提案。 | warning |
| `worktree_commit_integrity_check` | セッション開始時にworktree内のコミット整合性をチェック。 | warning |
| `worktree_creation_marker` | worktree作成時にセッションIDをマーカーファイルとして記録する。 | warning |
| `worktree_main_freshness_check` | worktree作成前にmainブランチが最新か確認。 | warning |
| `worktree_path_guard` | worktree作成先が.worktrees/内かを検証。 | warning |
| `worktree_removal_check` | worktree削除前にアクティブな作業やcwd衝突を検出。 | warning |
| `worktree_resume_check` | マージ済みPRのworktreeからgit push/gh pr createを実行しようとした際にブロックする。 | blocking |
| `worktree_session_guard` | 別セッションが作業中のworktreeへの誤介入を防止する。 | warning |
| `worktree_warning` | mainブランチでの編集をブロックし、worktreeでの作業を強制する。 | warning |

## ライブラリ一覧（全51件）
| ライブラリ | 説明 |
|-----------|------|
| `ai_review_checker` | merge-check用のAIレビュー状態確認ユーティリティ。 |
| `block_patterns` | ブロック→成功パターンの追跡と分析を行う。 |
| `check_utils` | merge-check関連の共通ユーティリティ。 |
| `ci_monitor_ai_review` | AI reviewer utilities for ci-monitor. |
| `cli_review` | CLI review utilities for Plan AI review hooks. |
| `command` | Command parsing utilities for hook scripts. |
| `command_parser` | locked-worktree-guard用のコマンド解析ユーティリティ。 |
| `common` | Claude Codeフック共通のディレクトリ定数とラッパー関数。 |
| `constants` | フック共通の定数を一元管理 |
| `cwd` | カレントワーキングディレクトリの検出・検証を行う。 |
| `execution` | Hook execution logging. |
| `fix_verification_checker` | 修正主張と検証チェックの関数群（merge-check用モジュール）。 |
| `flow` | フロー有効性トラッキング機能を提供する。 |
| `flow_constants` | Flow関連の共通定数モジュール。 |
| `flow_definitions` | Flowの定義モジュール - 全フロー設定の単一真実源。 |
| `format_error` | Format an unknown error value for logging. |
| `gh_utils` | GitHub CLI（gh）コマンド関連のユーティリティ |
| `git` | Git関連のユーティリティ関数を提供する。 |
| `github` | GitHub CLI（gh）関連のユーティリティ関数を提供する。 |
| `guard_rules` | locked-worktree-guardのガードルールと検証ロジック。 |
| `index` | TypeScript Hooks Library |
| `input_context` | フック入力からのコンテキスト抽出ユーティリティ |
| `issue_checker` | Issue・受け入れ基準チェック機能。 |
| `json` | JSON parsing utilities. |
| `labels` | gh CLIコマンドからのラベル抽出・分析ユーティリティを提供する。 |
| `logging` | ログレベル分離とエラーコンテキスト管理を提供する。 |
| `markdown` | Markdown parsing utilities. |
| `markers` | マーカーファイル操作のユーティリティ |
| `merge_conditions` | merge-checkフックのマージ条件チェックを集約・オーケストレーションする。 |
| `monitor_state` | Monitor state management for ci-monitor. |
| `option_parser` | 汎用CLIオプションパーサー |
| `path_validation` | パストラバーサル防止のためのパス検証ユーティリティを提供する。 |
| `plan_review_patterns` | Plan AIレビューの判定パターン |
| `plan_review_state` | イテレーティブPlan AIレビューの状態管理 |
| `rate_limit` | GitHub API rate limit management for ci-monitor. |
| `reflection` | Reflection-related utilities for hooks. |
| `repo` | リポジトリ関連のユーティリティ関数を提供する。 |
| `research` | リサーチ・探索活動の追跡ユーティリティ |
| `results` | フック結果（block/approve）の生成ユーティリティ |
| `review` | AIレビュー（Copilot/Codex）のコメント追跡・分析を提供する。 |
| `review_checker` | merge-checkフックのレビューコメント・スレッド検証機能。 |
| `session` | セッション識別・追跡機能 |
| `shell_tokenizer` | locked-worktree-guard用の低レベルシェルトークン化ユーティリティ。 |
| `spawn` | Bun用の非同期スポーン関数。 |
| `strings` | 純粋な文字列操作ユーティリティ |
| `timestamp` | タイムスタンプ関連のユーティリティ関数 |
| `timing` | フック実行時間の計測ユーティリティ |
| `transcript` | トランスクリプトファイルからの情報抽出を行う共通関数を提供する。 |
| `types` | Hook入出力の型定義 |
| `workflow_verifier` | ワークフロー実行を検証するユーティリティモジュール。 |
| `worktree_manager` | worktree状態管理のユーティリティモジュール。 |

## スクリプト一覧（全36件）
| スクリプト | 説明 |
|-----------|------|
| `analyze_hook_dependencies` | フックのlib/依存関係を分析しMermaid図を生成する。 |
| `analyze_session_outcomes` | セッション成果データを分析する。 |
| `batch_resolve_threads` | PRの未解決レビュースレッドを一括resolveする。 |
| `check_hook_test_coverage` | フックのテストカバレッジをチェックする。 |
| `check_locale_privacy_terms` | プライバシー/利用規約セクションがja.jsonのみに存在するか確認する。 |
| `check_sentry_usage` | Sentryスコープリークパターンを検出する。 |
| `check_ts_hook_execution` |  |
| `ci_monitor_ts/events` | Event emission and logging for ci-monitor. |
| `ci_monitor_ts/github_api` | GitHub API communication functions for ci-monitor. |
| `ci_monitor_ts/index` | CI Monitor TypeScript Entry Point |
| `ci_monitor_ts/main_loop` | Main monitoring loop for ci-monitor. |
| `ci_monitor_ts/main` | CI Monitor TypeScript CLI Entry Point |
| `ci_monitor_ts/monitor` | Sanitize a value for safe logging by removing control characters. |
| `ci_monitor_ts/pr_operations` | PR operations for ci-monitor. |
| `ci_monitor_ts/review_comments` | Review comment operations for ci-monitor. |
| `ci_monitor_ts/session` | Session management re-exports for ci-monitor. |
| `ci_monitor_ts/worktree` | Worktree management for ci-monitor. |
| `codex_design_review` |  |
| `compare-hook-output` | Python/TypeScriptフック出力比較ツール |
| `confirm_ui_check` | UI確認完了を記録しコミットをアンブロックする。 |
| `enforcement_coverage_audit` | AGENTS.mdの強制ルール数と実際のhook/CIチェック数の比率を算出する。 |
| `generate_index` | .fdp/index.json と .fdp/README.md を再生成するスクリプト |
| `hook_lint_ts` | TypeScriptフック専用のカスタムLintルールを適用する。 |
| `observation_verifier_ts/main` | Observation Issue自動検証スクリプト |
| `parallel_review` | codex review と gemini /code-review を並列実行するスクリプト |
| `review_contradiction_check` | AIレビューコメントの矛盾可能性を検出する。 |
| `setup_agent_cli` |  |
| `setup_worktree` |  |
| `ts/check_new_file_typecheck` | 新規TypeScript/JavaScriptファイルの型エラーをチェックする。 |
| `ts/compare_typecheck_errors` | mainブランチとの型エラー数を比較する。 |
| `ts/diff_hook_output` | Python/TypeScriptフック出力の差分比較ツール。 |
| `ts/record_review_response` | レビューコメントへの対応を記録する。 |
| `ts/review_respond` | レビューコメントに返信してスレッドをresolveする。 |
| `validate_hooks_settings` | settings.json内のフックファイル参照を検証する。 |
| `validate_lefthook` | lefthook.yml設定を検証する。 |
| `verify-subprocess-compat` | Bunでコマンドを実行 |

## スキル一覧（全11件）
| スキル | 説明 |
|--------|------|
| `adding-perspectives` | 振り返り観点追加ガイド |
| `analyzing-trends` | 傾向分析 |
| `applying-standards` | コーディング規約 |
| `authoring-skills` | Skill作成ガイド |
| `exploring-claude-code` | Claude Code機能調査ガイド |
| `implementing-hooks` | フックリファレンス |
| `managing-development` | 開発ワークフロー |
| `reflecting-sessions` | 振り返り実行ガイド |
| `reviewing-code` | コードレビュー対応 |
| `simplifying-code` | Code Simplifier |
| `troubleshooting` | トラブルシューティング |
