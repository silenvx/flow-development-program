#!/bin/bash
# Claude Codeステータスラインの動的生成。
#
# Why:
#     現在のworktree/Issue/PR/フロー状態を
#     ステータスラインに表示し、作業状況を可視化するため。
#
# What:
#     - get_worktree_info(): worktree/ブランチ/PR情報を取得
#     - get_flow_state(): フローフェーズ・イテレーション情報を取得
#     - get_session_id(): セッションIDを取得
#     - sanitize(): Terminal Injection対策
#
# Remarks:
#     - 入力: stdin JSON（model, workspace, session_id）
#     - 出力: [Model] worktree | PR状態 | フロー | session_id
#     - gh CLIタイムアウト: 2秒（遅延防止）
#     - 多言語対応（ja/en）
#
# Changelog:
#     - silenvx/dekita#734: セッション別stateファイル対応
#     - silenvx/dekita#777: session_id提供方式を変更
#     - silenvx/dekita#2148: フェーズ名を略称から正式名称に変更

set -euo pipefail

# 言語設定を取得
# STATUSLINE_LANGが設定されていればそれを使用、なければLANGから抽出
get_language() {
    if [ -n "${STATUSLINE_LANG:-}" ]; then
        echo "$STATUSLINE_LANG"
    elif [ -n "${LANG:-}" ]; then
        # LANG=ja_JP.UTF-8 -> ja
        echo "${LANG%%_*}"
    else
        echo "ja"  # デフォルトは日本語
    fi
}

# 言語に応じたステータス文字列を設定
setup_messages() {
    local lang
    lang=$(get_language)

    case "$lang" in
        en)
            STATUS_REVIEWING="reviewing"
            STATUS_MERGED="merged"
            STATUS_CLOSED="closed"
            STATUS_NO_PR="no PR"
            ;;
        *)
            # デフォルト: 日本語
            STATUS_REVIEWING="レビュー中"
            STATUS_MERGED="マージ済"
            STATUS_CLOSED="クローズ"
            STATUS_NO_PR="PRなし"
            ;;
    esac
}

# メッセージを初期化
setup_messages

# gh コマンドのタイムアウト（秒）- ステータスライン更新が遅延しないよう短く設定
readonly GH_TIMEOUT=2

# Issue番号パターン（正規表現）
readonly ISSUE_PATTERN='issue-[0-9]+'

# 文字列からIssue番号を抽出する
# Usage: extract_issue_number "feature/issue-123-xxx"
# Output: 123 (番号のみ、見つからない場合は空文字)
extract_issue_number() {
    local input="$1"
    echo "$input" | grep -oE "$ISSUE_PATTERN" | head -1 | sed 's/issue-//'
}

# JSON入力を読み取り
input=$(cat)

# モデル名を取得
MODEL=$(echo "$input" | jq -r '.model.display_name // "Claude"')

# 現在のディレクトリを取得
CURRENT_DIR=$(echo "$input" | jq -r '.workspace.current_dir // empty')
if [ -z "$CURRENT_DIR" ]; then
    CURRENT_DIR=$(pwd)
fi

