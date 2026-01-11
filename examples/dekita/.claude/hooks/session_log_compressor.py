#!/usr/bin/env python3
"""セッション終了時にローテート済みログを圧縮。

Why:
    ログファイルのローテート後、古いファイル（.log.1, .log.2等）が
    ディスクを圧迫する。gzip圧縮してストレージを節約する。

What:
    - セッション終了時（Stop）に発火
    - execution/とmetrics/のローテート済みログを検索
    - .log.N形式のファイルをgzip圧縮
    - 圧縮した件数をログに記録

Remarks:
    - 非ブロック型（Stopフック）
    - ローテーション自体はcommon.pyが担当
    - 既に圧縮済み（.gz）のファイルはスキップ

Changelog:
    - silenvx/dekita#710: フック追加
"""

import json
import sys
from pathlib import Path

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from common import EXECUTION_LOG_DIR, METRICS_LOG_DIR
from lib.execution import compress_rotated_logs, log_hook_execution
from lib.session import parse_hook_input


def main():
    """Compress rotated logs at session end."""
    # Read hook input
    hook_input = parse_hook_input()

    # Skip if Stop hook is already active (prevent infinite loop)
    if hook_input.get("stop_hook_active"):
        print(json.dumps({"decision": "approve"}))
        return

    # Compress rotated logs in both directories
    total_compressed = 0
    total_compressed += compress_rotated_logs(EXECUTION_LOG_DIR)
    total_compressed += compress_rotated_logs(METRICS_LOG_DIR)

    # Log the result
    log_hook_execution(
        "session-log-compressor",
        "approve",
        f"Compressed {total_compressed} rotated log file(s)",
        {"compressed_count": total_compressed},
    )

    # Always approve - don't block session end
    print(json.dumps({"decision": "approve"}))


if __name__ == "__main__":
    main()
