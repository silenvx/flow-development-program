# .claude/ その他のファイル

Skills、Prompts、Docs、Commands、Settings、Dashboardモジュール。

## .claude/scripts/dashboard/ - 3ファイル

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|------------|------------------|-------------------|--------|
| `__init__.py` | 開発フローログの可視化（Issue #1367）のため、dashboardパッケージとして機能を提供し、他モジュールからインポート可能にするために必要 | DashboardDataCollectorとgenerate_dashboard_htmlをエクスポートし、パッケージの公開インターフェースを定義する | `from dashboard import DashboardDataCollector, generate_dashboard_html` |
| `data_collector.py` | 各種ログファイル（API操作、フック実行、手戻りメトリクス、フロー状態）からダッシュボード表示用のデータを収集・集計するために必要 | API成功率トレンド、ブロック率トレンド、手戻りイベント、フェーズ滞在時間、CI失敗情報を日別に集計してKPIとして提供する | `collector = DashboardDataCollector(); kpis = collector.get_summary_kpis(days=7)` |
| `html_generator.py` | 収集したダッシュボードデータを人間が閲覧できる形式に変換し、Chart.jsを使用したグラフで視覚化するために必要 | KPI、API成功率、ブロック率、手戻りイベント、フェーズ滞在時間、CI失敗リストを含む静的HTMLダッシュボードを生成する | `html = generate_dashboard_html(kpis, api_trend, block_trend, rework_trend, phase_durations, ci_failures)` |

## .claude/skills/ - 8ファイル

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|------------|------------------|-------------------|--------|
| `add-perspective/SKILL.md` | ユーザーからの指摘や問題発見時に、類似問題を将来の振り返りで検出できるよう観点を追加する手順を標準化するために必要 | 問題分析、既存観点確認、新観点定義、reflection-self-check.pyへの追加、テスト追加の手順をガイドする | `[ACTION_REQUIRED: /add-perspective]` 表示時、またはユーザーから「動いてる？」の指摘を受けた時に参照 |
| `claude-code-features/SKILL.md` | 新機能調査・提案時に、プロジェクトの設計方針（自動実行優先）に適合する機能を選択するためのチェックリストを提供するために必要 | Skills、Hooks、Slash Commands、Subagentsの使い分けガイド、機能選択フローチャート、実装前確認チェックリストを提供する | 新機能提案時に参照し、既存機能で代替できないか、責務重複がないかを確認する |
| `code-review/SKILL.md` | GitHub Copilot/Codex Cloud/Codex CLIのレビュー対応手順を標準化し、レビューコメントへの適切な対応を保証するために必要 | レビュー確認方法、範囲内/範囲外の判断基準、対応フロー、False Positive対応、Verified形式、署名ルール、既知の誤検知パターンを網羅的に提供する | レビューコメント確認時、Resolve時、マージ前チェック時に参照。`gh api /repos/{owner}/{repo}/pulls/{PR}/comments` でコメント確認 |
| `coding-standards/SKILL.md` | コーディング規約・品質基準を統一し、チーム全体で一貫したコード品質を維持するために必要 | コメント規約（Why重視）、コマンド一覧、共通パターン修正時の網羅チェック、TDD、Pythonフックベストプラクティス、Sentry使用ガイドラインを提供する | コード実装前・レビュー前に参照。`pnpm lint && pnpm typecheck && pnpm test:ci` でローカル検証 |
| `development-workflow/SKILL.md` | Git Worktreeを使用した開発フローの詳細手順を標準化し、オリジナルは常にmainを維持しながら全ての作業をworktreeで行うワークフローを確立するために必要 | worktree作成、依存インストール、タスク要件確認、コミットメッセージ規約、PRボディ必須項目、CI監視、マージ手順、Dependabot PR対応、Sub-Issue管理を網羅的に提供する | worktree作成時 `git worktree add --lock`、PR作成時 `gh pr create`、CI監視時 `python3 .claude/scripts/ci-monitor.py {PR}` |
| `hooks-reference/SKILL.md` | Claude Codeフックの詳細仕様と設計原則を提供し、フック実装時の品質を保証するために必要 | フック出力フォーマット、共通ライブラリ関数詳細、HookContextへの移行、PreToolUse/PostToolUse/Stopフック一覧、実装チェックリスト、パターン検出ガイドライン、テンプレートを提供する | フック実装・修正時に参照。`make_block_result(hook_name, reason)` でブロック、`parse_hook_input()` で入力処理 |
| `reflection/SKILL.md` | 五省による自己評価、なぜなぜ分析、反省点の仕組み化を行うための振り返りガイドを提供するために必要 | 振り返りガイド（guide.md）を参照し、タスク完了後の自己評価と改善サイクルを実行する | 振り返り実行時に参照。`/reflect` コマンドで実行 |
| `troubleshooting/SKILL.md` | エラー、失敗、問題発生時のトラブルシューティング手順を提供し、Worktree操作不能、フックエラー、CI失敗、デプロイ問題の解決策を示すために必要 | TROUBLESHOOTING.mdを参照し、問題発生時の解決策を提供する | エラー、失敗、動かない、問題、トラブル発生時に参照 |

