# .claude/hooks/ ディレクトリ

Claude Codeフックシステム。PreToolUse、PostToolUse、Stop、SessionStart、UserPromptSubmitの5つのイベントタイプで動作する品質保証・ワークフロー強制機構。

## 実装ファイル一覧（190個）

### ドキュメント・設定ファイル（4個）

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|-----------|------------------|-------------------|--------|
| `ARCHITECTURE.md` | フック群の依存関係と設計パターンを一覧化することで、新規フック開発時の設計判断を容易にする | 179個のフックとライブラリの依存関係をドキュメント化。コア(50%+依存)とサポート(10-50%)ライブラリを分類 | フック新規開発時の設計参考として参照 |
| `metadata.json` | フックのライフサイクルメタデータを管理する | 各フックのcreated_at、purpose、expected_block_rate、status、triggerを記録 | analyze-stale-hooks.pyから参照 |
| `metadata.schema.json` | metadata.jsonのスキーマを定義する | required fields: created_at、purpose、status、triggerを検証 | メタデータバリデーション時に参照 |
| `settings.json` | フック設定を管理する | フックの有効/無効、パラメータ設定を保存 | 各フックから参照 |

### 共通モジュール（10個）

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|-----------|------------------|-------------------|--------|
| `common.py` | 複数フックで共通して必要な処理を一箇所にまとめることで、重複実装を避ける | セッションマーカーチェック、ログディレクトリ定数、共通ユーティリティ関数を提供 | 他のフックからimportして使用 |
| `check_utils.py` | 複数のフックで共通して必要なチェック処理を一箇所にまとめることで、重複実装を避ける | Issue存在確認、PRリンクチェック、受け入れ条件チェック等のユーティリティ関数を提供 | 他のフックからimportして使用 |
| `command_parser.py` | gh prコマンドやgit worktreeコマンドを正確に解析し、ロック中worktreeへの操作を検出する | Git worktreeコマンド検出・パス抽出、gh prコマンド解析、ci-monitor.pyコマンド検出 | locked-worktree-guard.pyから使用 |
| `shell_tokenizer.py` | シェルコマンドを正確に解析するための低レベルトークン化ユーティリティ | シェル演算子の正規化、リダイレクト検出、cdターゲット抽出、rmコマンドパス抽出 | command_parser.pyから使用 |
| `guard_rules.py` | Worktree関連の危険な操作を検出し、適切なブロックまたは警告を行う | 自己ブランチ削除チェック、worktree削除の安全性チェック、rm対象worktreeチェック、孤立worktree削除チェック | locked-worktree-guard.pyから呼び出される |
| `worktree_manager.py` | worktree状態管理のユーティリティモジュール | worktree一覧取得、セッション所有権チェック、アクティブ作業検出、rm対象worktree検出 | locked-worktree-guard等から使用 |
| `flow_constants.py` | フロー制御で使用する定数を一箇所で管理する | フェーズ名、ステータス、閾値等の定数を定義 | 他のフローフックからimportして使用 |
| `flow_definitions.py` | 開発フローの定義を一箇所で管理する | Issue着手からマージまでのフェーズ定義、遷移ルールを提供 | フローフックからimportして使用 |
| `merge_conditions.py` | マージ前の全条件チェックをオーケストレーションする | 12+のチェック（AIレビュー、Issue状態、テスト等）を集約実行 | merge-check.pyから呼び出される |
| `ai_review_checker.py` | AIレビュー完了していないPRをマージすると品質リスクがあるため、事前確認する | gh pr viewでAIレビューコメントの有無を確認。未レビュー時はブロック理由を返す | merge_conditions.pyから呼び出される。単体実行不可 |

