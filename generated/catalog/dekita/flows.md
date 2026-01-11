# dekita! システムフロー図

## 1. 開発ワークフロー全体像

Issue着手からPRマージまでの完全な開発サイクル。worktree運用による並列開発、CI監視、AIレビュー対応を含む。

```mermaid
flowchart TD
    START([セッション開始]) --> SESSION_START_HOOKS[SessionStartフック群実行]
    SESSION_START_HOOKS --> MAIN_SYNC{main同期チェック}
    MAIN_SYNC -->|遅れあり| PULL_MAIN[git pull origin main]
    MAIN_SYNC -->|同期済み| ISSUE_SELECT[Issue選択]
    PULL_MAIN --> ISSUE_SELECT

    ISSUE_SELECT --> OPEN_ISSUE_CHECK{オープンIssue確認}
    OPEN_ISSUE_CHECK -->|既存PR有| PR_WARNING[オープンPR警告表示]
    OPEN_ISSUE_CHECK -->|PRなし| WORKTREE_CREATE[worktree作成]
    PR_WARNING --> WORKTREE_CREATE

    WORKTREE_CREATE --> WT_LOCK[worktreeロック取得]
    WT_LOCK --> SESSION_MARKER[セッションマーカー設置]
    SESSION_MARKER --> PNPM_INSTALL[pnpm install実行]
    PNPM_INSTALL --> LEFTHOOK_SETUP[lefthook設定確認]

    LEFTHOOK_SETUP --> IMPL_START[実装開始]
    IMPL_START --> CODE_EDIT[コード編集]
    CODE_EDIT --> PRETOOL_HOOKS[PreToolUseフック群実行]
    PRETOOL_HOOKS -->|ブロック| BLOCK_HANDLE[ブロック対応]
    PRETOOL_HOOKS -->|警告| WARN_CONTINUE[警告確認後続行]
    PRETOOL_HOOKS -->|パス| EDIT_EXEC[編集実行]

    BLOCK_HANDLE --> CODE_EDIT
    WARN_CONTINUE --> EDIT_EXEC
    EDIT_EXEC --> POSTTOOL_HOOKS[PostToolUseフック群実行]
    POSTTOOL_HOOKS --> LINT_CHECK[lint/typecheck実行]
    LINT_CHECK --> TEST_RUN[テスト実行]

    TEST_RUN -->|失敗| CODE_EDIT
    TEST_RUN -->|成功| COMMIT_CREATE[コミット作成]
    COMMIT_CREATE --> COMMIT_HOOKS[コミットフック群実行]
    COMMIT_HOOKS --> PR_CREATE[PR作成]

    PR_CREATE --> PR_HOOKS[PR作成フック群実行]
    PR_HOOKS --> PR_SUBMIT[PR送信]
    PR_SUBMIT --> CI_WAIT[CI完了待機]

    CI_WAIT --> CI_RESULT{CI結果}
    CI_RESULT -->|失敗| CI_FIX[CI失敗修正]
    CI_RESULT -->|成功| AI_REVIEW_WAIT[AIレビュー待機]
    CI_FIX --> CODE_EDIT

    AI_REVIEW_WAIT --> REVIEW_RESULT{レビュー結果}
    REVIEW_RESULT -->|要修正| REVIEW_FIX[レビュー指摘対応]
    REVIEW_RESULT -->|承認| MERGE_CHECK[マージ前チェック]
    REVIEW_FIX --> CODE_EDIT

    MERGE_CHECK --> MERGE_COND{マージ条件}
    MERGE_COND -->|未達成| CONDITION_FIX[条件充足対応]
    MERGE_COND -->|全条件OK| MERGE_EXEC[マージ実行]
    CONDITION_FIX --> MERGE_CHECK

    MERGE_EXEC --> POST_MERGE[マージ後処理]
    POST_MERGE --> WT_CLEANUP[worktreeクリーンアップ]
    WT_CLEANUP --> REFLECT_EXEC[振り返り実行]
    REFLECT_EXEC --> SESSION_END([セッション終了])
```

