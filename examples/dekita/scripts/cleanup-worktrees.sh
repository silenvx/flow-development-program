#!/bin/bash
# マージ済みPRのworktreeを自動クリーンアップ。
#
# Why:
#     マージ完了したworktreeを自動削除し、
#     ディスク容量とworktree一覧の整理を行うため。
#
# What:
#     - check_prerequisites(): gh CLI存在確認
#     - worktree一覧からマージ済みPRを検出
#     - worktree削除とローカルブランチ削除
#
# Remarks:
#     - Usage: ./scripts/cleanup-worktrees.sh [--force]
#     - デフォルトはドライラン（確認のみ）
#     - Exit 0: 正常終了、Exit 1: エラー、Exit 2: 部分的失敗
#
# Changelog:
#     - silenvx/dekita#200: worktree自動クリーンアップを追加

set -euo pipefail

FORCE=false
if [[ "${1:-}" == "--force" ]]; then
    FORCE=true
fi

# 前提条件チェック: gh CLIが利用可能か確認
check_prerequisites() {
    if ! command -v gh &>/dev/null; then
        echo "❌ エラー: gh CLI がインストールされていません。"
        echo ""
        echo "インストール方法:"
        echo "  macOS: brew install gh"
        echo "  その他: https://cli.github.com/"
        exit 1
    fi

    # gh CLIが認証済みか確認
    if ! gh auth status &>/dev/null; then
        echo "❌ エラー: gh CLI が認証されていません。"
        echo ""
        echo "以下のコマンドで認証してください:"
        echo "  gh auth login"
        exit 1
    fi
}

# リポジトリルートから実行されているか確認
check_repository_root() {
    local git_root
    git_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
        echo "❌ エラー: gitリポジトリ内で実行してください。"
        exit 1
    }

    local current_dir
    current_dir=$(pwd -P)

    # worktree内からの実行は許可（後続のcheck_current_directoryで制御）
    # ただし、scriptsディレクトリが存在するか確認
    if [[ ! -f "$git_root/scripts/cleanup-worktrees.sh" ]]; then
        echo "❌ エラー: リポジトリルートまたはworktreeから実行してください。"
        echo "  現在地: $current_dir"
        echo "  期待: $git_root または配下のworktree"
        exit 1
    fi
}

# 自己削除防止チェック: カレントディレクトリがworktree内かどうかを確認
check_current_directory() {
    local current_dir
    current_dir=$(pwd -P 2>/dev/null) || {
        echo "❌ エラー: カレントディレクトリを取得できません。"
        echo "シェルが破損している可能性があります。"
        exit 1
    }

    local main_worktree
    main_worktree=$(git worktree list --porcelain | grep "^worktree " | head -1 | sed 's/^worktree //')
    if [[ -z "$main_worktree" || ! -d "$main_worktree" ]]; then
        echo "❌ エラー: メインworktreeのパスを取得できませんでした。"
        echo "  gitリポジトリ内で実行しているか、worktreeが正しく設定されているか確認してください。"
        exit 1
    fi

    # カレントディレクトリが他のworktree配下の場合はエラー
    # NOTE: メインworktree配下にネストされたworktreeも存在するため、
    #       先にworktreeリストをチェックする必要がある
    local worktree_list
    worktree_list=$(git worktree list --porcelain | grep "^worktree " | sed 's/^worktree //' | tail -n +2) || true
    while IFS= read -r worktree_path; do
        [[ -z "$worktree_path" ]] && continue
        # 末尾のスラッシュを除去して比較
        local norm_current_dir="${current_dir%/}"
        local norm_worktree_path="${worktree_path%/}"
        # パス境界チェック: 完全一致または配下のパスの場合のみマッチ
        # 例: /repo/feature-123 は /repo/feature-123-backup にマッチしない
        if [[ "$norm_current_dir" == "$norm_worktree_path" || "$norm_current_dir" == "$norm_worktree_path/"* ]]; then
            echo "❌ エラー: カレントディレクトリは削除対象のworktree内です。"
            echo "  現在地: $current_dir"
            echo "  worktree: $worktree_path"
            echo ""
            echo "メインリポジトリに移動してから再実行してください:"
            echo "  cd $main_worktree && ./scripts/cleanup-worktrees.sh --force"
            exit 1
        fi
    done <<< "$worktree_list"

    # worktreeリストに該当しなければ安全（メインworktree配下か外部）
}