# Git情報を取得
get_worktree_info() {
    local dir="$1"

    # Gitリポジトリかチェック
    if ! git -C "$dir" rev-parse --git-dir > /dev/null 2>&1; then
        echo ""
        return
    fi

    # 現在のブランチ名
    local branch
    branch=$(git -C "$dir" branch --show-current 2>/dev/null || echo "")

    if [ -z "$branch" ]; then
        echo ""
        return
    fi

    # worktree名を抽出（.worktrees/issue-XXX の場合）
    local worktree_name=""
    local git_dir
    git_dir=$(git -C "$dir" rev-parse --git-dir 2>/dev/null)

    if [[ "$git_dir" == *"/.worktrees/"* ]]; then
        # .worktrees/issue-XXX/.git から issue-XXX を抽出
        worktree_name=$(echo "$git_dir" | sed -n 's/.*\.worktrees\/\([^/]*\)\/.*/\1/p')
    elif [[ "$dir" == *"/.worktrees/"* ]]; then
        # パスから worktree 名を抽出
        worktree_name=$(echo "$dir" | sed -n 's/.*\.worktrees\/\([^/]*\).*/\1/p')
    fi

    # Issue番号を抽出（ブランチ名またはworktree名から）
    local issue_num=""
    if [ -n "$worktree_name" ]; then
        # worktree名から: issue-123, issue-123-description
        issue_num=$(extract_issue_number "$worktree_name")
    fi
    if [ -z "$issue_num" ]; then
        # ブランチ名から: feature/issue-123-xxx, fix/issue-456
        issue_num=$(extract_issue_number "$branch")
    fi

    # PR情報を取得（gh CLIが使える場合）
    # タイムアウトを設定してステータスライン更新の遅延を防ぐ
    local pr_info=""
    if command -v gh &> /dev/null; then
        local pr_data
        # timeout コマンドがあれば使用（macOS の場合は gtimeout を試行）
        local timeout_cmd=""
        if command -v timeout &> /dev/null; then
            timeout_cmd="timeout ${GH_TIMEOUT}s"
        elif command -v gtimeout &> /dev/null; then
            timeout_cmd="gtimeout ${GH_TIMEOUT}s"
        fi
        pr_data=$($timeout_cmd gh pr list --head "$branch" --json number,state --limit 1 2>/dev/null || echo "[]")
        local pr_num
        pr_num=$(echo "$pr_data" | jq -r '.[0].number // empty')
        local pr_state
        pr_state=$(echo "$pr_data" | jq -r '.[0].state // empty')

        if [ -n "$pr_num" ]; then
            case "$pr_state" in
                OPEN) pr_info="PR #$pr_num $STATUS_REVIEWING" ;;
                MERGED) pr_info="PR #$pr_num $STATUS_MERGED" ;;
                CLOSED) pr_info="PR #$pr_num $STATUS_CLOSED" ;;
                *) pr_info="PR #$pr_num" ;;
            esac
        else
            pr_info="$STATUS_NO_PR"
        fi
    fi

    # 表示文字列を構築
    local display=""
    if [ -n "$worktree_name" ]; then
        display="$worktree_name"
    elif [ -n "$issue_num" ]; then
        display="issue-$issue_num"
    else
        display="$branch"
    fi

    if [ -n "$pr_info" ]; then
        display="$display | $pr_info"
    fi

    echo "$display"
}

# ターミナルタイトルを設定（オプション）
set_terminal_title() {
    local title="$1"
    # OSC escape sequence for terminal title
    printf '\033]0;%s\007' "$title" >&2
}

# フェーズ名の日本語マッピング
# Issue #2148: 略称を廃止し、わかりやすい名称に変更
get_phase_name() {
    local phase="$1"
    case "$phase" in
        session_start) echo "セッション開始" ;;
        pre_check) echo "事前確認" ;;
        worktree_create) echo "worktree作成" ;;
        implementation) echo "実装" ;;
        pre_commit_check) echo "コミット前検証" ;;
        local_ai_review) echo "AIレビュー" ;;
        pr_create) echo "PR作成" ;;
        issue_work) echo "Issue作業" ;;
        ci_review) echo "CIレビュー" ;;
        merge) echo "マージ" ;;
        cleanup) echo "クリーンアップ" ;;
        production_check) echo "本番確認" ;;
        session_end) echo "セッション終了" ;;
        *) echo "$phase" ;;
    esac
}

# セッションIDを取得
# Issue #734: セッションごとに分離されたstate fileを使用
# Issue #777: Claude Codeが直接session_idを提供（環境変数・marker fileは廃止）
# Issue #779: シンプル化（stdin JSON → fallback空文字列の2段階のみ）
get_session_id() {
    # 1. stdin JSON input (Claude Codeが提供)
    # $input は64行目でグローバルに設定される
    local json_session_id
    json_session_id=$(echo "$input" | jq -r '.session_id // empty' 2>/dev/null)
    if [ -n "$json_session_id" ]; then
        echo "$json_session_id"
        return
    fi

    # 2. Fallback: empty (will use default state.json)
    echo ""
}