## 2. Issue作成フロー

Issue作成時の品質チェックと自動処理。優先度ラベル、必須セクション、重複検出を含む。

```mermaid
flowchart TD
    ISSUE_START([Issue作成開始]) --> LABEL_CHECK{ラベル確認}
    LABEL_CHECK -->|ラベルなし| LABEL_ADD[ラベル追加要求]
    LABEL_CHECK -->|ラベルあり| PRIORITY_CHECK{優先度ラベル確認}
    LABEL_ADD --> PRIORITY_CHECK

    PRIORITY_CHECK -->|P0-P3なし| PRIORITY_BLOCK[優先度ラベル必須ブロック]
    PRIORITY_CHECK -->|P0-P3あり| BODY_CHECK[本文セクション確認]
    PRIORITY_BLOCK --> PRIORITY_ADD[優先度ラベル追加]
    PRIORITY_ADD --> BODY_CHECK

    BODY_CHECK --> WHY_CHECK{なぜセクション}
    WHY_CHECK -->|なし| BODY_WARN[必須セクション警告]
    WHY_CHECK -->|あり| CURRENT_CHECK{現状セクション}

    CURRENT_CHECK -->|なし| BODY_WARN
    CURRENT_CHECK -->|あり| EXPECTED_CHECK{期待動作セクション}

    EXPECTED_CHECK -->|なし| BODY_WARN
    EXPECTED_CHECK -->|あり| PROPOSAL_CHECK{対応案セクション}

    PROPOSAL_CHECK -->|なし| BODY_WARN
    PROPOSAL_CHECK -->|あり| DUP_CHECK[重複Issue検索]

    BODY_WARN --> BODY_FIX[本文修正]
    BODY_FIX --> BODY_CHECK

    DUP_CHECK --> DUP_RESULT{重複あり?}
    DUP_RESULT -->|類似Issue存在| DUP_WARN[重複警告表示]
    DUP_RESULT -->|重複なし| SCOPE_CHECK[スコープ確認]
    DUP_WARN --> SCOPE_CHECK

    SCOPE_CHECK --> MULTI_PROB{複数問題含む?}
    MULTI_PROB -->|はい| SPLIT_SUGGEST[Issue分割提案]
    MULTI_PROB -->|いいえ| ISSUE_CREATE[Issue作成実行]
    SPLIT_SUGGEST --> ISSUE_CREATE

    ISSUE_CREATE --> AUTO_ASSIGN[自動アサイン]
    AUTO_ASSIGN --> AI_REVIEW_SUGGEST[AIレビュー提案]
    AI_REVIEW_SUGGEST --> ISSUE_DONE([Issue作成完了])
```

## 3. CI監視フロー

PR作成後のCI監視、自動リベース、AIレビュー対応を含む継続監視プロセス。