# スクリプト開始時に前提条件と自己削除防止チェックを実行
check_prerequisites
check_repository_root
check_current_directory

# メインworktree以外を取得
get_worktrees() {
    # sedを使用: パスに空白が含まれる場合でも正しく処理できる
    git worktree list --porcelain | grep "^worktree " | sed 's/^worktree //' | tail -n +2
}

# GitHub APIコールをリトライ付きで実行
# Usage: gh_with_retry <gh_command_args...>
# リトライ対象: ネットワークエラー（終了コード1以外）
# 即座に失敗: PRが見つからないなど（終了コード1）
gh_with_retry() {
    local max_retries=3
    local retry_delay=2
    local attempt=1
    local output
    local exit_code

    while [[ $attempt -le $max_retries ]]; do
        output=$(gh "$@" 2>&1) && {
            echo "$output"
            return 0
        }
        exit_code=$?

        # 終了コード1は通常のエラー（PRが見つからないなど）、リトライしない
        # 終了コード2以上はネットワークエラーやシステムエラーの可能性があるのでリトライ
        if [[ $exit_code -eq 1 ]]; then
            echo ""
            return 1
        fi

        # リトライ可能なエラーの場合
        if [[ $attempt -lt $max_retries ]]; then
            sleep $retry_delay
            retry_delay=$((retry_delay * 2))
            attempt=$((attempt + 1))
        else
            # 最大リトライ回数に達した
            echo ""
            return 1
        fi
    done
}

# ブランチ名からPR番号を取得（--head で直接検索、ページネーション問題を回避）
get_pr_number() {
    local branch="$1"
    local result
    # set -e 環境下でも gh_with_retry の失敗でスクリプトが終了しないよう || true を付与
    result=$(gh_with_retry pr list --state all --head "$branch" --json number --jq '.[0].number // empty') || true
    # nullや空の場合は空文字を返す
    if [[ -z "$result" || "$result" == "null" ]]; then
        echo ""
    else
        echo "$result"
    fi
}

# PRの状態を取得（失敗時は空文字を返す）
get_pr_state() {
    local pr_number="$1"
    # set -e 環境下でも gh_with_retry の失敗でスクリプトが終了しないよう || true を付与
    gh_with_retry pr view "$pr_number" --json state --jq '.state' || true
}

echo "=== Worktree クリーンアップ ==="
echo ""

worktrees=$(get_worktrees)
if [[ -z "$worktrees" ]]; then
    echo "クリーンアップ対象のworktreeはありません。"
    exit 0
fi

# 削除対象を並列配列で管理（Bash 3.2互換、パス名に特殊文字が含まれる場合も安全）
to_delete_paths=()
to_delete_branches=()
to_delete_pr_numbers=()
to_keep=()

while IFS= read -r worktree_path; do
    [[ -z "$worktree_path" ]] && continue

    worktree_name=$(basename "$worktree_path")
    branch=$(git -C "$worktree_path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")

    pr_number=$(get_pr_number "$branch")
    if [[ -z "$pr_number" ]]; then
        echo "⚠️  $worktree_name ($branch) - 関連するPRが見つかりません（ローカル専用ブランチの可能性）"
        to_keep+=("$worktree_path")
        continue
    fi

    pr_state=$(get_pr_state "$pr_number")

    case "$pr_state" in
        MERGED|CLOSED)
            echo "🗑️  $worktree_name ($branch) - PR #$pr_number $pr_state → 削除対象"
            to_delete_paths+=("$worktree_path")
            to_delete_branches+=("$branch")
            to_delete_pr_numbers+=("$pr_number")
            ;;
        OPEN)
            echo "✅ $worktree_name ($branch) - PR #$pr_number OPEN → 保持"
            to_keep+=("$worktree_path")
            ;;
        "")
            echo "⚠️  $worktree_name ($branch) - PR #$pr_number の状態を取得できませんでした（ネットワークエラーの可能性）"
            to_keep+=("$worktree_path")
            ;;
        *)
            echo "❓ $worktree_name ($branch) - PR #$pr_number 状態不明: $pr_state"
            to_keep+=("$worktree_path")
            ;;
    esac
