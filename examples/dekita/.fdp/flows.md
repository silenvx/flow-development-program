# dekita システムフロー図

開発フローを可視化。フック詳細は[README.md](README.md)を参照。

---

## 1. 開発ワークフロー全体像

セッション開始からマージまでの完全サイクル。

```mermaid
flowchart TD
    開始([セッション開始]) --> date[date_context_injector<br/>現在日時とSession IDを表示<br/>フォーマット: YYYY-MM-DD HH:MM:SS JST]
    date --> env_check[environment_integrity_check<br/>settings.jsonとindex.jsonの同期確認<br/>不整合時は自動修正オプションを提案]
    env_check --> env{環境OK?}
    env -->|No| fix[環境修正]
    fix --> env_check
    env -->|Yes| git_check[git_config_check<br/>user.name/user.email設定確認<br/>未設定時は警告表示]
    git_check --> handoff[session_handoff_reader<br/>.claude/handoff.mdを読み込み表示<br/>前回セッションの継続文脈を提供]

    handoff --> wt_status[session_worktree_status<br/>全worktreeのロック/ブランチ状態一覧<br/>作業中worktreeを可視化]
    wt_status --> pr_warn[open_pr_warning<br/>オープンPR一覧と担当ブランチ表示<br/>介入禁止PRを警告]
    pr_warn --> resume_warn[session_resume_warning<br/>別セッションが作業中のworktreeを検出<br/>競合リスクを警告]
    resume_warn --> wt_check{worktree必要?}
    wt_check -->|Yes| wt_create[worktree作成]
    wt_check -->|No| wt_move[既存worktreeへ移動]
    wt_create --> wt_guard[worktree_path_guard<br/>.worktrees/配下以外へのパスを検出<br/>違反時はexit 2でブロック]
    wt_guard --> impl[実装開始]
    wt_move --> impl

    impl --> edit[Edit/Write]
    edit --> worktree_warn[worktree_warning<br/>mainブランチでの編集を検出<br/>worktree作成を促すメッセージを表示]
    worktree_warn --> similar[similar_code_check<br/>変更内容と類似するコードをrg検索<br/>重複実装を防ぐ]
    similar --> commit[コミット作成]

    commit --> commit_why[commit_message_why_check<br/>コミットメッセージに「なぜ」が必須<br/>欠落時はexit 2でブロック]
    commit_why -->|block| impl
    commit_why -->|pass| push[git push]

    push --> codex_review[codex_review_check<br/>codex review --base mainの実行を確認<br/>未実行時はexit 2でブロック]
    codex_review -->|未実行| impl
    codex_review -->|実行済み| pr_create[PR作成]

    pr_create --> pr_body[pr_body_quality_check<br/>PRボディに「なぜ」と参照#番号が必須<br/>欠落時はexit 2でブロック]
    pr_body --> ci[CI監視開始]
    ci --> ci_monitor[ci_monitor.py<br/>CI状態/レビュー状態を定期ポーリング<br/>BEHIND時は自動リベース実行]
    ci_monitor --> ci_result{CI結果}
    ci_result -->|失敗| ci_fix[CI失敗修正]
    ci_fix --> impl
    ci_result -->|成功| review[レビュー待機]

    review --> review_check{レビュー完了?}
    review_check -->|コメントあり| review_respond[レビュー対応]
    review_respond --> resolve[resolve_thread_guard<br/>Resolveにはコメント返信が必須<br/>返信なしResolveをexit 2でブロック]
    resolve --> review
    review_check -->|承認| merge_check[merge_check.py<br/>AIレビュー完了/スレッド解決/検証コメント確認<br/>条件未達時はexit 2でブロック]

    merge_check -->|block| review
    merge_check -->|pass| merge[マージ実行]

    merge --> cleanup[worktree_auto_cleanup<br/>マージ成功後にworktreeを自動削除<br/>mainに戻ってgit pullを促す]
    cleanup --> metrics[session_metrics_collector<br/>ツール使用回数/ブロック回数を記録<br/>セッション効率分析用データ]
    metrics --> handoff_write[session_handoff_writer<br/>作業内容を.claude/handoff.mdに保存<br/>次回セッションへの引き継ぎ]
    handoff_write --> reflect_check[reflection_completion_check<br/>/reflect実行済みかtranscriptで確認<br/>未実行時はexit 2でブロック]
    reflect_check -->|未実行| do_reflect["reflect実行"]
    do_reflect --> 終了([セッション終了])
    reflect_check -->|実行済み| 終了
```