### SessionStartフック（20個）

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|-----------|------------------|-------------------|--------|
| `active-worktree-check.py` | 複数セッション間で同じIssueへの重複着手を防止するため、既存の作業状況を把握する必要がある | 作業中のworktree（PRがOPEN/未作成）を検出し、ブランチ名、PR状態、最終コミット情報を表示 | SessionStart時に自動発火。情報提供のみ |
| `continuation-session-metrics.py` | セッション継続時のメトリクスを記録し、長時間セッションの分析に活用する | セッション継続イベントを検出し、経過時間、ツール呼び出し数等を記録 | SessionStart時に自動発火 |
| `date-context-injector.py` | 日付コンテキストをセッションに注入し、時間依存の判断を可能にする | 現在日時、曜日、祝日情報をsystemMessageで提供 | SessionStart時に自動発火 |
| `fork-session-collaboration-advisor.py` | Fork-sessionが親セッションと競合するIssueに着手するとコンフリクトが発生するため、独立したIssue候補を提案する | Fork-session検出時、親/siblingセッションのworktreeを特定し、競合しない独立したIssue候補を提案 | SessionStart時に自動発火。提案のみでブロックしない |
| `fork-session-id-updater.py` | Fork-sessionのセッションIDを正しく更新し、ログの紐付けを正確にする | Fork-session検出時、新しいセッションIDでマーカーファイルを更新 | SessionStart時に自動発火 |
| `git-status-check.py` | git statusで未コミット変更を確認してから作業開始を促す | セッション開始時、未コミット変更があれば警告 | SessionStart時に自動発火 |
| `main_sync_check.py` | ローカルmainがリモートより遅れていると新しいフック/修正が適用されず問題が発生するため、同期状態を確認して早期警告する | git fetchでリモート情報を更新し、ローカルmainとorigin/mainのコミット差分を確認 | SessionStart時に自動発火。警告のみでブロックしない |
| `merged-worktree-check.py` | PRがマージされた後もworktreeが残っているとディスクを圧迫するため、マージ済みworktreeを検知して削除を促す | .worktrees/内のworktreeを列挙し、各worktreeのブランチに関連するPRがマージ済みか確認して警告 | SessionStart時に自動発火。警告のみでブロックしない |
| `multi-issue-guard.py` | 複数Issueへの同時着手を検出し、フォーカスを促す | worktree状態から複数Issueへの着手を検出して警告 | SessionStart時に自動発火 |
| `observation-session-reminder.py` | セッション開始時に観察の重要性を通知する | セッション開始時に観察ガイダンスを表示 | SessionStart時に自動発火 |
| `open-issue-reminder.py` | オープンIssueの問題を意識するよう促す | セッション開始時、関連するオープンIssueを表示 | SessionStart時に自動発火 |
| `orphan-worktree-check.py` | git未登録のworktreeディレクトリを検出する | .worktrees/内でgit worktree listに登録されていないディレクトリを検出して警告 | SessionStart時に自動発火 |
| `session-file-state-check.py` | セッションファイルの状態をチェックする | セッションマーカー、ログファイルの整合性を確認 | SessionStart時に自動発火 |
| `session-handoff-reader.py` | 引き継ぎプロンプトを読み込む | 前セッションからの引き継ぎファイルを検出して表示 | SessionStart時に自動発火 |
| `session-resume-warning.py` | セッション再開時の注意事項を表示する | 中断されたセッションの再開時に状態確認を促す警告を表示 | SessionStart時に自動発火 |
| `session-worktree-status.py` | セッション開始時にworktree状態を表示する | 現在のworktree、ブランチ、未コミット変更を表示 | SessionStart時に自動発火 |
| `worktree-cleanup-suggester.py` | worktreeクリーンアップを提案する | 古いworktreeを検出してクリーンアップを提案 | SessionStart時に自動発火 |
| `worktree-commit-integrity-check.py` | worktreeのコミット整合性をチェックする | worktree内の未プッシュコミットを検出して警告 | SessionStart時に自動発火 |
| `worktree-main-freshness-check.py` | worktreeのmainブランチの鮮度をチェックする | worktreeのベースブランチがorigin/mainより遅れている場合に警告 | SessionStart時に自動発火 |

### UserPromptSubmitフック（2個）

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|-----------|------------------|-------------------|--------|
| `feedback-detector.py` | ユーザーからのフィードバック（指摘、修正依頼）を検出し、Issue化を促す | ユーザー入力から「違う」「間違い」等のキーワードを検出 | UserPromptSubmit時に自動発火 |

