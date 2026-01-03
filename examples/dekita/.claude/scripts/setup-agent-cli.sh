#!/usr/bin/env bash
# Agent CLI（Gemini/Codex）の初期セットアップ。
#
# Why:
#     Gemini/Codex CLIのデフォルト設定を適用し、
#     404エラー等の初期設定問題を回避するため。
#
# What:
#     - setup_gemini_cli(): Gemini設定ファイルを作成/更新
#     - verify_gemini_cli(): 動作確認テストを実行
#     - setup_codex_cli(): Codex CLIの存在確認
#
# Remarks:
#     - Usage: .claude/scripts/setup-agent-cli.sh [--verify]
#     - ~/.gemini/settings.jsonにモデル設定を書き込む
#     - jqがある場合は既存設定を保持して更新
#
# Changelog:
#     - silenvx/dekita#1700: Agent CLIセットアップ機能を追加

set -euo pipefail

# 色付き出力
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

# 最新の安定版モデル（定期的に更新が必要）
# 参考: https://ai.google.dev/gemini-api/docs/models
GEMINI_MODEL="gemini-2.5-pro"

setup_gemini_cli() {
    info "Gemini CLI のセットアップを開始..."

    local gemini_dir="$HOME/.gemini"
    local settings_file="$gemini_dir/settings.json"

    # ディレクトリが存在しない場合は作成
    if [[ ! -d "$gemini_dir" ]]; then
        mkdir -p "$gemini_dir"
        info "作成: $gemini_dir"
    fi

    # 設定ファイルが存在しない場合は新規作成
    if [[ ! -f "$settings_file" ]]; then
        cat > "$settings_file" << EOF
{
  "model": {
    "name": "${GEMINI_MODEL}"
  }
}
EOF
        info "作成: $settings_file (モデル: ${GEMINI_MODEL})"
        return 0
    fi

    # 既存の設定ファイルを確認
    if command -v jq &> /dev/null; then
        local current_model
        current_model=$(jq -r '.model.name // empty' "$settings_file" 2>/dev/null || echo "")

        if [[ -z "$current_model" ]]; then
            # model.nameが未設定の場合、追加（既存のmodel設定は保持）
            local tmp_file
            tmp_file=$(mktemp)
            # mv失敗時に一時ファイルをクリーンアップ
            trap 'rm -f "$tmp_file"' EXIT
            jq --arg model "$GEMINI_MODEL" '.model.name = $model' "$settings_file" > "$tmp_file"
            mv "$tmp_file" "$settings_file"
            trap - EXIT
            info "更新: $settings_file (モデル: ${GEMINI_MODEL})"
        elif [[ "$current_model" != "$GEMINI_MODEL" ]]; then
            warn "現在のモデル: $current_model"
            warn "推奨モデル: $GEMINI_MODEL"
            warn "変更する場合: jq '.model.name = \"${GEMINI_MODEL}\"' $settings_file | sponge $settings_file"
        else
            info "設定済み: $settings_file (モデル: ${GEMINI_MODEL})"
        fi
    else
        warn "jqがインストールされていません。手動で設定を確認してください。"
        warn "設定ファイル: $settings_file"
        warn "推奨設定: {\"model\": {\"name\": \"${GEMINI_MODEL}\"}}"
    fi
}

verify_gemini_cli() {
    info "Gemini CLI の動作確認..."

    if ! command -v gemini &> /dev/null; then
        warn "gemini コマンドが見つかりません。インストールしてください。"
        warn "インストール: npm install -g @google/gemini-cli"
        return 1
    fi

    # 簡単なテスト（macOS互換のタイムアウト処理）
    local tmp_file
    tmp_file=$(mktemp)
    trap 'rm -f "$tmp_file"' EXIT

    # サブシェルでgeminiを実行し、30秒後にkill
    # trap 'kill 0' で同一プロセスグループの全プロセスを終了
    (
        trap 'kill 0 2>/dev/null' EXIT
        echo "test" | gemini "Reply with OK" > "$tmp_file" 2>&1 &
        local pid=$!
        (
            sleep 30
            kill "$pid" 2>/dev/null
        ) &
        wait "$pid" 2>/dev/null
    ) || true

    local result
    result=$(cat "$tmp_file" 2>/dev/null || echo "")
    rm -f "$tmp_file"
    trap - EXIT

    if echo "$result" | grep -qi "ok"; then
        info "Gemini CLI: 正常動作"
    else
        warn "Gemini CLI: 動作確認に失敗しました"
        warn "手動で確認: echo 'test' | gemini 'Reply with OK'"
        warn "上記コマンドでも失敗する場合は、Gemini CLI の API キー設定やネットワーク接続を確認してください。"
        warn "詳細な手順やトラブルシューティングについては、リポジトリ直下の TROUBLESHOOTING.md を参照してください。"
        return 1
    fi
}

setup_codex_cli() {
    info "Codex CLI のセットアップを確認..."

    if ! command -v codex &> /dev/null; then
        warn "codex コマンドが見つかりません。"
        warn "インストール方法: Codex CLI の公式ドキュメントを確認してください（npm パッケージ名は変更される可能性があります）。"
        # Codexは任意なのでエラーにしない
        return 0
    fi

    info "Codex CLI: インストール済み"
}

main() {
    echo "========================================"
    echo "Agent CLI セットアップスクリプト"
    echo "========================================"
    echo ""

    setup_gemini_cli
    echo ""

    setup_codex_cli
    echo ""

    if [[ "${1:-}" == "--verify" ]]; then
        verify_gemini_cli
        echo ""
    fi

    info "セットアップ完了"
    echo ""
    echo "使用方法:"
    echo "  Gemini: echo 'your prompt' | gemini 'instruction'"
    echo "  Codex:  codex exec 'instruction'"
}

main "$@"