---

## 2. Issue作成フロー

Issue作成時の品質チェックと自動処理。

```mermaid
flowchart TD
    開始([gh issue create]) --> body[issue_body_requirements_check<br/>なぜ/現状/期待動作/対応案の4項目を検証<br/>欠落時はexit 2でブロック]

    body -->|項目欠落| block_body[ブロック: 必須項目不足]
    block_body --> 終了_fail([作成失敗])
    body -->|OK| priority[issue_priority_label_check<br/>P0/P1/P2/P3いずれかのラベルを検証<br/>未設定時はexit 2でブロック]

    priority -->|P0-P3なし| block_priority[ブロック: 優先度ラベル必須]
    block_priority --> 終了_fail
    priority -->|OK| dup[duplicate_issue_check<br/>タイトルと本文で類似Issueを検索<br/>類似発見時は警告表示して継続]

    dup -->|類似あり| warn_dup[警告: 重複の可能性<br/>類似Issue番号を表示]
    warn_dup --> multi[issue_multi_problem_check<br/>タイトルに複数問題パターンを検出<br/>複数問題検出時はexit 2でブロック]
    dup -->|なし| multi

    multi -->|複数問題| block_scope[ブロック: 1Issue1問題に分割必要]
    block_scope --> 終了_fail
    multi -->|OK| label[issue_label_check<br/>P0-P3以外のラベルが1つ以上あるか検証<br/>ラベルなしはexit 2でブロック]

    label -->|ラベルなし| block_label[ブロック: 分類ラベル必須]
    block_label --> 終了_fail
    label -->|OK| create[Issue作成実行]

    create --> assign[issue_auto_assign<br/>作成者をassigneeに自動設定<br/>gh issue edit --add-assignee実行]
    assign --> track[issue_creation_tracker<br/>Issue番号/タイトルをセッションログに記録<br/>api-operations-SESSION.jsonlに出力]
    track --> ai_review[issue_ai_review<br/>GeminiにIssue内容のレビューを依頼<br/>不明瞭な点や改善提案をコメント]
    ai_review --> 終了_ok([作成成功])
```

---

## 3. CI監視フロー

PR作成後の継続的監視と自動対応。

```mermaid
flowchart TD
    開始([ci_monitor.py開始]) --> status[gh pr viewでCI状態取得<br/>mergeStateStatus/mergeable/checksを確認<br/>30秒間隔でポーリング]

    status --> state{mergeStateStatus判定<br/>BEHIND/DIRTY/UNKNOWN/CLEAN<br/>PRのmain追従状態}
    state -->|BEHIND| rebase[自動リベース実行<br/>git fetch origin main<br/>git rebase origin/main && git push -f]
    rebase --> rebase_count{リベース回数カウント<br/>--max-rebaseで上限指定<br/>デフォルト3回}
    rebase_count -->|3回未満| wait_ci[CI再待機<br/>リベース後のCIを監視<br/>30秒間隔でポーリング再開]
    rebase_count -->|3回以上| wait_stable[main安定待機<br/>他セッションのマージ完了を待つ<br/>60秒間隔で再チェック]
    wait_stable --> rebase
    wait_ci --> status

    state -->|DIRTY| 終了_conflict([コンフリクト発生<br/>手動でのコンフリクト解決が必要<br/>エラー終了])
    state -->|UNKNOWN| wait_unknown[状態確定待機<br/>GitHub側の計算完了を待つ<br/>10秒後に再チェック]
    wait_unknown --> status

    state -->|BLOCKED/CLEAN| ci_check{CI結果判定<br/>checksのconclusionを確認<br/>success/failure/pending}
    ci_check -->|pending| wait_ci2[CI完了待機<br/>GitHub Actionsの完了を待つ<br/>30秒間隔でポーリング]
    wait_ci2 --> ci_check
    ci_check -->|failure| 終了_fail([CI失敗検出<br/>失敗したジョブを表示<br/>エラー終了で修正を促す])
    ci_check -->|success| review[レビュー状態確認<br/>requested_reviewersを取得<br/>AIレビュアーの存在を確認]

    review --> ai_reviewer{AIレビュアー状態<br/>Copilot/Codexがリストにいるか<br/>いれば進行中と判定}
    ai_reviewer -->|進行中| wait_ai[AIレビュー完了待機<br/>レビュアーリストから消えるまで待機<br/>30秒間隔でポーリング]
    wait_ai --> ai_reviewer
    ai_reviewer -->|コメントあり| show_comments[レビューコメント表示<br/>GraphQL reviewThreadsを取得<br/>未解決スレッドを一覧表示]
    show_comments --> 終了_review([レビュー対応必要<br/>コメントに対応してResolve<br/>--early-exitで即終了])
    ai_reviewer -->|完了| human_review{人間レビュー確認<br/>reviewDecisionをチェック<br/>APPROVED/CHANGES_REQUESTED}
    human_review -->|承認待ち| wait_human[人間承認待機<br/>APPROVED状態を待つ<br/>30秒間隔でポーリング]
    wait_human --> human_review
    human_review -->|承認済み| 終了_ok([マージ可能<br/>全条件クリア<br/>gh pr merge実行可能])
```

