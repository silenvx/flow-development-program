# Python subprocess ↔ Bun spawnSync 互換性検証

Issue #2814: TypeScript移行におけるsubprocess互換性の検証結果。

## 概要

Pythonフック（189ファイル）の多くが `subprocess.run()` を使用している。TypeScript移行時に `Bun.spawnSync()` で同等の機能を実現できるか検証する。

---

## 1. 基本的な対応関係

### 1.1 同期実行

| 機能 | Python | Bun |
|------|--------|-----|
| 基本実行 | `subprocess.run(["cmd", "arg"])` | `Bun.spawnSync(["cmd", "arg"])` |
| 標準入力 | `input="data"` | `stdin: "data"` |
| 出力キャプチャ | `capture_output=True` | デフォルトで有効 |
| テキストモード | `text=True` | `.toString()` で変換 |
| タイムアウト | `timeout=N` (秒) | `timeout: N*1000` (ミリ秒、※プロジェクト定数は両言語とも秒単位) |
| 作業ディレクトリ | `cwd="/path"` | `cwd: "/path"` |

### 1.2 結果オブジェクト

| 情報 | Python | Bun |
|------|--------|-----|
| 終了コード | `result.returncode` | `result.exitCode` |
| 標準出力 | `result.stdout` (str) | `result.stdout.toString()` |
| 標準エラー | `result.stderr` (str) | `result.stderr.toString()` |
| 成功判定 | `result.returncode == 0` | `result.success` |

---

## 2. タイムアウト処理

### 2.1 動作の違い

| 観点 | Python | Bun |
|------|--------|-----|
| タイムアウト単位 | **秒** | **ミリ秒** |
| タイムアウト検出 | 例外 `TimeoutExpired` を送出 | `exitedDueToTimeout: true` を設定 |
| 終了シグナル | `SIGKILL` (デフォルト) | `SIGTERM` (デフォルト) |

### 2.2 Python実装

```python
from lib.constants import TIMEOUT_LIGHT  # 5秒

try:
    result = subprocess.run(
        ["git", "status"],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_LIGHT,
    )
    if result.returncode == 0:
        return result.stdout.strip()
except subprocess.TimeoutExpired:
    # タイムアウト時の処理
    return None
```

### 2.3 TypeScript（Bun）実装

```typescript
import { TIMEOUT_LIGHT } from "./constants";  // 5秒

const result = Bun.spawnSync(["git", "status"], {
  timeout: TIMEOUT_LIGHT * 1000,  // 秒→ミリ秒に変換
});

if (result.exitedDueToTimeout) {
  // タイムアウト時の処理
  return null;
}

if (result.exitCode === 0) {
  return result.stdout.toString().trim();
}
return null;
```

### 2.4 変換時の注意点

1. **単位変換必須**: Python秒 → Bun ミリ秒（×1000）
2. **エラーハンドリング変更**: try-catch → if文
3. **終了シグナル**: 必要に応じて `killSignal: "SIGKILL"` を指定

---

## 3. エラーハンドリング

### 3.1 Python実装パターン（fail-open）

```python
def get_current_branch() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_LIGHT,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        # すべての例外を無視（fail-open）
        pass
    return None
```

### 3.2 TypeScript（Bun）実装パターン

```typescript
function getCurrentBranch(): string | null {
  try {
    const result = Bun.spawnSync(["git", "rev-parse", "--abbrev-ref", "HEAD"], {
      timeout: TIMEOUT_LIGHT * 1000,  // 秒→ミリ秒に変換
    });

    if (result.exitedDueToTimeout) {
      return null;
    }

    if (result.exitCode === 0) {
      return result.stdout.toString().trim();
    }
  } catch {
    // fail-open: エラー時はnull
  }
  return null;
}
```

### 3.3 例外の種類

| Python例外 | Bun対応 |
|-----------|--------|
| `subprocess.TimeoutExpired` | `result.exitedDueToTimeout === true` |
| `FileNotFoundError` | `try-catch`で捕捉（コマンドが見つからない場合、例外を送出） |
| `OSError` | try-catchで捕捉 |

---

## 4. 実装上の差異

### 4.1 `check=True` オプション

Python:
```python
# 非ゼロ終了で CalledProcessError を送出
result = subprocess.run(["cmd"], check=True)
```

Bun:
```typescript
// 同等機能なし。手動でチェックする
const result = Bun.spawnSync(["cmd"]);
if (!result.success) {
  throw new Error(`Command failed: ${result.exitCode}`);
}
```

### 4.2 `check=False` オプション（デフォルト）

両言語ともデフォルトで終了コードを無視。差異なし。

