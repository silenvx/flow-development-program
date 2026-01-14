#!/bin/bash
# リベース/amend後にCodexレビューマーカーを自動更新する。
#
# Why:
#     リベースでコミットハッシュが変わった際に
#     Codexレビュー記録を自動更新し、手動更新を不要にするため。
#
# What:
#     - sanitize_branch_name(): ブランチ名をファイル名用にサニタイズ
#     - マーカーファイル更新: branch:commit:diff_hash形式で保存
#
# State:
#     - reads: .claude/logs/markers/codex-review-*.done
#     - writes: .claude/logs/markers/codex-review-*.done
#
# Remarks:
#     - lefthook post-rewriteから呼び出される
#     - rebaseとamend両方で実行される
#     - main/masterブランチはスキップ
#     - detached HEAD時はスキップ
#
# Changelog:
#     - silenvx/dekita#802: post-rewrite自動更新機能を追加
#     - silenvx/dekita#811: detached HEADスキップを追加
#     - silenvx/dekita#813: マーカーファイル仕様を定義
#     - silenvx/dekita#1057: diff_hash保存を追加

set -euo pipefail

# マーカーファイルのディレクトリ（common.pyのMARKERS_LOG_DIRと同じ）
MARKERS_DIR=".claude/logs/markers"

# 現在のブランチ名を取得
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")

# ブランチ名が取得できない場合はスキップ
if [[ -z "$BRANCH" ]]; then
    exit 0
fi

# main/masterブランチはスキップ（マーカー対象外）
if [[ "$BRANCH" == "main" || "$BRANCH" == "master" ]]; then
    exit 0
fi

# Issue #811: detached HEAD時はスキップ
# リベース中間コミットではHEADがdetachedになることがある
# git rev-parse --abbrev-ref HEAD は detached時に "HEAD" を返す
if [[ "$BRANCH" == "HEAD" ]]; then
    exit 0
fi

# ブランチ名をサニタイズ
# common.pyのsanitize_branch_name()と同じロジック:
# - / \ : < > " | ? * を - に置換
# - スペースを _ に置換
# - 連続する - を単一の - に圧縮
# - 先頭/末尾の - を削除
sanitize_branch_name() {
    local branch="$1"
    # Replace / and \ with -
    branch="${branch//\//-}"
    branch="${branch//\\/-}"
    # Replace : < > " | ? * with - using sed
    # shellcheck disable=SC2001 # Can't use ${var//pattern} for character class replacement
    branch=$(echo "$branch" | sed 's/[:<>"|?*]/-/g')
    # Replace spaces with _
    branch="${branch// /_}"
    # Remove consecutive dashes (use -E for extended regex on macOS)
    branch=$(echo "$branch" | sed -E 's/-+/-/g')
    # Remove leading/trailing dashes
    branch="${branch#-}"
    branch="${branch%-}"
    echo "$branch"
}

SAFE_BRANCH=$(sanitize_branch_name "$BRANCH")
MARKER_FILE="$MARKERS_DIR/codex-review-$SAFE_BRANCH.done"

# マーカーファイルが存在しない場合はスキップ
# （まだCodexレビューを実行していないブランチ）
if [[ ! -f "$MARKER_FILE" ]]; then
    exit 0
fi

# 新しいHEADコミットを取得（ショートハッシュ、common.pyのget_head_commit()と同じ）
NEW_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "")

if [[ -z "$NEW_COMMIT" ]]; then
    echo "[post-rewrite] Warning: Could not get HEAD commit" >&2
    exit 0
fi

# 現在のマーカー内容を読み取り
OLD_CONTENT=$(cat "$MARKER_FILE" 2>/dev/null || echo "")

# ディレクトリが存在することを確認（通常は存在するはずだが念のため）
mkdir -p "$MARKERS_DIR"

# Issue #1057: リベース後もdiff_hashを保存してCodexレビュースキップを有効化
# common.py の get_diff_hash() と同等のロジック: git diff main | sha256 | first 12 chars
# shasum -a 256 は macOS/Linux 両対応
# 注意: mainブランチが存在しない場合やエラー時は空文字を設定（非致命的）
# git diff の終了コード: 0=差分なし, 1=差分あり, 128以上=エラー
DIFF_OUTPUT=$(git diff main 2>/dev/null)
DIFF_EXIT_CODE=$?
if [[ $DIFF_EXIT_CODE -le 1 ]]; then
  # git diff成功（0=差分なし, 1=差分あり）→ 出力をハッシュ化
  DIFF_HASH=$(echo "$DIFF_OUTPUT" | shasum -a 256 | cut -c1-12)
else
  # git diffエラー（mainブランチなし等）→ 空文字
  DIFF_HASH=""
fi

# マーカーファイルを更新
# Issue #813: マーカーファイル仕様
# - ファイル名: サニタイズされたブランチ名（$SAFE_BRANCH）を使用
# - ファイル内容: 元のブランチ名（$BRANCH）を使用（正確な識別のため）
# - フォーマット: branch:commit:diff_hash (Issue #841)
# 例: ファイル名 "codex-review-feat-issue-123.done", 内容 "feat/issue-123:abc1234:def567890123"
echo "$BRANCH:$NEW_COMMIT:$DIFF_HASH" > "$MARKER_FILE"

# 情報出力はstderrへ（lefthookの出力を汚染しない）
echo "[post-rewrite] Codex review marker updated:" >&2
echo "  File: $MARKER_FILE" >&2
echo "  Old:  $OLD_CONTENT" >&2
echo "  New:  $BRANCH:$NEW_COMMIT:$DIFF_HASH" >&2