---

## 4. セッション管理フロー

セッションのライフサイクル管理と状態注入。

```mermaid
flowchart TD
    開始([Claude Code起動]) --> date[date_context_injector<br/>YYYY-MM-DD HH:MM:SS JST形式で現在日時を表示<br/>Session IDをUUID形式で出力]
    date --> session_id[Session ID取得<br/>Claude CodeのセッションIDを取得<br/>ログのセッション紐付けに使用]
    session_id --> env_check[environment_integrity_check<br/>settings.jsonのフック定義とindex.jsonを比較<br/>不整合があれば修正オプションを提案]

    env_check --> env_ok{環境整合性判定<br/>全フックが正しく登録されているか<br/>不整合があれば再同期}
    env_ok -->|No| env_fix[環境修正提案<br/>sync-hooks.pyの実行を提案<br/>自動修正オプションを表示]
    env_fix --> env_check
    env_ok -->|Yes| git_check[git_config_check<br/>git config user.name/user.emailを確認<br/>未設定の場合は警告表示]

    git_check --> handoff_read[session_handoff_reader<br/>.claude/handoff.mdの内容を表示<br/>前回セッションの作業内容と引き継ぎ事項]
    handoff_read --> flow_state[flow_state_updater<br/>現在のフェーズを追跡<br/>implementation/review/mergeなど]
    flow_state --> context[コンテキスト表示<br/>セッション開始情報をまとめて出力<br/>ユーザーに現状を共有]

    context --> wt_status[session_worktree_status<br/>git worktree listの結果を整形表示<br/>各worktreeのロック状態とブランチ名]
    wt_status --> active_wt[active_worktree_check<br/>ロック中のworktreeを検出<br/>他セッションが作業中の可能性を警告]
    active_wt --> pr_warn[open_pr_warning<br/>gh pr listでオープンPR一覧を取得<br/>介入禁止PRを赤字で警告表示]
    pr_warn --> resume_warn[session_resume_warning<br/>transcriptファイル比較で別セッション検出<br/>競合リスクがある場合は警告]
    resume_warn --> fork_check[fork_session_collaboration_advisor<br/>fork-session向けに独立Issue候補を提案<br/>競合しない作業を推奨]
    fork_check --> ready([セッション準備完了<br/>全初期化処理完了<br/>作業開始可能])

    ready --> work[作業実行...]
    work --> stop_trigger{Stop検知<br/>セッション終了シグナルを検出<br/>Ctrl+Cまたは明示的終了}
    stop_trigger -->|No| work

    stop_trigger -->|Yes| metrics[session_metrics_collector<br/>Read/Write/Bash等のツール使用回数を集計<br/>session-metrics.logに出力]
    metrics --> outcome[session_outcome_collector<br/>作成したPR/Issue数をカウント<br/>セッション成果を記録]
    outcome --> handoff_write[session_handoff_writer<br/>作業内容を.claude/handoff.mdに保存<br/>次回セッションへの引き継ぎ情報]
    handoff_write --> todo_check[session_todo_check<br/>TodoWriteの未完了項目を検出<br/>in_progress/pendingがあれば警告]
    todo_check -->|未完了あり| warn_todo[警告表示<br/>未完了タスクの一覧を表示<br/>完了または引き継ぎを促す]
    warn_todo --> reflect_check[reflection_completion_check<br/>transcriptで/reflect実行を検索<br/>未実行ならexit 2でブロック]
    todo_check -->|なし| reflect_check

    reflect_check -->|未実行| block_reflect[ブロック: 振り返り必須<br/>/reflect Skillの実行が必要<br/>セッション終了をブロック]
    block_reflect --> work
    reflect_check -->|実行済み| cwd[cwd_check<br/>現在のcwdが存在するか確認<br/>削除済みworktree内なら警告]
    cwd --> git_status[git_status_check<br/>未コミット変更の有無を確認<br/>変更があれば警告表示]
    git_status --> 終了([セッション終了<br/>全終了処理完了<br/>安全にセッションを終了])
```