### PreToolUse:Bashフック（80個）

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|-----------|------------------|-------------------|--------|
| `acceptance_criteria_reminder.py` | 受け入れ条件未完了のままPR作成するとmerge-checkでブロックされるため、PR作成時に事前警告する | gh pr createコマンド検出時、ブランチ名からIssue番号を抽出し、未完了のチェックボックスを警告表示 | PreToolUse:Bashで自動発火。警告のみでブロックしない |
| `branch_rename_guard.py` | ブランチリネームはmain/masterのgit設定破損、リモートとの不整合、他セッションとの競合を引き起こす可能性がある | git branch -m/-M/--moveコマンドを検出してブロック。SKIP_BRANCH_RENAME_GUARD=1でバイパス可能 | PreToolUse:Bashで自動発火 |
| `bug-issue-creation-guard.py` | バグ報告は再現手順と期待動作の記載が必要なため、不完全なIssue作成を防止する | gh issue createコマンドでbugラベル付きIssue作成時、必須セクションの有無をチェック | PreToolUse:Bashで自動発火 |
| `checkout-block.py` | mainブランチへのcheckoutはworktree運用を破壊するため、ブロックする | git checkout main/masterコマンドを検出してブロック。SKIP_CHECKOUT_BLOCK=1でバイパス可能 | PreToolUse:Bashで自動発火 |
| `ci-monitor-session-id-check.py` | ci-monitor.pyが--session-idなしで実行されるとログが正しいセッションと紐付かないため、警告する | ci-monitor.pyコマンドで--session-idオプションの有無をチェックし、なければ警告表示 | PreToolUse:Bashで自動発火 |
| `closes-keyword-check.py` | PRにCloses #123形式がないとIssueが自動クローズされないため、チェックする | gh pr createコマンドのbodyオプションからCloses/Fixes/Resolves #NNN形式を検出 | PreToolUse:Bashで自動発火 |
| `closes-scope-check.py` | PRでIssue範囲外の変更を行うとスコープクリープになるため、警告する | PRの変更ファイルとIssueの対象範囲を比較し、範囲外の変更があれば警告 | PreToolUse:Bashで自動発火 |
| `closes-validation.py` | Closes #NNNで参照されたIssueが存在しない、またはクローズ済みの場合にPR作成を防止する | gh issue viewでIssue存在確認とステータスチェックを実行 | PreToolUse:Bashで自動発火 |
| `codex-review-check.py` | Codexレビュー結果を確認せずに作業を続けるとレビュー指摘を見落とすため、確認を促す | gh pr view --commentsでCodexレビューコメントの有無を確認し、未確認時に警告 | PreToolUse:Bashで自動発火 |
| `commit-amend-block.py` | git commit --amendはpush済みコミットへの適用で履歴破壊を招くため、条件付きでブロックする | --amendフラグを検出し、push済みの場合はブロック。未pushの場合は警告のみ | PreToolUse:Bashで自動発火 |
| `commit-message-template.py` | コミットメッセージの品質を統一するため、テンプレートを提案する | git commitコマンド検出時、推奨フォーマットのテンプレートをsystemMessageで表示 | PreToolUse:Bashで自動発火 |
| `commit-message-why-check.py` | 「何を」だけでなく「なぜ」を含むコミットメッセージを促進する | コミットメッセージから「Why」セクションの有無を検出し、欠如時に警告 | PreToolUse:Bashで自動発火 |
| `dependabot-skill-reminder.py` | Dependabot PRの対応時に専用スキルの使用を促す | dependabot/で始まるブランチを検出し、Dependabotスキル使用を提案 | PreToolUse:Bashで自動発火 |
| `duplicate-issue-check.py` | 重複Issueの作成を防止する | gh issue createコマンド実行時、類似タイトルのIssueを検索して警告 | PreToolUse:Bashで自動発火 |
| `force-push-guard.py` | git push --forceは履歴破壊を招くため、ブロックする | --force、-fフラグを検出してブロック。SKIP_FORCE_PUSH_GUARD=1でバイパス可能 | PreToolUse:Bashで自動発火 |
| `git-config-check.py` | git configの意図しない変更を防止する | git config --globalコマンドを検出して警告 | PreToolUse:Bashで自動発火 |
| `issue-body-requirements-check.py` | Issue本文の必須セクションをチェックする | gh issue createコマンドで「なぜ」「現状」「期待動作」「対応案」の有無を確認 | PreToolUse:Bashで自動発火 |
| `issue-comments-check.py` | Issueコメントを確認してから作業開始を促す | Issue着手時、未読コメントがあれば警告 | PreToolUse:Bashで自動発火 |
| `issue-incomplete-close-check.py` | 未完了のIssueクローズを防止する | gh issue closeコマンド実行時、受け入れ条件の完了状況を確認 | PreToolUse:Bashで自動発火 |
| `issue-label-check.py` | Issueに必須ラベルがあるかチェックする | gh issue createコマンドでラベルの有無を確認 | PreToolUse:Bashで自動発火 |
| `issue-multi-problem-check.py` | 1つのIssueに複数の問題が含まれていないかチェックする | Issue本文から複数問題のパターンを検出して警告 | PreToolUse:Bashで自動発火 |
| `issue-priority-label-check.py` | Issueに優先度ラベル（P0-P3）があるかチェックする | gh issue createコマンドで優先度ラベルの有無を確認し、なければブロック | PreToolUse:Bashで自動発火 |
| `issue-reference-check.py` | コミットやPRにIssue参照があるかチェックする | git commitメッセージやPR本文からIssue参照を検出 | PreToolUse:Bashで自動発火 |
| `issue-review-response-check.py` | Issueへのレビューコメントに対応しているかチェックする | Issue着手時、未対応のレビューコメントがあれば警告 | PreToolUse:Bashで自動発火 |
| `issue-scope-check.py` | Issueのスコープが適切かチェックする | Issue本文から複数の目的、広すぎるスコープを検出して警告 | PreToolUse:Bashで自動発火 |
| `locked-worktree-guard.py` | ロックされたworktreeへの操作を検出し、セッション間競合を防止する | git worktree remove、rm -rf等でロック中worktree削除を検出してブロック | PreToolUse:Bashで自動発火 |
| `merge-check.py` | マージ前の安全チェックを強制する | --auto、--admin、REST APIマージをブロック。各種マージ条件をチェック | PreToolUse:Bashで自動発火 |
| `merge-commit-quality-check.py` | マージコミットの品質をチェックする | マージコミットメッセージのフォーマットを検証 | PreToolUse:Bashで自動発火 |
| `merge-confirmation-warning.py` | マージ前の最終確認を促す | gh pr mergeコマンド実行前に確認警告を表示 | PreToolUse:Bashで自動発火 |
| `open-pr-warning.py` | オープンPRへの介入を警告する | オープンPRが存在するIssueへの着手時に警告 | PreToolUse:Bashで自動発火 |
| `pr_related_issue_check.py` | PRに関連Issueがリンクされているかチェックする | gh pr createコマンドでIssue参照の有無を確認 | PreToolUse:Bashで自動発火 |
| `pr-body-quality-check.py` | PR本文の品質をチェックする | Summary、Test planセクションの有無を確認 | PreToolUse:Bashで自動発火 |
| `pr-issue-alignment-check.py` | PRの変更がIssueの目的と整合しているかチェックする | PR変更内容とIssue本文を比較し、不整合があれば警告 | PreToolUse:Bashで自動発火 |
| `pr-issue-assign-check.py` | PR作成時にIssueがアサインされているかチェックする | gh pr createコマンド実行時、関連Issueのアサイン状況を確認 | PreToolUse:Bashで自動発火 |
| `pr-overlap-check.py` | 同一Issueへの重複PR作成を検出する | gh pr createコマンド実行時、同一IssueへのオープンPRを検索して警告 | PreToolUse:Bashで自動発火 |
| `pr-scope-check.py` | PRのスコープが適切かチェックする | 変更ファイル数、行数が閾値を超える場合に警告 | PreToolUse:Bashで自動発火 |
| `pr-test-coverage-check.py` | PRにテストが含まれているかチェックする | 変更ファイルに対応するテストファイルの有無を確認 | PreToolUse:Bashで自動発火 |
| `problem-report-check.py` | 問題報告の品質をチェックする | 問題報告時に再現手順、期待動作の記載を確認 | PreToolUse:Bashで自動発火 |
| `production-url-warning.py` | 本番URL（dekita.app）への操作を警告する | 本番環境へのAPI呼び出しを検出して警告 | PreToolUse:Bashで自動発火 |
| `recurring-problem-block.py` | 繰り返し発生する問題をブロックし、根本対策を促す | 同一パターンのエラーが繰り返し発生する場合にブロック | PreToolUse:Bashで自動発火 |
| `related-task-check.py` | 関連タスクの有無をチェックする | Issue着手時、関連する未完了タスクを検索して表示 | PreToolUse:Bashで自動発火 |
| `reviewer-removal-check.py` | レビュアー削除を検出して警告する | gh pr edit --remove-reviewerコマンドを検出して警告 | PreToolUse:Bashで自動発火 |
| `security_bypass_test_reminder.py` | セキュリティバイパス（SKIP_*環境変数）のテストを促す | SKIP_*環境変数使用時、セキュリティテストの実行を促す警告を表示 | PreToolUse:Bashで自動発火 |
| `systematization-issue-close-check.py` | 仕組み化Issueのクローズ条件をチェックする | 仕組み化Issueクローズ時、強制機構の実装を確認 | PreToolUse:Bashで自動発火 |
| `test-deletion-check.py` | テストファイル削除を検出して警告する | テストファイルの削除を検出して警告 | PreToolUse:Bashで自動発火 |
| `tool-substitution-detector.py` | 推奨ツールの代替使用を検出する | Bashでのcat使用等、専用ツールの代替使用を検出して警告 | PreToolUse:Bashで自動発火 |
| `uv_run_guard.py` | uv run ruff使用を検出してuvx使用を促す | uv run ruffコマンドを検出してブロック。uvx ruffの使用を促す | PreToolUse:Bashで自動発火 |
| `worktree-auto-setup.py` | worktreeを自動セットアップする | Issue着手時、worktreeを自動作成してロック | PreToolUse:Bashで自動発火 |
| `worktree-path-guard.py` | worktreeパスの安全性をチェックする | 危険なパス（/、/home等）へのworktree作成を検出してブロック | PreToolUse:Bashで自動発火 |
| `worktree-removal-check.py` | worktree削除の安全性をチェックする | git worktree removeコマンド実行時、未コミット変更の有無を確認 | PreToolUse:Bashで自動発火 |
| `worktree-session-guard.py` | worktreeのセッション間競合を防止する | 他セッションが作業中のworktreeへの操作を検出してブロック | PreToolUse:Bashで自動発火 |
| `worktree-warning.py` | worktree操作の警告を表示する | worktree関連の危険な操作を検出して警告 | PreToolUse:Bashで自動発火 |

