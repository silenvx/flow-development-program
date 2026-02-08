/**
 * Bun用の非同期スポーン関数。
 *
 * spawnSyncの代替として使用し、イベントループをブロックしない。
 * 複数コマンドの並列実行（Promise.all）が可能になる。
 */

export interface SpawnResult {
  stdout: string;
  stderr: string;
  exitCode: number;
  success: boolean;
}

export interface SpawnOptions {
  cwd?: string;
  env?: Record<string, string>;
  timeout?: number;
}

/**
 * 非同期でコマンドを実行する。
 *
 * @param command - 実行するコマンド
 * @param args - コマンドの引数
 * @param options - オプション（cwd, env, timeout）
 * @returns SpawnResult
 */
export async function asyncSpawn(
  command: string,
  args: string[],
  options: SpawnOptions = {},
): Promise<SpawnResult> {
  const { cwd, env, timeout = 30000 } = options;

  let proc: ReturnType<typeof Bun.spawn>;
  try {
    proc = Bun.spawn([command, ...args], {
      cwd,
      env: env ? { ...process.env, ...env } : undefined,
      stdout: "pipe",
      stderr: "pipe",
    });
  } catch (error) {
    // コマンドが見つからない場合など
    const message = error instanceof Error ? error.message : String(error);
    return {
      stdout: "",
      stderr: `Command failed: ${message}`,
      exitCode: -1,
      success: false,
    };
  }

  // タイムアウト処理（完了後にクリアするためIDを保持）
  let timeoutId: ReturnType<typeof setTimeout> | undefined;

  const timeoutPromise = new Promise<never>((_, reject) => {
    timeoutId = setTimeout(() => {
      proc.kill();
      reject(new Error(`Command timed out after ${timeout}ms: ${command} ${args.join(" ")}`));
    }, timeout);
  });

  try {
    // Type assertion: When stdout/stderr are set to "pipe", they are ReadableStream
    const [exitCode, stdout, stderr] = await Promise.race([
      Promise.all([
        proc.exited,
        new Response(proc.stdout as ReadableStream<Uint8Array>).text(),
        new Response(proc.stderr as ReadableStream<Uint8Array>).text(),
      ]),
      timeoutPromise,
    ]);

    return {
      stdout,
      stderr,
      exitCode,
      success: exitCode === 0,
    };
  } catch (error) {
    if (error instanceof Error && error.message.includes("timed out")) {
      // タイムアウト時はプロセス終了を待機してから結果を返す
      await proc.exited;

      return {
        stdout: "",
        stderr: error.message,
        exitCode: -1,
        success: false,
      };
    }
    throw error;
  } finally {
    // タイムアウトをクリア（リソースリーク防止）
    if (timeoutId) clearTimeout(timeoutId);
  }
}

/**
 * 複数コマンドを並列実行する。
 *
 * @param commands - 実行するコマンドの配列
 * @returns SpawnResult の配列
 */
export async function asyncSpawnAll(
  commands: Array<{ command: string; args: string[]; options?: SpawnOptions }>,
): Promise<SpawnResult[]> {
  return Promise.all(
    commands.map(({ command, args, options }) => asyncSpawn(command, args, options)),
  );
}