---

## 5. フック実行フロー

イベント駆動のパイプライン処理。

```mermaid
flowchart TD
    開始([イベント発火]) --> trigger{triggerタイプ}

    trigger -->|SessionStart| ss_date[date_context_injector<br/>現在日時とSession IDを出力<br/>全セッションで最初に実行]
    ss_date --> ss_env[environment_integrity_check<br/>settings.jsonとindex.jsonの同期確認<br/>不整合時は修正オプションを提案]
    ss_env --> ss_handoff[session_handoff_reader<br/>.claude/handoff.mdの内容を表示<br/>前回セッションの引き継ぎ]
    ss_handoff --> exec

    trigger -->|UserPromptSubmit| ups_fork[fork_session_id_updater<br/>fork-session検出時にSession IDを更新<br/>親セッションとの区別に使用]
    ups_fork --> ups_feedback[feedback_detector<br/>ユーザーフィードバックパターンを検出<br/>検出時は仕組み化を促すリマインド]
    ups_feedback --> ups_pending[immediate_pending_check<br/>未実行のIMMEDIATEタグを検出<br/>検出時はexit 2でブロック]
    ups_pending --> exec

    trigger -->|PreToolUse| matcher_pre{matcher照合}
    trigger -->|PostToolUse| matcher_post{matcher照合}
    trigger -->|Stop| stop_metrics[session_metrics_collector<br/>ツール使用回数/ブロック回数を集計<br/>session-metrics.logに出力]
    stop_metrics --> stop_handoff[session_handoff_writer<br/>作業内容をhandoff.mdに保存<br/>次回セッションへの引き継ぎ]
    stop_handoff --> stop_reflect[reflection_completion_check<br/>transcriptで/reflect実行を確認<br/>未実行時はexit 2でブロック]
    stop_reflect --> exec

    matcher_pre -->|Edit/Write| edit_warn[worktree_warning<br/>mainブランチでの編集を検出<br/>worktree作成を促す警告表示]
    edit_warn --> edit_similar[similar_code_check<br/>変更内容と類似コードをrg検索<br/>重複実装防止の警告表示]
    edit_similar --> exec

    matcher_pre -->|Bash: gh pr merge| merge_check[merge_check.py<br/>AIレビュー完了/スレッド解決を確認<br/>条件未達時はexit 2でブロック]
    merge_check --> exec

    matcher_pre -->|Bash: git worktree| wt_guard[worktree_path_guard<br/>.worktrees/配下のみ許可<br/>違反時はexit 2でブロック]
    wt_guard --> exec

    matcher_pre -->|Bash: git commit| commit_why[commit_message_why_check<br/>メッセージに背景/理由があるか確認<br/>欠落時はexit 2でブロック]
    commit_why --> exec

    matcher_post -->|Bash: gh pr merge成功| cleanup[worktree_auto_cleanup<br/>マージ成功後にworktree削除を提案<br/>mainでのgit pullも促す]
    cleanup --> exec

    matcher_pre -->|非マッチ| skip[スキップ: 該当フックなし]
    matcher_post -->|非マッチ| skip
    skip --> 終了_pass([pass: 継続])

    exec[フック実行] --> result{実行結果}
    result -->|exit 0 + メッセージ| 終了_info([info: 情報表示して継続])
    result -->|exit 0 + 出力なし| 終了_pass

    result -->|exit 2| 終了_block([block: 処理停止<br/>ユーザー対応が必要])
    result -->|exception| fail_open{fail-open設定}
    fail_open -->|有効| 終了_warn([warn: 警告表示して継続])
    fail_open -->|無効| 終了_error([error: エラー表示して継続])
```

---

## 6. 振り返りフロー

reflectコマンドによる改善サイクル。