### 4.3 環境変数

Python:
```python
result = subprocess.run(["cmd"], env={"KEY": "value"})
```

Bun:
```typescript
const result = Bun.spawnSync(["cmd"], {
  env: { KEY: "value" },
});
```

---

## 5. 互換レイヤー提案

### 5.1 TypeScript用ラッパー関数

```typescript
// .claude/hooks/lib/subprocess.ts

export interface RunResult {
  returncode: number;
  stdout: string;
  stderr: string;
  success: boolean;
  timedOut: boolean;
}

export interface RunOptions {
  timeout?: number;  // 秒（内部でミリ秒に変換）
  cwd?: string;
  env?: Record<string, string>;
}

/**
 * Python subprocess.run() と互換性のある関数。
 * 注意: コマンドが見つからない等でプロセスの起動に失敗した場合、例外がスローされます。
 */
export function run(cmd: string[], options: RunOptions = {}): RunResult {
  const result = Bun.spawnSync(cmd, {
    timeout: options.timeout ? options.timeout * 1000 : undefined,
    cwd: options.cwd,
    env: options.env,
  });

  return {
    returncode: result.exitCode ?? -1,
    stdout: result.stdout?.toString() ?? "",
    stderr: result.stderr?.toString() ?? "",
    success: result.success,
    timedOut: result.exitedDueToTimeout === true,
  };
}

/**
 * タイムアウト例外を模倣するバージョン
 */
export function runWithTimeoutException(
  cmd: string[],
  options: RunOptions = {}
): RunResult {
  const result = run(cmd, options);
  if (result.timedOut) {
    throw new TimeoutError(`Command timed out: ${cmd.join(" ")}`);
  }
  return result;
}

export class TimeoutError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TimeoutError";
  }
}
```

### 5.2 定数について

**重要**: プロジェクトの定数は両言語とも**秒単位**で統一されています。

```typescript
// .claude/hooks/lib/constants.ts（既存）
export const TIMEOUT_LIGHT = 5;    // 秒
export const TIMEOUT_MEDIUM = 10;  // 秒
export const TIMEOUT_HEAVY = 30;   // 秒
```

`Bun.spawnSync`呼び出し時に`× 1000`でミリ秒に変換してください。互換レイヤーの`run()`関数は内部で自動変換します。

---

## 6. テスト計画

### 6.1 単体テスト項目

| テスト | 内容 |
|-------|------|
| 基本実行 | コマンド実行と出力取得 |
| 終了コード | 0/非0の判定 |
| タイムアウト | 指定時間での打ち切り |
| 作業ディレクトリ | cwd指定での実行 |
| 環境変数 | env指定での実行 |
| コマンドエラー | 存在しないコマンドの処理 |

### 6.2 シャドウテスト

Python版とBun版で同じ入力を与え、出力を比較:

```bash
# Python版
echo '{"command": "git status"}' | python3 test_subprocess.py

# Bun版
echo '{"command": "git status"}' | bun run test_subprocess.ts

# 差分確認
diff <(python3 ...) <(bun run ...)
```

---

## 7. 検証結果サマリー

| 項目 | 互換性 | 備考 |
|------|--------|------|
| 基本実行 | ✅ 完全互換 | APIは異なるが同等機能 |
| 出力キャプチャ | ✅ 完全互換 | Bunはデフォルトで有効 |
| タイムアウト | ⚠️ 要変換 | 秒→ミリ秒、例外→プロパティ |
| 終了コード | ✅ 完全互換 | `returncode` → `exitCode` |
| エラーハンドリング | ⚠️ 要調整 | 例外パターンが異なる |
| 環境変数 | ✅ 完全互換 | 同じAPI |
| 作業ディレクトリ | ✅ 完全互換 | 同じAPI |

### 結論

**互換性あり**。以下の点に注意すれば移行可能:

1. `Bun.spawnSync`呼び出し時にタイムアウト値（秒）を×1000してミリ秒に変換
2. タイムアウト検出を例外→プロパティチェックに変更
3. `result.stdout` を `.toString()` で文字列変換
4. `result.returncode` を `result.exitCode` に変更（nullの場合は-1）

互換レイヤー（`subprocess.ts`）を作成することで、既存のPythonコードパターンをほぼそのまま移植可能。定数は両言語で秒単位のまま統一されているため、変換は互換レイヤー内で行います。

---

## 参照

- Python subprocess: https://docs.python.org/3/library/subprocess.html
- Bun子プロセス: https://bun.sh/docs/runtime/child-process
- 互換レイヤー仕様: `.claude/docs/hook-compatibility-spec.md`