### PreToolUse:Edit/Writeフック（25個）

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|-----------|------------------|-------------------|--------|
| `branch_check.py` | mainブランチでの直接作業は危険なため、worktreeでの作業を強制する | mainブランチでのファイル編集を検出し、worktree作成を促すメッセージでブロック | PreToolUse:Edit/Writeで自動発火 |
| `cwd-check.py` | カレントディレクトリがworktree外の場合、意図しないファイル編集を防止する | cwdがworktree内かチェックし、外部の場合は警告を表示 | PreToolUse:Edit/Writeで自動発火 |
| `dependency-check-reminder.py` | 依存関係変更時にセキュリティチェックを促す | package.json、requirements.txt等の変更を検出し、依存関係チェックを提案 | PreToolUse:Edit/Writeで自動発火 |
| `doc_edit_check.py` | ドキュメント変更時にコード変更も必要かチェックする | *.md、*.txt等のドキュメント編集時、関連コード変更の有無を確認 | PreToolUse:Edit/Writeで自動発火 |
| `e2e-test-check.py` | UI変更時にE2Eテストの更新が必要かチェックする | frontend/配下のファイル変更時、関連E2Eテストの有無を確認 | PreToolUse:Edit/Writeで自動発火 |
| `empty-return-check.py` | 空のreturnでフックが終了するとログが残らないため、適切なログ出力を促す | フックコードの編集時、空returnパターンを検出して警告 | PreToolUse:Edit/Writeで自動発火 |
| `environment-integrity-check.py` | 環境設定の整合性をチェックし、設定ミスを防止する | .env、settings.json等の変更時、必須設定の有無を確認 | PreToolUse:Edit/Writeで自動発火 |
| `existing-impl-check.py` | 既存実装を無視した重複実装を防止する | 新規ファイル作成時、類似機能の既存実装を検索して警告 | PreToolUse:Writeで自動発火 |
| `file_overwrite_warning.py` | 既存ファイルの上書きを検出し、意図しないデータ損失を防止する | Writeツールで既存ファイルを上書きする場合に警告 | PreToolUse:Writeで自動発火 |
| `file-size-warning.py` | 大きすぎるファイルの作成を検出し、警告する | 作成するファイルのサイズが閾値を超える場合に警告 | PreToolUse:Writeで自動発火 |
| `hook-change-detector.py` | フックファイルの変更を検出し、テスト実行を促す | .claude/hooks/配下のファイル変更を検出して警告 | PreToolUse:Edit/Writeで自動発火 |
| `hook-dev-warning.py` | フック開発中の注意事項を表示する | フックファイル編集時、テスト実行とレビュー依頼を促す警告を表示 | PreToolUse:Edit/Writeで自動発火 |
| `hook-return-format-check.py` | フックの戻り値形式が正しいかチェックする | フックコードの編集時、JSON出力形式を検証 | PreToolUse:Edit/Writeで自動発火 |
| `hooks-design-check.py` | フック設計のベストプラクティスをチェックする | Why/What/Remarksセクションの有無、fail-open設計を確認 | PreToolUse:Edit/Writeで自動発火 |
| `merit-demerit-check.py` | 設計判断時にメリット・デメリットの検討を促す | 新規ファイル作成や大きな変更時に設計検討を促す警告を表示 | PreToolUse:Edit/Writeで自動発火 |
| `parallel-edit-conflict-check.py` | 並行編集によるコンフリクトリスクを検出する | 同一ファイルへの並行編集を検出して警告 | PreToolUse:Edit/Writeで自動発火 |
| `planning-enforcement.py` | 計画なしの実装開始を防止する | 新規機能実装開始時、計画ファイルの有無を確認 | PreToolUse:Edit/Writeで自動発火 |
| `reference_comment_check.py` | 参照コメント（Issue、PR等へのリンク）の有無をチェックする | コード内のTODO、FIXME等に参照リンクがあるか確認 | PreToolUse:Edit/Writeで自動発火 |
| `regex-pattern-reminder.py` | 正規表現使用時のベストプラクティスを通知する | 正規表現を含むコード編集時、エスケープやフラグの注意点を表示 | PreToolUse:Edit/Writeで自動発火 |
| `research-requirement-check.py` | リサーチが必要な変更かチェックする | 新規技術導入時、事前リサーチの実施を促す警告を表示 | PreToolUse:Edit/Writeで自動発火 |
| `signature_change_check.py` | 関数シグネチャの変更を検出する | 関数/メソッドのシグネチャ変更時、呼び出し側の更新を促す警告を表示 | PreToolUse:Edit/Writeで自動発火 |
| `similar-code-check.py` | 類似コードの存在を検出する | 新規コード作成時、類似の既存コードを検索して表示 | PreToolUse:Edit/Writeで自動発火 |
| `similar-pattern-search.py` | 類似パターンを検索する | コード変更時、同様のパターンが他にないか検索 | PreToolUse:Edit/Writeで自動発火 |
| `subprocess_lint_check.py` | subprocess使用時のセキュリティチェックを促す | subprocess呼び出しを含むコード編集時、インジェクション対策を促す警告を表示 | PreToolUse:Edit/Writeで自動発火 |

