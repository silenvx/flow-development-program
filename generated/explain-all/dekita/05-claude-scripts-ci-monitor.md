# .claude/scripts/ci_monitor モジュール

CI監視の中核モジュール。PRの状態監視、自動リベース、レビュー対応を提供。

## 実装ファイル一覧（14個）

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|------------|------------------|-------------------|--------|
| `__init__.py` | ci_monitorパッケージのエントリポイントとして、全モジュールとその公開APIを一元管理し、テスト互換性のためにサブモジュールへのアクセスを提供する必要があるため | ci_monitorパッケージの初期化を行い、全サブモジュール（models, constants, github_api, state, rate_limit, events, worktree, ai_review, review_comments, pr_operations, monitor, session, main_loop）からの関数・クラスをインポートして再エクスポートする | `from ci_monitor import check_once, monitor_pr` のように直接インポート |
| `constants.py` | PRモニタリングに必要な設定値（タイムアウト、リトライ回数、レート制限閾値）を一元管理し、コード全体で一貫した動作を保証するため | ポーリング間隔、最大リベース回数、Copilotリトライ上限、レート制限の警告/クリティカル閾値、コードブロック除去用正規表現、AIレビュワー識別子リストを定義する | `from ci_monitor.constants import DEFAULT_POLLING_INTERVAL` |
| `models.py` | CI監視で使用するデータ構造（イベントタイプ、PR状態、監視結果）を型安全に定義し、コード全体でのデータ一貫性を確保するため | EventType/CheckStatus/MergeState等のEnum、PRState/MonitorEvent/MonitorResult/RebaseResult等のdataclass、has_unresolved_threadsヘルパー関数を提供する | `from ci_monitor.models import PRState, CheckStatus, MonitorResult` |
| `state.py` | バックグラウンド実行時に監視状態をファイルに永続化し、別プロセス/セッションから状態確認やリカバリを可能にするため | PR番号ごとの状態ファイル（.claude/state/ci-monitor-{pr}.json）のパス取得、アトミック書き込み、読み込み、クリア機能を提供する。worktreeからもメインリポジトリの状態を参照可能 | `save_monitor_state("123", {"status": "monitoring"})`, `load_monitor_state("123")` |
| `events.py` | モニタリング中のイベント（CI成功/失敗、レビュー完了）を構造化して出力し、ログ記録とクライアント通知を統一的に行うため | MonitorEventの作成（タイムスタンプ自動付与）、JSON形式でのstdout出力、コンソールログ出力（プレーン/JSON両対応）、バックグラウンドロガーへの転送機能を提供する | `event = create_event(EventType.CI_PASSED, "123", "CI passed")`, `emit_event(event)` |
| `rate_limit.py` | GitHub GraphQL APIのレート制限を監視し、制限接近時にポーリング間隔調整やREST APIへのプロアクティブなフォールバックを行ってAPI枯渇を防ぐため | レート制限チェック（インメモリ/ファイルキャッシュ対応）、リセット時刻のフォーマット、残量に応じたポーリング間隔調整、REST優先モード切替判定、警告メッセージ出力、イベントログ記録を提供する | `remaining, limit, reset = check_rate_limit()`, `interval = get_adjusted_interval(30, remaining)` |
| `github_api.py` | GitHub CLI（gh）コマンドの実行を抽象化し、タイムアウト処理、エラー診断、レート制限検出、GraphQLからRESTへの自動フォールバックを提供するため | ghコマンドの実行（stdout/stderr両方取得）、レート制限エラー判定（URL除去によるfalse positive防止）、GraphQL実行時のフォールバック機能、リポジトリ情報（owner/name）取得を提供する | `success, output = run_gh_command(["pr", "view", "123"])`, `run_graphql_with_fallback(args, fallback_fn)` |
| `worktree.py` | Git worktreeの検出・管理を行い、PRマージ後のworktreeとブランチの自動クリーンアップを支援するため | 現在のディレクトリがworktree内かを検出（メインリポジトリパスも取得）、マージ後のworktree削除（ロック解除→通常削除→強制削除のフォールバック）、関連ブランチの自動削除（exact match判定）を提供する | `main_repo, wt_path = get_worktree_info()`, `cleanup_worktree_after_merge(wt_path, main_repo)` |
| `review_comments.py` | PRのレビューコメント・スレッドの取得、分類、重複検出、自動解決の包括的なレビュー管理機能を提供するため | レビューコメント取得（REST/GraphQL両対応）、PR変更ファイル一覧取得、スコープ内/外分類、未解決スレッド取得、AIレビュワースレッド抽出、コメント本文正規化、リベース後の重複スレッド自動解決、品質ログ記録を提供する | `comments = get_review_comments("123")`, `classified = classify_review_comments("123")` |
| `ai_review.py` | CopilotやCodex等のAIレビュワーに関する検出・管理機能を提供し、AIレビューの完了判定やエラーリトライを自動化するため | AIレビュワー判定（is_ai_reviewer）、pending確認、Codexレビューリクエスト検出、Copilot/Codexレビュー取得、Copilotエラー検出（最新レビューのみ）、Copilotレビュー再リクエスト、矛盾コメント検出支援を提供する | `if is_ai_reviewer(author): ...`, `is_error, msg = is_copilot_review_error("123")` |
| `pr_operations.py` | PRに対する各種操作（バリデーション、状態取得、リベース、マージ、再作成）を一元管理し、堅牢なPRライフサイクル管理を実現するため | PR番号バリデーション、PR状態取得（マージ状態/レビュワー/CIステータス）、ローカル変更検出、mainブランチ安定待機、リベース実行（コンフリクト検出付き）、squashマージ、ローカル同期、PRリオープン（リトライ付き）、PR再作成を提供する | `state, error = get_pr_state("123")`, `result = rebase_pr("123")`, `merge_pr("123")` |
| `monitor.py` | PRの一回チェック、通知専用モード、複数PR並列監視のコア監視機能を提供し、様々な監視シナリオに対応するため | 単発PR状態チェック（check_once）、アクション可能イベント検出時の通知（monitor_notify_only）、複数PR並列監視（ThreadPoolExecutor使用）、PR自己参照検出、Issueのクローズ参照/受け入れ条件抽出、待機時間活用ヒント表示を提供する | `event = check_once("123", prev_reviewers)`, `monitor_multiple_prs(["123", "456"])` |
| `session.py` | CI監視セッションのIDを管理し、ログやAPI操作記録を正しいセッションに紐付けるため | セッションID（UUID形式）のグローバル設定・取得機能を提供。--session-id引数で起動時に設定し、各種ログ記録時にセッション識別に使用される | `set_session_id("3f03a042-...")`, `session_id = get_session_id()` |
| `main_loop.py` | PRのCIとレビュー完了を待つメイン監視ループを実装し、自動リベース、レート制限対応、Copilotリトライの複雑なロジックを統合するため | monitor_pr関数として、タイムアウト管理、BEHIND/DIRTY検出と自動リベース、ローカル変更待機、main安定待機、AIレビュー完了待機、Copilotエラーリトライ、PR再作成、重複スレッド自動解決、レート制限対応ポーリング、状態永続化を統合したメインループを提供する | `result = monitor_pr("123", timeout_minutes=20, early_exit=False)` |