```mermaid
flowchart TD
    CI_START([CI監視開始]) --> PR_VALIDATE[PR番号バリデーション]
    PR_VALIDATE --> STATE_LOAD[監視状態読み込み]
    STATE_LOAD --> RATE_CHECK[レート制限確認]

    RATE_CHECK --> RATE_STATUS{残量確認}
    RATE_STATUS -->|警告レベル| INTERVAL_ADJUST[ポーリング間隔調整]
    RATE_STATUS -->|正常| PR_STATE_GET[PR状態取得]
    INTERVAL_ADJUST --> PR_STATE_GET

    PR_STATE_GET --> MERGE_STATE{マージ状態}
    MERGE_STATE -->|MERGED| MERGED_HANDLE[マージ済み処理]
    MERGE_STATE -->|CLOSED| CLOSED_HANDLE[クローズ済み処理]
    MERGE_STATE -->|BEHIND| BEHIND_HANDLE[遅れ検出]
    MERGE_STATE -->|DIRTY| DIRTY_HANDLE[コンフリクト検出]
    MERGE_STATE -->|OPEN| CI_CHECK[CIステータス確認]

    BEHIND_HANDLE --> REBASE_EXEC[自動リベース実行]
    REBASE_EXEC --> REBASE_RESULT{リベース結果}
    REBASE_RESULT -->|成功| PR_STATE_GET
    REBASE_RESULT -->|コンフリクト| CONFLICT_NOTIFY[コンフリクト通知]

    DIRTY_HANDLE --> LOCAL_CHANGE_CHECK[ローカル変更確認]
    LOCAL_CHANGE_CHECK --> CONFLICT_NOTIFY

    CI_CHECK --> CI_STATUS{CIステータス}
    CI_STATUS -->|PENDING| WAIT_LOOP[待機ループ]
    CI_STATUS -->|FAILURE| CI_FAIL_NOTIFY[CI失敗通知]
    CI_STATUS -->|SUCCESS| REVIEW_CHECK[レビュー状態確認]

    WAIT_LOOP --> RATE_CHECK

    REVIEW_CHECK --> COPILOT_CHECK{Copilotレビュー}
    COPILOT_CHECK -->|エラー| COPILOT_RETRY[Copilotリトライ]
    COPILOT_CHECK -->|待機中| WAIT_LOOP
    COPILOT_CHECK -->|完了| CODEX_CHECK{Codexレビュー}

    COPILOT_RETRY --> RETRY_RESULT{リトライ結果}
    RETRY_RESULT -->|成功| CODEX_CHECK
    RETRY_RESULT -->|失敗| RETRY_LIMIT{リトライ上限}
    RETRY_LIMIT -->|未達| COPILOT_RETRY
    RETRY_LIMIT -->|到達| CODEX_CHECK

    CODEX_CHECK -->|待機中| WAIT_LOOP
    CODEX_CHECK -->|完了| THREAD_CHECK[未解決スレッド確認]

    THREAD_CHECK --> UNRESOLVED{未解決あり?}
    UNRESOLVED -->|あり| DUP_THREAD_CHECK[重複スレッド確認]
    UNRESOLVED -->|なし| READY_NOTIFY[マージ準備完了通知]

    DUP_THREAD_CHECK --> AUTO_RESOLVE[重複スレッド自動解決]
    AUTO_RESOLVE --> THREAD_CHECK

    MERGED_HANDLE --> WT_CLEANUP_SUGGEST[worktreeクリーンアップ提案]
    CLOSED_HANDLE --> SESSION_END_NOTIFY[セッション終了通知]
    READY_NOTIFY --> MERGE_SUGGEST[マージ実行提案]

    WT_CLEANUP_SUGGEST --> CI_END([監視終了])
    SESSION_END_NOTIFY --> CI_END
    MERGE_SUGGEST --> CI_END
    CONFLICT_NOTIFY --> CI_END
    CI_FAIL_NOTIFY --> CI_END
```

## 4. セッション管理フロー

セッション開始から終了までのライフサイクル管理。状態注入、マーカー管理、引き継ぎを含む。