### PreToolUse:Readフック（1個）

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|-----------|------------------|-------------------|--------|
| `doc-reference-warning.py` | 古いドキュメントを参照している可能性がある場合に警告する | ドキュメント参照時、最終更新日を確認し古い場合は警告 | PreToolUse:Readで自動発火 |

### PostToolUse:Bashフック（30個）

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|-----------|------------------|-------------------|--------|
| `api-operation-logger.py` | API操作の実行時間やエラー率を分析し、ワークフローのボトルネックや障害パターンを特定する | gh/git/npmコマンドの実行時間、終了コード、レート制限エラーを記録 | PostToolUse:Bashで自動発火。api-operation-timerと連携 |
| `bash-failure-tracker.py` | worktree自己削除等でシェルが破損状態になると全コマンドが失敗し続けるため、早期検知して回復策を提示する | Bash失敗を連続カウントし、シェル破損パターン検出時に引き継ぎプロンプト生成を提案 | PostToolUse:Bashで自動発火。3回連続失敗で警告 |
| `ci-recovery-tracker.py` | CI失敗からの回復パターンを記録し、将来の問題解決に活用する | CI失敗後の修正コミット、再実行パターンを記録。失敗理由と回復方法を紐付け | PostToolUse:Bashで自動発火 |
| `ci-wait-check.py` | CI待ち中に別の作業を開始すると混乱するため、CI待ち状態を明示する | gh pr checksコマンド実行後、CI待ち状態を検出して差し込みタスクを提案 | PostToolUse:Bashで自動発火 |
| `codex-review-logger.py` | Codexレビューの実行パターンを記録し、レビュー品質向上に活用する | Codexレビューコマンドの実行をログに記録。PR番号、実行時刻、結果を保存 | PostToolUse:Bashで自動発火 |
| `codex-review-output-logger.py` | Codexレビュー出力を保存し、後からの分析や振り返りに活用する | Codexレビュー結果のstdout/stderrを専用ログファイルに保存 | PostToolUse:Bashで自動発火 |
| `commit-marker-update.py` | コミット作成後にセッションマーカーを更新し、セッション間の作業追跡を可能にする | git commitコマンド成功後、worktreeのセッションマーカーファイルを更新 | PostToolUse:Bashで自動発火 |
| `copilot-review-retry-suggestion.py` | Copilotレビューが失敗した場合、リトライを提案する | gh pr checksでCopilotレビュー失敗を検出し、リトライコマンドを提案 | PostToolUse:Bashで自動発火 |
| `false-positive-detector.py` | フックの誤検知パターンを検出し、フック品質向上に活用する | ブロック後すぐにSKIP環境変数でバイパスされるパターンを検出 | PostToolUse:Bashで自動発火 |
| `git-operations-tracker.py` | git操作のパターンを記録し、ワークフロー分析に活用する | git commit、push、merge等のコマンドを記録 | PostToolUse:Bashで自動発火 |
| `issue-ai-review.py` | IssueへのAIレビューを提案する | 新規Issue作成後、AIレビューの実行を提案 | PostToolUse:Bashで自動発火 |
| `issue-auto-assign.py` | Issue作成時に自動アサインを行う | gh issue createコマンド実行後、作成者を自動アサイン | PostToolUse:Bashで自動発火 |
| `issue-creation-tracker.py` | Issue作成パターンを記録し、分析に活用する | gh issue createコマンドの実行を記録 | PostToolUse:Bashで自動発火 |
| `post-merge-flow-completion.py` | マージ後のフロー完了処理を実行する | マージ成功後、worktreeクリーンアップ、振り返り実行を促す | PostToolUse:Bashで自動発火 |
| `post-merge-observation-issue.py` | マージ後の観察Issue作成を促す | マージ成功後、観察Issueの作成を提案 | PostToolUse:Bashで自動発火 |
| `post-merge-reflection-enforcer.py` | マージ後の振り返り実行を強制する | マージ成功後、/reflect実行を促す。未実行の場合はセッション終了をブロック | PostToolUse:Bashで自動発火 |
| `pr_metrics_collector.py` | PRメトリクスを収集し、分析に活用する | PR作成からマージまでの時間、レビュー回数等を記録 | PostToolUse:Bashで自動発火 |
| `pr-merge-pull-reminder.py` | マージ後のpull実行を促す | マージ成功後、mainブランチのpull実行を促す警告を表示 | PostToolUse:Bashで自動発火 |
| `resolve-thread-guard.py` | PRコメントスレッドの解決を管理する | gh pr comment --resolveコマンドの使用を追跡 | PostToolUse:Bashで自動発火 |
| `worktree-auto-cleanup.py` | マージ済みworktreeを自動クリーンアップする | マージ済みPRのworktreeを検出して自動削除 | PostToolUse:Bashで自動発火 |
| `worktree-creation-marker.py` | worktree作成時にセッションマーカーを設置する | git worktree addコマンド成功後、セッションマーカーファイルを作成 | PostToolUse:Bashで自動発火 |