```mermaid
flowchart TD
    開始(["reflect実行"]) --> log_collect[ログ収集開始<br/>セッションIDを元にログファイルを特定<br/>.claude/logs/配下を検索]

    log_collect --> hook_log[hook-execution-SESSION.jsonl読込<br/>全フック実行結果を取得<br/>block/warn/passの統計を集計]
    hook_log --> api_log[api-operations-SESSION.jsonl読込<br/>GitHub API操作履歴を取得<br/>Issue/PR作成・更新を確認]
    api_log --> flow_log[state-SESSION.json読込<br/>フェーズ遷移履歴を取得<br/>implementation→review→merge等]

    flow_log --> gosei[五省評価開始<br/>日本海軍の自己反省フレームワーク<br/>5つの観点で自己評価]
    gosei --> q1{要件理解に悖るなかりしか<br/>ユーザー要求を正確に理解したか<br/>曖昧な点を確認せず進めなかったか}
    q1 --> q2{実装に恥づるなかりしか<br/>コード品質は十分か<br/>テスト・ドキュメントは適切か}
    q2 --> q3{検証に欠くるなかりしか<br/>ビルド・テスト・Lintを確認したか<br/>レビューコメントを見落としていないか}
    q3 --> q4{対応に憾みなかりしか<br/>レビュー指摘を慎重に評価したか<br/>全てに対応したか}
    q4 --> q5{効率に欠くるなかりしか<br/>無駄な作業はなかったか<br/>並列実行・sub-agentを活用したか}

    q5 --> lesson{教訓判定<br/>改善すべき点が見つかったか<br/>再発防止が必要か}
    lesson -->|あり| lesson_extract[教訓抽出<br/>具体的な問題と原因を特定<br/>なぜなぜ分析で根本原因を追求]
    lesson_extract --> sys_check{仕組み化判定<br/>フック/CI/ツールで強制可能か<br/>ドキュメント追加だけでは不十分}

    sys_check -->|Yes| create_issue[Issue作成<br/>gh issue createで改善Issueを作成<br/>P2/enhancementラベルを付与]
    create_issue --> add_perspective["add-perspective実行<br/>reflection_self_check.pyに観点追加<br/>将来の振り返りで検出可能に"]
    add_perspective --> implement[フック/CI実装<br/>強制機構を実装してマージまで完遂<br/>セッション内で完了必須]
    implement --> 終了_ok([振り返り完了<br/>教訓が仕組み化された<br/>再発防止策が実装済み])

    sys_check -->|No| record_decision[record-issue-decision.py実行<br/>スキップ理由をissue-decisions.jsonlに記録<br/>後からスキップ判断を評価可能]
    record_decision --> 終了_ok

    lesson -->|なし| self_check[reflection_self_check<br/>PERSPECTIVESに定義された観点を検証<br/>キーワード検索で言及漏れを検出]
    self_check --> missing{観点漏れ判定<br/>必須観点がtranscriptに含まれるか<br/>漏れがあれば警告}
    missing -->|あり| warn_missing[警告: 観点確認必要<br/>漏れている観点を一覧表示<br/>再度確認を促す]
    warn_missing --> gosei
    missing -->|なし| 終了_ok
```

---

## 7. マージ条件チェックフロー

gh pr merge時の段階的検証。GitHubブランチ保護とmerge_check.pyの2段階。