```mermaid
flowchart TD
    SESSION_INIT([セッション初期化]) --> ENV_CHECK[環境整合性確認]
    ENV_CHECK --> MARKER_CHECK{既存マーカー確認}

    MARKER_CHECK -->|継続セッション| HANDOFF_READ[引き継ぎ読み込み]
    MARKER_CHECK -->|新規セッション| NEW_MARKER[新規マーカー作成]

    HANDOFF_READ --> RESUME_WARN[再開警告表示]
    RESUME_WARN --> CONTEXT_INJECT[コンテキスト注入]
    NEW_MARKER --> CONTEXT_INJECT

    CONTEXT_INJECT --> DATE_INJECT[日付情報注入]
    DATE_INJECT --> WT_STATUS[worktree状態表示]
    WT_STATUS --> OPEN_ISSUE_LIST[オープンIssue一覧]
    OPEN_ISSUE_LIST --> MERGED_WT_CHECK[マージ済みworktree確認]

    MERGED_WT_CHECK --> ORPHAN_CHECK[孤立worktree確認]
    ORPHAN_CHECK --> MAIN_FRESH_CHECK[main鮮度確認]
    MAIN_FRESH_CHECK --> FORK_CHECK{Fork-session?}

    FORK_CHECK -->|はい| COLLAB_ADVISE[独立Issue候補提案]
    FORK_CHECK -->|いいえ| SESSION_READY[セッション準備完了]
    COLLAB_ADVISE --> SESSION_READY

    SESSION_READY --> WORK_LOOP[作業ループ]
    WORK_LOOP --> MARKER_REFRESH[マーカー更新]
    MARKER_REFRESH --> METRICS_COLLECT[メトリクス収集]
    METRICS_COLLECT --> WORK_LOOP

    WORK_LOOP --> STOP_TRIGGER{停止トリガー}
    STOP_TRIGGER --> STOP_HOOKS[Stopフック群実行]

    STOP_HOOKS --> TODO_CHECK[TODO完了確認]
    TODO_CHECK --> LESSON_CHECK[教訓Issue化確認]
    LESSON_CHECK --> REFLECT_CHECK{振り返り実行済み?}

    REFLECT_CHECK -->|未実行| REFLECT_REMIND[振り返りリマインダー]
    REFLECT_CHECK -->|実行済み| HANDOFF_WRITE[引き継ぎ書き出し]
    REFLECT_REMIND --> HANDOFF_WRITE

    HANDOFF_WRITE --> OUTCOME_COLLECT[成果収集]
    OUTCOME_COLLECT --> LOG_HEALTH[ログ健全性確認]
    LOG_HEALTH --> SESSION_END([セッション終了])
```

## 5. フック実行フロー

Claude Codeフックの実行パイプライン。イベント検出、フック選択、結果処理を含む。

```mermaid
flowchart TD
    EVENT_DETECT([イベント発生]) --> EVENT_TYPE{イベントタイプ}

    EVENT_TYPE -->|SessionStart| SS_HOOKS[SessionStartフック群]
    EVENT_TYPE -->|UserPromptSubmit| UPS_HOOKS[UserPromptSubmitフック群]
    EVENT_TYPE -->|PreToolUse| PTU_HOOKS[PreToolUseフック群]
    EVENT_TYPE -->|PostToolUse| POTU_HOOKS[PostToolUseフック群]
    EVENT_TYPE -->|Stop| STOP_HOOKS[Stopフック群]

    SS_HOOKS --> HOOK_EXEC_SS[フック実行]
    UPS_HOOKS --> HOOK_EXEC_UPS[フック実行]
    PTU_HOOKS --> TOOL_TYPE_CHECK{ツールタイプ}
    POTU_HOOKS --> HOOK_EXEC_POTU[フック実行]
    STOP_HOOKS --> HOOK_EXEC_STOP[フック実行]

    TOOL_TYPE_CHECK -->|Bash| BASH_HOOKS[Bashフック群]
    TOOL_TYPE_CHECK -->|Edit/Write| EDIT_HOOKS[Edit/Writeフック群]
    TOOL_TYPE_CHECK -->|Read| READ_HOOKS[Readフック群]
    TOOL_TYPE_CHECK -->|Skill| SKILL_HOOKS[Skillフック群]

    BASH_HOOKS --> HOOK_EXEC_BASH[フック実行]
    EDIT_HOOKS --> HOOK_EXEC_EDIT[フック実行]
    READ_HOOKS --> HOOK_EXEC_READ[フック実行]
    SKILL_HOOKS --> HOOK_EXEC_SKILL[フック実行]

    HOOK_EXEC_SS --> RESULT_PARSE[結果パース]
    HOOK_EXEC_UPS --> RESULT_PARSE
    HOOK_EXEC_BASH --> RESULT_PARSE
    HOOK_EXEC_EDIT --> RESULT_PARSE
    HOOK_EXEC_READ --> RESULT_PARSE
    HOOK_EXEC_SKILL --> RESULT_PARSE
    HOOK_EXEC_POTU --> RESULT_PARSE
    HOOK_EXEC_STOP --> RESULT_PARSE

    RESULT_PARSE --> RESULT_TYPE{結果タイプ}
    RESULT_TYPE -->|block| BLOCK_PROC[ブロック処理]
    RESULT_TYPE -->|warn| WARN_PROC[警告処理]
    RESULT_TYPE -->|info| INFO_PROC[情報表示]
    RESULT_TYPE -->|pass| PASS_PROC[パス処理]
    RESULT_TYPE -->|error| ERROR_PROC[エラー処理]

    BLOCK_PROC --> BLOCK_LOG[ブロックログ記録]
    BLOCK_LOG --> BLOCK_RESPONSE[ブロックレスポンス]

    WARN_PROC --> WARN_LOG[警告ログ記録]
    WARN_LOG --> WARN_DISPLAY[警告表示]
    WARN_DISPLAY --> CONTINUE_EXEC[実行継続]

    INFO_PROC --> INFO_INJECT[システムメッセージ注入]
    INFO_INJECT --> CONTINUE_EXEC

    PASS_PROC --> CONTINUE_EXEC

    ERROR_PROC --> FAILOPEN[fail-open継続]
    FAILOPEN --> CONTINUE_EXEC

    BLOCK_RESPONSE --> EVENT_END([イベント処理完了])
    CONTINUE_EXEC --> EVENT_END
```