# フロー状態を取得
get_flow_state() {
    local project_dir="${CLAUDE_PROJECT_DIR:-}"
    if [ -z "$project_dir" ]; then
        # Try to find project dir from current directory
        project_dir=$(git -C "$CURRENT_DIR" rev-parse --show-toplevel 2>/dev/null || echo "")
    fi

    # Issue #734: Get session-specific state file
    local session_id
    session_id=$(get_session_id)

    local state_file
    if [ -n "$session_id" ]; then
        state_file="$project_dir/.claude/logs/flow/state-${session_id}.json"
    else
        # Fallback to legacy state.json for backward compatibility
        state_file="$project_dir/.claude/logs/flow/state.json"
    fi

    if [ ! -f "$state_file" ]; then
        echo ""
        return
    fi

    # Read state file
    local active_workflow
    active_workflow=$(jq -r '.active_workflow // empty' "$state_file" 2>/dev/null)

    if [ -z "$active_workflow" ]; then
        echo ""
        return
    fi

    # Get current phase and iteration
    local current_phase
    current_phase=$(jq -r ".workflows[\"$active_workflow\"].current_phase // empty" "$state_file" 2>/dev/null)

    local iterations
    iterations=$(jq -r ".workflows[\"$active_workflow\"].phases[\"$current_phase\"].iterations // 1" "$state_file" 2>/dev/null)

    local hooks_fired
    hooks_fired=$(jq -r '.global.hooks_fired_total // 0' "$state_file" 2>/dev/null)

    if [ -n "$current_phase" ]; then
        local phase_name
        phase_name=$(get_phase_name "$current_phase")
        # Issue #2148: iteration 1は表示しない（リトライ時のみ回数表示）
        if [ "$iterations" -gt 1 ]; then
            echo "⏳${phase_name} (${iterations}) | 🪝${hooks_fired}"
        else
            echo "⏳${phase_name} | 🪝${hooks_fired}"
        fi
    else
        echo ""
    fi
}

# 外部データのサニタイズ（Terminal Injection対策）
# ANSIエスケープシーケンスと制御文字（C0制御文字+DEL）を除去
sanitize() {
    local input="$1"
    # $'\x1b' でリテラルESC文字を使用（シェル互換性向上）
    # ANSIエスケープシーケンスを除去し、制御文字（0x00-0x1F, 0x7F）を除去
    printf '%s' "$input" | sed "s/$'\x1b'\[[0-9;]*[mGKHflSTABCDEFnsuJha-zA-Z]//g" | tr -d '\000-\037\177'
}

# メイン処理
WORKTREE_INFO=$(sanitize "$(get_worktree_info "$CURRENT_DIR")")
FLOW_STATE=$(sanitize "$(get_flow_state)")

# session_idを取得（claude -r でのfork用に完全な形式で表示）
SESSION_ID=$(sanitize "$(get_session_id)")

# MODELもサニタイズ
SANITIZED_MODEL=$(sanitize "$MODEL")

# ステータスライン文字列を構築（DRY化）
# DISPLAY_NAMEで統一
if [ -n "$WORKTREE_INFO" ]; then
    DISPLAY_NAME="$WORKTREE_INFO"
else
    # Git外の場合はディレクトリ名のみ
    DISPLAY_NAME=$(sanitize "$(basename "$CURRENT_DIR")")
fi

# ターミナルタイトルを設定
set_terminal_title "Claude: $DISPLAY_NAME"

# ステータスライン構築
STATUS_LINE="[$SANITIZED_MODEL] $DISPLAY_NAME"

# フロー状態があれば追加
if [ -n "$FLOW_STATE" ]; then
    STATUS_LINE="$STATUS_LINE | $FLOW_STATE"
fi

# session_idを追加して出力
printf '%s\n' "$STATUS_LINE | ${SESSION_ID:-?}"