```mermaid
flowchart TD
    開始([gh pr merge]) --> github{GitHubブランチ保護<br/>CI状態/BEHIND/reviewDecisionを確認<br/>GitHub側で自動チェック}
    github -->|CI失敗| block_ci[ブロック: CI失敗<br/>修正してプッシュ必要]
    github -->|BEHIND| block_behind[ブロック: mainより遅れ<br/>リベースが必要]
    github -->|CHANGES_REQUESTED| block_review[ブロック: 変更要求あり<br/>レビュー対応が必要]
    block_ci --> 終了_fail([マージ失敗])
    block_behind --> 終了_fail
    block_review --> 終了_fail
    github -->|OK| merge_check[merge_check.py<br/>カスタムマージ条件を検証<br/>settings.jsonで定義された全チェック実行]

    merge_check --> ai_status{check_ai_reviewing<br/>requested_reviewersにCopilot/Codexがいるか<br/>いればレビュー進行中と判定}
    ai_status -->|進行中| block_ai[ブロック: AIレビュー待ち<br/>完了まで待機が必要]
    block_ai --> 終了_fail
    ai_status -->|完了| threads{check_unresolved_ai_threads<br/>AIレビュアーの未解決スレッドを検出<br/>GraphQL reviewThreadsを検査}

    threads -->|あり| block_threads[ブロック: 未解決スレッドあり<br/>対応してResolve必要]
    block_threads --> 終了_fail
    threads -->|なし| response{check_resolved_without_response<br/>Resolveされたスレッドに署名付き返信があるか<br/>-- Claude Code署名を検索}

    response -->|署名なし| block_response[ブロック: 署名付き返信なし<br/>-- Claude Code署名必須]
    block_response --> 終了_fail
    response -->|あり| verified{check_resolved_without_verification<br/>修正に対するVerifiedコメントがあるか<br/>Verified:キーワードを検索}

    verified -->|なし| block_verified[ブロック: 検証コメントなし<br/>Verified: 形式で確認内容を記載]
    block_verified --> 終了_fail
    verified -->|あり| issue_check{check_incomplete_acceptance_criteria<br/>リンク先Issueのチェックボックスを確認<br/>未完了があればブロック}

    issue_check -->|未完了| block_issue[ブロック: Issue要件未完了<br/>チェックボックスを完了させる]
    block_issue --> 終了_fail
    issue_check -->|OK| security{check_security_issues_without_issue<br/>セキュリティ指摘にIssue参照があるか<br/>medium以上はIssue必須}

    security -->|Issue参照なし| block_security[ブロック: セキュリティIssue未作成<br/>Issue作成して参照を追加]
    block_security --> 終了_fail
    security -->|OK| wt_lock{locked_worktree_guard<br/>worktreeがロック中かを確認<br/>ロック中なら自動unlock}

    wt_lock -->|ロック中| auto_unlock[自動unlock実行<br/>git worktree unlock後にマージ許可]
    auto_unlock --> merge[マージ実行]
    wt_lock -->|非ロック| merge

    merge --> cleanup[worktree_auto_cleanup<br/>マージ成功を検出してworktree削除を提案<br/>mainディレクトリへのcd推奨]
    cleanup --> pull_remind[pr_merge_pull_reminder<br/>mainでgit pullを促すメッセージ表示<br/>ローカルmainを最新化]
    pull_remind --> 終了_ok([マージ成功])
```

---

## 8. worktree管理フロー

並列開発環境のライフサイクル制御。

```mermaid
flowchart TD
    開始([git worktree add]) --> path[worktree_path_guard<br/>.worktrees/配下のパスかを検証<br/>違反時はexit 2でブロック]

    path -->|.worktrees外| block_path[ブロック: パス不正<br/>.worktrees/issue-N形式を使用]
    block_path --> 終了_fail([作成失敗])
    path -->|OK| issue[issue_branch_check<br/>ブランチ名にissue-N形式が含まれるか検証<br/>Issue番号がなければexit 2でブロック]

    issue -->|番号なし| block_issue[ブロック: Issue番号必須<br/>feat/issue-123-descのような形式]
    block_issue --> 終了_fail
    issue -->|OK| main_fresh[worktree_main_freshness_check<br/>origin/mainとの乖離日数を計算<br/>1日以上古ければ警告表示]

    main_fresh -->|1日以上| warn_fresh[警告: mainが古い<br/>git fetch origin main推奨]
    warn_fresh --> create[worktree作成実行]
    main_fresh -->|最新| create

    create --> lock[--lockオプション処理<br/>他セッションからの削除を防止<br/>作業中worktreeを保護]
    lock --> setup[worktree_auto_setup<br/>package.jsonを検出してpnpm install実行<br/>依存関係を自動インストール]
    setup --> 終了_ok([作成成功])

    終了_ok --> work[作業実行...]
    work --> remove([git worktree remove])

    remove --> removal_check[worktree_removal_check<br/>4つの削除条件を順次検証<br/>全条件パスで削除許可]
    removal_check --> cwd{cwd検証<br/>削除対象ディレクトリ内にいるか<br/>pwdで現在位置を確認}
    cwd -->|内部| block_cwd[ブロック: cwdが削除対象内<br/>cd /main/repoで移動してから再試行]
    block_cwd --> work

    cwd -->|外部| commit{未コミット変更検証<br/>git statusで変更を確認<br/>未コミットがあれば警告}
    commit -->|あり| block_commit[ブロック: 未コミット変更あり<br/>コミットまたはstashしてから再試行]
    block_commit --> work
    commit -->|なし| pr{PR状態検証<br/>gh pr listでブランチのPRを確認<br/>オープンならブロック}

    pr -->|オープン| block_pr[ブロック: PRがオープン<br/>マージまたはクローズしてから削除]
    block_pr --> work
    pr -->|マージ済み/なし| unlock[git worktree unlock<br/>ロックを解除して削除準備<br/>--forceは使用しない]

    unlock --> delete[worktree削除実行<br/>git worktree remove実行<br/>ディレクトリとGit参照を削除]
    delete --> 終了_delete([削除成功])
```
