#!/bin/bash
# 開発サーバー（Worker+Frontend）を起動する。
#
# Why:
#     Workerのポートを自動検出し、Frontendのプロキシ設定を
#     自動更新して開発環境を簡単に起動するため。
#
# What:
#     - Worker起動: pnpm run dev:worker
#     - ポート自動検出: ログからポート番号を抽出
#     - Frontend起動: .env.localを更新してpnpm run dev:frontend
#
# Remarks:
#     - Ctrl+Cで両プロセスを終了
#     - .env.localにVITE_API_URLを書き込む
#
# Changelog:
#     - silenvx/dekita#120: 開発サーバー起動スクリプトを追加

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
ENV_LOCAL="$FRONTEND_DIR/.env.local"

# 色付き出力
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 一時ファイルでworkerの出力をキャプチャ
WORKER_LOG=$(mktemp)
WORKER_PID=""
FRONTEND_PID=""

# クリーンアップ関数
cleanup() {
    if [[ -n "$WORKER_PID" ]]; then
        kill "$WORKER_PID" 2>/dev/null || true
    fi
    if [[ -n "$FRONTEND_PID" ]]; then
        kill "$FRONTEND_PID" 2>/dev/null || true
    fi
    rm -f "$WORKER_LOG"
}
trap cleanup EXIT

echo -e "${GREEN}Starting development servers...${NC}"

# Workerを起動してポートを検出
echo -e "${YELLOW}Starting worker and detecting port...${NC}"

# Workerをバックグラウンドで起動
pnpm --filter @dekita/worker dev > "$WORKER_LOG" 2>&1 &
WORKER_PID=$!

# ポートが出力されるまで待機（最大30秒）
PORT=""
for _ in {1..30}; do
    # Workerプロセスが終了していないか確認
    if ! kill -0 "$WORKER_PID" 2>/dev/null; then
        echo -e "${RED}Error: Worker process crashed${NC}"
        echo -e "${YELLOW}Worker output:${NC}"
        cat "$WORKER_LOG"
        exit 1
    fi

    if grep -q "Ready on http://localhost:" "$WORKER_LOG" 2>/dev/null; then
        # 最初のマッチのみ取得
        PORT=$(grep "Ready on http://localhost:" "$WORKER_LOG" | head -n 1 | sed -E 's/.*localhost:([0-9]+).*/\1/')
        break
    fi
    sleep 1
done

# ポートの検証（数値かつ有効な範囲）
if [[ -z "$PORT" ]]; then
    echo -e "${RED}Error: Could not detect worker port (timeout)${NC}"
    echo -e "${YELLOW}Worker output:${NC}"
    cat "$WORKER_LOG"
    exit 1
fi

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [[ "$PORT" -lt 1 ]] || [[ "$PORT" -gt 65535 ]]; then
    echo -e "${RED}Error: Invalid port detected: $PORT${NC}"
    exit 1
fi

echo -e "${GREEN}Worker started on port $PORT${NC}"

# .env.local を更新（既存の設定を保持しつつVITE_API_PROXY_TARGETのみ更新）
if [[ -f "$ENV_LOCAL" ]]; then
    # 既存ファイルがある場合、VITE_API_PROXY_TARGETの行を更新または追加
    if grep -q "^VITE_API_PROXY_TARGET=" "$ENV_LOCAL"; then
        sed -i.bak "s|^VITE_API_PROXY_TARGET=.*|VITE_API_PROXY_TARGET=http://localhost:$PORT|" "$ENV_LOCAL"
        rm -f "$ENV_LOCAL.bak"
    else
        echo "VITE_API_PROXY_TARGET=http://localhost:$PORT" >> "$ENV_LOCAL"
    fi
else
    echo "VITE_API_PROXY_TARGET=http://localhost:$PORT" > "$ENV_LOCAL"
fi
echo -e "${GREEN}Updated $ENV_LOCAL with proxy target: http://localhost:$PORT${NC}"

# Viteが.env.localを読み込めるよう少し待機
sleep 1

# Frontendを起動（バックグラウンド）
echo -e "${YELLOW}Starting frontend...${NC}"
pnpm --filter @dekita/frontend dev &
FRONTEND_PID=$!

# 両方のプロセスを監視（どちらかが終了したら両方停止）
while true; do
    if ! kill -0 "$WORKER_PID" 2>/dev/null; then
        echo -e "${RED}Worker process exited${NC}"
        break
    fi
    if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
        echo -e "${RED}Frontend process exited${NC}"
        break
    fi
    sleep 2
done