done <<< "$worktrees"

echo ""
echo "=== サマリー ==="
echo "削除対象: ${#to_delete_paths[@]}件"
echo "保持: ${#to_keep[@]}件"
echo ""

if [[ ${#to_delete_paths[@]} -eq 0 ]]; then
    echo "削除対象はありません。"
    exit 0
fi

if [[ "$FORCE" != true ]]; then
    echo "実際に削除するには --force オプションを付けて実行してください:"
    echo "  ./scripts/cleanup-worktrees.sh --force"
    exit 0
fi

echo "=== 削除実行 ==="
partial_failure=false

# 並列配列の整合性チェック（防御的プログラミング）
# shellcheck disable=SC2055
# SC2055 警告を無効化: shellcheckは「!= A || != B は常にtrue」パターンを検出するが、
# このコードは「-ne A || -ne B」で「いずれかが不一致ならエラー」という正しい意図。
# 3つの配列の長さが全て一致しなければエラーとする防御的チェック。
if [[ ${#to_delete_paths[@]} -ne ${#to_delete_branches[@]} || \
      ${#to_delete_paths[@]} -ne ${#to_delete_pr_numbers[@]} ]]; then
    echo "❌ 内部エラー: 配列の長さが一致しません"
    exit 1
fi

for i in "${!to_delete_paths[@]}"; do
    worktree_path="${to_delete_paths[$i]}"
    branch="${to_delete_branches[$i]}"
    pr_number="${to_delete_pr_numbers[$i]}"
    worktree_name=$(basename "$worktree_path")

    echo "削除中: $worktree_name..."

    # Worktree削除
    # NOTE: worktreeはlock機構により保護されている場合があるため、
    #       削除前にunlockを試行する。lockは誤削除防止のための保護機構。
    git worktree unlock "$worktree_path" 2>/dev/null || true
    if ! git worktree remove "$worktree_path" 2>/dev/null; then
        echo "  ⚠️  worktree削除失敗、強制削除を試行..."
        if ! git worktree remove --force "$worktree_path" 2>/dev/null; then
            echo "  ❌ worktree削除に失敗: $worktree_path"
            partial_failure=true
            # worktreeが削除できない場合はブランチ削除もスキップ
            # （worktreeがブランチを参照しているとブランチ削除も失敗するため）
            continue
        fi
    fi

    # ローカルブランチ削除
    branch_delete_output=""
    if branch_delete_output=$(git branch -D "$branch" 2>&1); then
        echo "  ✓ ローカルブランチ削除: $branch"
    else
        # git branch -D のエラーメッセージで原因を判定
        # "not found" = 既に削除済み、それ以外 = 削除失敗
        if [[ "$branch_delete_output" == *"not found"* ]]; then
            echo "  ⚠️  ローカルブランチ削除をスキップ（既に削除済み）"
        else
            echo "  ❌ ローカルブランチ削除に失敗: $branch"
            partial_failure=true
        fi
    fi

    # NOTE: リモートブランチはGitHubの"delete_branch_on_merge"設定により
    #       マージ時に自動削除されるため、ここでは削除しない

    echo "  ✅ 完了: $worktree_name (PR #$pr_number)"
done

echo ""
echo "=== クリーンアップ完了 ==="
git worktree list

if [[ "$partial_failure" == true ]]; then
    echo ""
    echo "⚠️  一部の操作が失敗しました。上記のログを確認してください。"
    exit 2
fi

exit 0