## 6. 振り返りフロー

/reflectコマンドによる振り返りプロセス。五省、教訓抽出、Issue化、仕組み化を含む。

```mermaid
flowchart TD
    REFLECT_START([/reflect実行]) --> LOG_GATHER[ログ収集]
    LOG_GATHER --> API_LOG[API操作ログ分析]
    API_LOG --> HOOK_LOG[フック実行ログ分析]
    HOOK_LOG --> REWORK_LOG[手戻りメトリクス分析]

    REWORK_LOG --> GOSEI_START[五省開始]

    GOSEI_START --> GOSEI_1[至誠: 真心で取り組んだか]
    GOSEI_1 --> GOSEI_2[言行: 言葉と行動は一致したか]
    GOSEI_2 --> GOSEI_3[克己: 怠けず最善を尽くしたか]
    GOSEI_3 --> GOSEI_4[礼節: ルールを守ったか]
    GOSEI_4 --> GOSEI_5[勉強: 新しい知識を得たか]

    GOSEI_5 --> LESSON_EXTRACT[教訓抽出]
    LESSON_EXTRACT --> LESSON_LIST{教訓あり?}

    LESSON_LIST -->|なし| REFLECT_END([振り返り完了])
    LESSON_LIST -->|あり| LESSON_CLASSIFY[教訓分類]

    LESSON_CLASSIFY --> ISSUE_CHECK{Issue化必要?}
    ISSUE_CHECK -->|観点追加| PERSPECTIVE_ADD[観点フック追加検討]
    ISSUE_CHECK -->|仕組み化| SYSTEM_ADD[仕組み化Issue作成]
    ISSUE_CHECK -->|記録のみ| LOG_ONLY[ログ記録のみ]

    PERSPECTIVE_ADD --> SELF_CHECK_UPDATE[reflection-self-check更新]
    SELF_CHECK_UPDATE --> TEST_ADD[テスト追加]

    SYSTEM_ADD --> IMPL_PLAN[実装計画作成]
    IMPL_PLAN --> HOOK_OR_CI{実装方法}
    HOOK_OR_CI -->|フック| NEW_HOOK[新規フック作成]
    HOOK_OR_CI -->|CI| CI_UPDATE[CI更新]
    HOOK_OR_CI -->|スキル| SKILL_UPDATE[スキル更新]

    NEW_HOOK --> METADATA_UPDATE[metadata.json更新]
    CI_UPDATE --> WORKFLOW_UPDATE[ワークフロー更新]
    SKILL_UPDATE --> SKILL_DOC_UPDATE[スキルドキュメント更新]

    METADATA_UPDATE --> CLOSE_CHECK[仕組み化Issueクローズ確認]
    WORKFLOW_UPDATE --> CLOSE_CHECK
    SKILL_DOC_UPDATE --> CLOSE_CHECK
    TEST_ADD --> CLOSE_CHECK
    LOG_ONLY --> CLOSE_CHECK

    CLOSE_CHECK --> FORCE_CHECK{強制機構実装済み?}
    FORCE_CHECK -->|はい| ISSUE_CLOSE[Issue自動クローズ]
    FORCE_CHECK -->|いいえ| ISSUE_OPEN_KEEP[Issueオープン維持]

    ISSUE_CLOSE --> REFLECT_END
    ISSUE_OPEN_KEEP --> REFLECT_END
```