## .claude/prompts/reflection/ - 3ファイル

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|------------|------------------|-------------------|--------|
| `execute.md` | 振り返りの具体的な実行手順を標準化し、形式的な振り返りを防ぐために必須チェックや詳細な記述を要求するために必要 | 必須チェック（前提バイアス回避、調査網羅性、3回自問）、セッション行動振り返り、ログ調査、PR/Issue実装評価、五省、自律完遂度評価、改善点洗い出し、Issue作成、観点チェックを段階的に実行する | `/reflect` コマンド実行時に自動参照。セクション毎に具体的な記述を記入 |
| `guide.md` | 振り返りの基本原則、五省の各項目、反省点対策、なぜなぜ分析手法、アンチパターンを提供し、正しい振り返りの姿勢を教育するために必要 | 振り返り前の情報収集手順、五省（AIエージェント版）、反省点の対策（仕組み化優先）、なぜなぜ分析テンプレート、振り返りアンチパターン、正しい振り返りの姿勢を解説する | execute.mdから参照される。振り返りの質を向上させるための教育資料 |
| `trends.md` | 単一セッションでは見えない問題を検出するための複数粒度分析（3日間の傾向）を行い、システムレベルの健全性を評価するために必要 | fix/feat比率、NOT_PLANNED Issue、頻出ブロックパターンを分析し、ガードレール回避サイクル、fix連鎖、Issue乱発等のパターンを検出して改善策をIssue化する | `/reflect-trends` コマンド実行時に自動参照。`git log --since="3 days ago"` で傾向分析 |

## .claude/docs/ - 2ファイル

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|------------|------------------|-------------------|--------|
| `design-review-prompt.txt` | Codex CLIによる設計品質レビュー（結合度・凝集度・SRP）を実行する際のプロンプトを標準化し、一貫した品質基準でレビューを行うために必要 | 優先度レベル（P0/P1/P2）定義、設計品質チェック（結合度、凝集度、単一責任原則、変更影響範囲）、セキュリティチェック、テストカバレッジ、プロジェクト固有ルール、出力形式を定義する | `.claude/scripts/codex-design-review.sh` から参照。`codex review --base main --instructions "$(cat .claude/docs/design-review-prompt.txt)"` |
| `lint-rule-checklist.md` | 新しいlintルール追加時の確認事項を標準化し、誤検知パターンの事前検討やテストカバレッジの確保を保証するために必要 | 実装前チェック（検出パターン明確化、誤検知パターン列挙、ドライラン）、テスト項目（検出ケース、誤検知しないケース、エッジケース）、実装時注意事項、レビュー前チェックを提供する | 新Lintルール追加時に参照。`python3 .claude/scripts/hook_lint.py --check-only` でドライラン |

## .claude/commands/ - 2ファイル

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|------------|------------------|-------------------|--------|
| `reflect.md` | セッションの行動を振り返り、五省で自己評価し、改善点をIssue化するためのSlash Commandを提供するために必要 | execute.mdを参照して振り返りを実行し、ログ分析やなぜなぜ分析も実行可能。ultrathinkモードで深い思考を促す | `/reflect` と入力して実行 |
| `reflect-trends.md` | 直近3日間の傾向分析（fix比率、NOT_PLANNED Issue、ブロックパターン）を実行するためのSlash Commandを提供するために必要 | trends.mdを参照して傾向分析を実行。ultrathinkモードで深い思考を促す | `/reflect-trends` と入力して実行 |

## .claude/settings.json - 1ファイル

| ファイル名 | Why（なぜ必要か） | What（何をするか） | 使い方 |
|------------|------------------|-------------------|--------|
| `settings.json` | Claude Codeのプロジェクト固有設定を定義し、ステータスライン、パーミッション、フック設定を一元管理するために必要 | statusLine（コマンドベース）、permissions（git commit --no-verify等の禁止）、hooks（SessionStart 19個、UserPromptSubmit 2個、PreToolUse 52個以上、PostToolUse 34個以上、Stop 34個以上）を設定する | プロジェクトルートに配置することで自動適用。フック追加時は該当イベントの配列に追加 |