### PostToolUse:Edit/Writeフック（10個）

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|-----------|------------------|-------------------|--------|
| `dogfooding-reminder.py` | 新機能実装時に自分で使ってみることを促す | 新規スクリプト作成後、実データでのテスト実行を提案 | PostToolUse:Writeで自動発火 |
| `python-lint-check.py` | Pythonファイル編集時にlintチェックを促す | .pyファイル編集後、uvx ruff checkの実行を促す警告を表示 | PostToolUse:Edit/Writeで自動発火 |
| `rework-tracker.py` | 同一ファイルへの複数編集（リワーク）を検出する | 同一ファイルへの編集回数を記録し、閾値超過で警告 | PostToolUse:Edit/Writeで自動発火 |
| `script-test-reminder.py` | スクリプト作成後のテスト実行を促す | 新規スクリプト作成後、テスト実行を促す警告を表示 | PostToolUse:Writeで自動発火 |
| `secret-deploy-trigger.py` | シークレット設定変更を追跡する | .env、wrangler.toml等のシークレット関連ファイル変更を記録 | PostToolUse:Edit/Writeで自動発火 |
| `ui-check-reminder.py` | UI変更後の確認を促す | フロントエンドファイル変更後、ブラウザでの確認を促す警告を表示 | PostToolUse:Edit/Writeで自動発火 |