## 7. マージ条件チェックフロー

PRマージ前の12+条件チェック。AIレビュー、CI、Issue状態、テストカバレッジを含む。

```mermaid
flowchart TD
    MERGE_REQ([マージリクエスト]) --> UNSAFE_CHECK{危険フラグ確認}
    UNSAFE_CHECK -->|--auto/--admin| UNSAFE_BLOCK[危険フラグブロック]
    UNSAFE_CHECK -->|通常| CONDITIONS_START[条件チェック開始]

    CONDITIONS_START --> CI_STATUS_CHECK[CIステータス確認]
    CI_STATUS_CHECK --> CI_RESULT{CI結果}
    CI_RESULT -->|失敗/実行中| CI_BLOCK[CI未完了ブロック]
    CI_RESULT -->|成功| COPILOT_CHECK[Copilotレビュー確認]

    COPILOT_CHECK --> COPILOT_RESULT{Copilot結果}
    COPILOT_RESULT -->|未レビュー| COPILOT_BLOCK[Copilotレビュー待ちブロック]
    COPILOT_RESULT -->|完了| CODEX_CHECK[Codexレビュー確認]

    CODEX_CHECK --> CODEX_RESULT{Codex結果}
    CODEX_RESULT -->|未レビュー| CODEX_BLOCK[Codexレビュー待ちブロック]
    CODEX_RESULT -->|完了| ISSUE_CHECK[Issue状態確認]

    ISSUE_CHECK --> ISSUE_LINKED{Issue紐付け}
    ISSUE_LINKED -->|なし| ISSUE_BLOCK[Issue未紐付けブロック]
    ISSUE_LINKED -->|あり| ACCEPTANCE_CHECK[受け入れ条件確認]

    ACCEPTANCE_CHECK --> ACCEPTANCE_RESULT{条件完了}
    ACCEPTANCE_RESULT -->|未完了| ACCEPTANCE_BLOCK[受け入れ条件未完了ブロック]
    ACCEPTANCE_RESULT -->|完了| THREAD_CHECK[未解決スレッド確認]

    THREAD_CHECK --> THREAD_RESULT{未解決あり?}
    THREAD_RESULT -->|あり| THREAD_BLOCK[未解決スレッドブロック]
    THREAD_RESULT -->|なし| BUG_CHECK{bugラベル?}

    BUG_CHECK -->|はい| FIX_VERIFY[修正検証確認]
    BUG_CHECK -->|いいえ| PR_BODY_CHECK[PR本文品質確認]

    FIX_VERIFY --> VERIFY_RESULT{検証テストあり?}
    VERIFY_RESULT -->|なし| VERIFY_BLOCK[検証テスト未追加ブロック]
    VERIFY_RESULT -->|あり| PR_BODY_CHECK

    PR_BODY_CHECK --> BODY_RESULT{Summary/Test plan}
    BODY_RESULT -->|不足| BODY_WARN[本文品質警告]
    BODY_RESULT -->|OK| SCOPE_CHECK[スコープ確認]
    BODY_WARN --> SCOPE_CHECK

    SCOPE_CHECK --> SCOPE_RESULT{適切なスコープ?}
    SCOPE_RESULT -->|過大| SCOPE_WARN[スコープ過大警告]
    SCOPE_RESULT -->|適切| ALL_PASS[全条件クリア]
    SCOPE_WARN --> ALL_PASS

    UNSAFE_BLOCK --> MERGE_DENIED([マージ拒否])
    CI_BLOCK --> MERGE_DENIED
    COPILOT_BLOCK --> MERGE_DENIED
    CODEX_BLOCK --> MERGE_DENIED
    ISSUE_BLOCK --> MERGE_DENIED
    ACCEPTANCE_BLOCK --> MERGE_DENIED
    THREAD_BLOCK --> MERGE_DENIED
    VERIFY_BLOCK --> MERGE_DENIED

    ALL_PASS --> MERGE_ALLOWED([マージ許可])
```