## テストファイル一覧（12個）

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|------------|------------------|-------------------|--------|
| `tests/__init__.py` | testsディレクトリをPythonパッケージとして認識させ、テストモジュールの適切なインポートを可能にするため | 空のinitファイルとしてパッケージマーカーの役割を果たす | pytest実行時に自動的に認識される |
| `tests/test_models.py` | models.pyで定義されたEnum、dataclass、ヘルパー関数の動作を検証し、データ構造の正確性と一貫性を保証するため | EventType/CheckStatus/MergeState/RateLimitEventType等の全Enum値検証、PRState/MonitorEvent/MonitorResult等のdataclass生成・変換テスト、has_unresolved_threads関数のエッジケーステストを実施する | `pytest tests/test_models.py` |
| `tests/test_constants.py` | constants.pyで定義された設定値と正規表現パターンの妥当性を検証し、設定ミスによる予期せぬ動作を防ぐため | デフォルト値の正整数検証、レート制限閾値の大小関係検証、GITHUB_FILES_LIMITの正整数検証、CODE_BLOCK_PATTERNの各種コードブロック/インラインコードマッチングテストを実施する | `pytest tests/test_constants.py` |
| `tests/test_state.py` | state.pyの状態ファイル管理機能（パス生成、保存、読込、クリア）の正確性と堅牢性を検証するため | パストラバーサル攻撃に対するバリデーション、アトミック書き込みの検証、存在しないファイルの読込、クリア操作の成功/存在しないケースをtmp_pathフィクスチャを使って検証する | `pytest tests/test_state.py` |
| `tests/test_events.py` | events.pyのイベント作成・出力・ログ機能の正確性を検証し、出力形式やエラーハンドリングを保証するため | emit_eventのJSON出力検証、create_eventのタイムスタンプ/詳細/推奨アクション検証、バックグラウンドロガーエラー時の継続性検証、log関数のプレーン/JSONモード出力検証を実施する | `pytest tests/test_events.py` |
| `tests/test_rate_limit.py` | rate_limit.pyのレート制限管理機能（キャッシュ、API呼び出し、間隔調整、REST優先判定）の正確性と堅牢性を検証するため | リセット時刻フォーマット、ファイルキャッシュの読み書き（破損/stale/不正タイムスタンプ対応）、API呼び出しモック、メモリ/ファイルキャッシュ統合、間隔調整計算、REST優先モード切替ログ検証を実施する | `pytest tests/test_rate_limit.py` |
| `tests/test_github_api.py` | github_api.pyのghコマンド実行、エラー検出、フォールバック機能の正確性を検証するため | コマンド成功/失敗/タイムアウト検証、stderr取得検証、URL除去によるレート制限誤検出防止検証、リポジトリ情報取得（成功/失敗/不正JSON/フィールド欠落）、GraphQLフォールバック動作検証を実施する | `pytest tests/test_github_api.py` |
| `tests/test_worktree.py` | worktree.pyのworktree検出・クリーンアップ機能の正確性と堅牢性を検証するため | exact match判定（部分一致/完全一致/区切り文字別）、worktree情報取得（worktree内/外/サブディレクトリ/エラー時）、クリーンアップ（成功/強制削除フォールバック/ブランチ削除/タイムアウト/例外）検証を実施する | `pytest tests/test_worktree.py` |
| `tests/test_ai_review.py` | ai_review.pyのAIレビュワー検出・管理機能の正確性を検証し、誤検出や見逃しを防ぐため | is_ai_reviewer（Copilot/Codex/大文字小文字/人間/空）、Codexリクエスト/レビュー取得、Copilotレビュー取得、エラー検出（最新レビューのみ）、レビュー再リクエスト、矛盾検出呼び出し検証を実施する | `pytest tests/test_ai_review.py` |
| `tests/test_review_comments.py` | review_comments.pyのコメント取得・分類・重複処理・スレッド管理機能の正確性を検証するため | コードブロック除去、コメント取得（成功/失敗/不正JSON）、ファイル一覧取得（成功/失敗/制限到達）、スコープ分類、REST fallback変換、本文正規化、未解決スレッド取得、スレッド解決、重複フィルタリング検証を実施する | `pytest tests/test_review_comments.py` |
| `tests/test_pr_operations.py` | pr_operations.pyのPR操作機能（バリデーション、状態取得、リベース、マージ）の正確性と堅牢性を検証するため | PR番号バリデーション（正常/非整数/ゼロ/負数/上限超過）、状態取得（成功/失敗/不明状態/CI失敗・キャンセル）、ローカル変更検出（untracked除外）、リベース（成功/コンフリクト）、マージ、PR再作成、リオープンリトライ検証を実施する | `pytest tests/test_pr_operations.py` |
| `tests/test_monitor.py` | monitor.pyのコア監視機能（check_once, monitor_notify_only, ヘルパー関数）の正確性を検証するため | ログサニタイズ（制御文字除去）、イベントログ記録、Closes/Fixes参照抽出、受け入れ条件抽出、待機時間提案、check_once各種イベント検出（エラー/BEHIND/DIRTY/CI成功・失敗/レビュー完了）、notify_only出力検証、自己参照検出を実施する | `pytest tests/test_monitor.py` |