### PostToolUse:Skillフック（8個）

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|-----------|------------------|-------------------|--------|
| `reflection-log-collector.py` | 振り返りログを収集し、分析に活用する | /reflect実行時のログを専用ファイルに保存 | PostToolUse:Skillで自動発火 |
| `reflection-progress-tracker.py` | 振り返りの進捗を追跡する | 五省の各項目の回答状況を記録 | PostToolUse:Skillで自動発火 |
| `reflection-quality-check.py` | 振り返りの品質をチェックする | 教訓が具体的か、アクショナブルかを検証 | PostToolUse:Skillで自動発火 |
| `reflection-self-check.py` | 振り返りの自己チェックを促す | 振り返り完了後、内容の見直しを促す警告を表示 | PostToolUse:Skillで自動発火 |
| `skill-failure-detector.py` | スキル実行失敗を検出する | スキル実行結果を監視し、失敗パターンを記録 | PostToolUse:Skillで自動発火 |
| `systematization-check.py` | 教訓の仕組み化（フック/CI等の強制機構）をチェックする | 教訓発見時、仕組み化されているかを確認して警告 | PostToolUse:Skillで自動発火 |

### Stopフック（20個）

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|-----------|------------------|-------------------|--------|
| `askuser-suggestion.py` | テキストでの選択肢列挙はユーザー入力負担が大きく選択ミスのリスクがあるため、AskUserQuestion使用を提案する | トランスクリプトから選択肢パターン（A案/B案等）を検出し、AskUserQuestion使用回数と比較して警告 | Stop時に自動発火。警告のみ |
| `block-response-tracker.py` | AGENTS.mdでは「ブロックは代替アクションを実行せよ」と定めており、ツール呼び出しがない場合はエージェントループ停止を検知する | セッション終了時にブロックパターンを分析し、ブロック後にツール呼び出しがないケースを検出して警告 | Stop時に自動発火。分析のみでブロックしない |
| `hook-behavior-evaluator.py` | フックの動作パターンを評価し、改善点を提案する | フックのブロック率、誤検知率を分析して改善提案 | セッション終了時に自動発火 |
| `hook-effectiveness-evaluator.py` | フックの効果を評価し、改善点を特定する | ブロック後の行動パターン、回復率を分析して効果を評価 | セッション終了時に自動発火 |
| `immediate-action-check.py` | [IMMEDIATE]タグの指示が実行されていないかチェックする | セッション内で[IMMEDIATE]タグ付き指示の実行状況を確認 | Stop時に自動発火 |
| `lesson-issue-check.py` | /reflectで抽出した教訓がIssue化されているかチェックする | [lesson]タグ付きの教訓を検出し、Issue参照がなければブロック | Stop時に自動発火 |
| `log_health_check.py` | セッション終了時にログファイルの健全性を確認する | ログファイルの権限、サイズ、フレッシュネスをチェックし、問題があれば警告 | Stop時に自動発火 |
| `observation-auto-check.py` | 観察記録の有無を自動チェックする | セッション中の観察記録をチェックし、不足があれば警告 | Stop時に自動発火 |
| `reflection-completion-check.py` | 振り返りが完了しているかチェックする | /reflect実行後、教訓のIssue化が完了しているか確認 | Stop時に自動発火 |
| `reflection-reminder.py` | 定期的に振り返りを促す | 一定時間経過後、振り返りの実行を促す警告を表示 | 定期的に自動発火 |
| `secret-deploy-check.py` | シークレット設定変更後のデプロイ確認を促す | VITE_*環境変数の変更後、デプロイ手順を表示 | Stop時に自動発火 |
| `session-end-main-check.py` | セッション終了時にmainブランチにいないことを確認する | セッション終了時、cwdがmainブランチの場合に警告 | Stop時に自動発火 |
| `session-end-worktree-cleanup.py` | セッション終了時のworktreeクリーンアップを促す | セッション終了時、マージ済みworktreeの削除を促す警告を表示 | Stop時に自動発火 |
| `session-handoff-writer.py` | 引き継ぎプロンプトを書き出す | セッション終了時、次セッションへの引き継ぎ情報を保存 | Stop時に自動発火 |
| `session-outcome-collector.py` | セッション結果を収集する | セッション終了時の成果（マージ数、Issue解決数等）を記録 | Stop時に自動発火 |
| `session-todo-check.py` | セッション終了時のTODO完了状況をチェックする | TodoWriteで登録されたタスクの完了状況を確認 | Stop時に自動発火 |
| `stop-auto-review.py` | セッション終了時の自動レビューを実行する | セッション終了時、成果物の自動レビューを実行 | Stop時に自動発火 |