## 8. worktree管理フロー

worktreeのライフサイクル管理。作成、ロック、セッション追跡、クリーンアップを含む。

```mermaid
flowchart TD
    WT_REQUEST([worktree操作リクエスト]) --> OP_TYPE{操作タイプ}

    OP_TYPE -->|作成| PATH_CHECK[パス安全性確認]
    OP_TYPE -->|削除| LOCK_CHECK[ロック状態確認]
    OP_TYPE -->|一覧| WT_LIST[worktree一覧取得]

    PATH_CHECK --> PATH_SAFE{安全なパス?}
    PATH_SAFE -->|危険| PATH_BLOCK[危険パスブロック]
    PATH_SAFE -->|安全| BRANCH_CHECK[ブランチ確認]

    BRANCH_CHECK --> BRANCH_EXISTS{ブランチ存在?}
    BRANCH_EXISTS -->|存在| DUP_WARN[重複ブランチ警告]
    BRANCH_EXISTS -->|なし| WT_CREATE[worktree作成実行]
    DUP_WARN --> WT_CREATE

    WT_CREATE --> WT_LOCK[ロック取得]
    WT_LOCK --> MARKER_CREATE[セッションマーカー作成]
    MARKER_CREATE --> PNPM_EXEC[pnpm install実行]
    PNPM_EXEC --> LEFTHOOK_CHECK[lefthook設定確認]
    LEFTHOOK_CHECK --> WT_READY([worktree準備完了])

    LOCK_CHECK --> IS_LOCKED{ロック中?}
    IS_LOCKED -->|はい| OWNER_CHECK[所有セッション確認]
    IS_LOCKED -->|いいえ| CHANGES_CHECK[未コミット変更確認]

    OWNER_CHECK --> SAME_SESSION{同一セッション?}
    SAME_SESSION -->|いいえ| LOCK_BLOCK[他セッションロックブロック]
    SAME_SESSION -->|はい| CHANGES_CHECK

    CHANGES_CHECK --> HAS_CHANGES{変更あり?}
    HAS_CHANGES -->|あり| CHANGES_WARN[未コミット変更警告]
    HAS_CHANGES -->|なし| UNPUSHED_CHECK[未プッシュ確認]
    CHANGES_WARN --> UNPUSHED_CHECK

    UNPUSHED_CHECK --> HAS_UNPUSHED{未プッシュあり?}
    HAS_UNPUSHED -->|あり| UNPUSHED_WARN[未プッシュ警告]
    HAS_UNPUSHED -->|なし| WT_REMOVE[worktree削除実行]
    UNPUSHED_WARN --> WT_REMOVE

    WT_REMOVE --> LOCK_RELEASE[ロック解除]
    LOCK_RELEASE --> BRANCH_DELETE[ブランチ削除]
    BRANCH_DELETE --> WT_REMOVED([worktree削除完了])

    WT_LIST --> ORPHAN_DETECT[孤立worktree検出]
    ORPHAN_DETECT --> MERGED_DETECT[マージ済み検出]
    MERGED_DETECT --> STALE_DETECT[古いworktree検出]
    STALE_DETECT --> CLEANUP_SUGGEST[クリーンアップ提案]
    CLEANUP_SUGGEST --> WT_LIST_DONE([一覧表示完了])

    PATH_BLOCK --> WT_DENIED([操作拒否])
    LOCK_BLOCK --> WT_DENIED
```