### その他のフック（フロー管理、メトリクス収集等）

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|-----------|------------------|-------------------|--------|
| `api-operation-timer.py` | APIコマンドの実行時間を計測するため、開始時刻を記録する | gh/git/npmコマンド検出時に開始時刻を一時ファイルに記録 | PreToolUse:Bashで自動発火 |
| `block-improvement-reminder.py` | 同じフックが3回以上連続ブロックする場合、フック自体に改善の余地がある可能性が高いため、改善を提案する | セッション内の連続ブロックをフック別にカウントし、閾値超過で改善リマインダーを表示 | PreToolUse時に自動発火 |
| `defer-keyword-check.py` | 「後で」「将来的に」等の先送り表現を検出し、Issue作成を促す | Claudeの応答から先送りキーワードを検出し、Issue作成なしの場合に警告 | PostToolUse時に自動発火 |
| `development-workflow-tracker.py` | 開発ワークフローの進捗を追跡し、フロー完遂を支援する | Issue着手からマージまでのフェーズを記録し、未完了フェーズを警告 | 各フェーズ完了時に自動発火 |
| `exploration-tracker.py` | 調査・探索作業の進捗を追跡し、調査完了を支援する | grep、read等の調査コマンドを検出し、調査セッションを記録 | PostToolUse時に自動発火 |
| `flow-effect-verifier.py` | フロー遷移の効果を検証し、フロー設計の改善に活用する | フェーズ遷移後の実際の行動パターンを記録・分析 | 各フェーズ完了時に自動発火 |
| `flow-progress-tracker.py` | 開発フローの進捗を追跡し、停滞を検出する | 各フェーズの滞在時間を記録し、閾値超過で警告 | 定期的に自動発火 |
| `flow-state-updater.py` | フロー状態を更新し、状態遷移を管理する | ツール実行結果に基づいてフロー状態ファイルを更新 | PostToolUse時に自動発火 |
| `flow-verifier.py` | フロー状態の整合性を検証し、不整合を検出する | 現在のフロー状態と実際の状況を比較し、不整合があれば警告 | PreToolUse時に自動発火 |
| `followup-issue-guard.py` | 「フォローアップ」「後で対応」等の発言時にIssue作成を強制する | Claudeの応答からフォローアップキーワードを検出し、Issue参照がなければブロック | PostToolUse時に自動発火 |
| `issue_checker.py` | PRにリンクされたIssueの状態をチェックする | Closes #NNNで参照されたIssueの存在確認、ステータスチェック | merge_conditions.pyから呼び出される |
| `issue-investigation-tracker.py` | Issue調査の進捗を追跡する | Issue関連のgrep、read等の調査コマンドを記録 | PostToolUse時に自動発火 |
| `fix_verification_checker.py` | bugラベル付きPRの修正が実際に検証されているかチェックする | PRの変更内容とテスト追加の有無を確認し、未検証時はブロック | merge_conditions.pyから呼び出される |
| `observation-reminder.py` | 定期的に観察記録を促す | 一定時間経過後、観察の記録を促す警告を表示 | 定期的に自動発火 |
| `plan-file-updater.py` | 計画ファイルを更新し、進捗を記録する | タスク完了時に計画ファイルのステータスを更新 | PostToolUse時に自動発火 |
| `research-tracker.py` | リサーチ作業の進捗を追跡する | WebSearch、WebFetch等のリサーチコマンドを記録 | PostToolUse時に自動発火 |
| `review_checker.py` | PRレビュー状態をチェックする | gh pr viewでレビュー承認状況を確認 | merge_conditions.pyから呼び出される |
| `review-promise-tracker.py` | レビュー対応の約束を追跡する | 「後で修正します」等の約束を検出して記録 | PostToolUse時に自動発火 |
| `session_metrics_collector.py` | セッションメトリクスを収集する | ツール呼び出し数、実行時間、エラー率等を記録 | 各ツール実行時に自動発火 |
| `session-issue-integrity-check.py` | セッションとIssueの整合性をチェックする | セッション中のIssue操作の整合性を確認 | 定期的に自動発火 |
| `session-log-compressor.py` | 古いセッションログを圧縮する | 一定期間経過したログファイルをgzip圧縮 | 定期的に自動発火 |
| `session-marker-refresh.py` | セッションマーカーを更新する | セッション中定期的にマーカーファイルのタイムスタンプを更新 | 定期的に自動発火 |
| `session-marker-updater.py` | セッションマーカーをツール実行後に更新する | ツール実行成功後、セッションマーカーを更新 | PostToolUse時に自動発火 |
| `skill-usage-reminder.py` | 該当するスキルの使用を促す | 特定の作業パターン検出時、関連スキルの使用を提案 | PreToolUse時に自動発火 |
| `task-start-checklist.py` | タスク開始時のチェックリストを表示する | 新規タスク開始時、確認事項のチェックリストを表示 | PreToolUse時に自動発火 |
| `tool-efficiency-tracker.py` | ツール使用効率を追跡する | ツール呼び出しパターンを記録し、効率を分析 | PostToolUse時に自動発火 |
| `vague-action-block.py` | 曖昧な行動宣言をブロックする | 「心がける」「注意する」等の曖昧表現を検出してブロック | PostToolUse時に自動発火 |
| `workflow_verifier.py` | ワークフローの整合性を検証する | 開発フローの各ステップが正しく実行されているか確認 | 定期的に自動発火 |
| `workflow-skill-reminder.py` | ワークフロースキルの使用を促す | PR作成、マージ等の作業時、関連スキルの使用を提案 | PreToolUse時に自動発火 |

## テストファイル（227個）

| ディレクトリ | ファイル数 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|-------------|-----------|------------------|-------------------|--------|
| `tests/` | 227 | 各フックの動作を検証し、リファクタリングや機能追加時のデグレードを防止するため | conftest.pyで共通フィクスチャ（tmp_path、mock_subprocess等）を提供し、各test_*.pyでフックのユニットテストを実行。run_tests.pyでテスト一括実行 | `cd .claude/hooks && python -m pytest tests/` または `uvx pytest tests/` |
