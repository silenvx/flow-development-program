#!/usr/bin/env python3
"""フック実行時間の計測ユーティリティを提供する。

Why:
    フックのパフォーマンス分析のため、実行時間を自動計測し、
    ログに記録する仕組みを提供する。

What:
    - HookTimer: 手動タイミング計測用クラス
    - timed_hook(): デコレータで自動タイミング計測

Remarks:
    - time.perf_counter()使用で高精度計測
    - SystemExit/例外時も計測結果をログ出力
    - デコレータ使用時はlog_hook_execution()を二重呼び出ししない

Changelog:
    - silenvx/dekita#1882: フック実行時間計測を追加

Example:
    @timed_hook("my-hook-name")
    def main():
        # hook logic here
        pass

    if __name__ == "__main__":
        main()
"""

import functools
import time
from collections.abc import Callable
from typing import TypeVar

F = TypeVar("F", bound=Callable)


class HookTimer:
    """Simple timer for measuring hook execution time."""

    def __init__(self, hook_name: str):
        """Initialize timer with hook name.

        Args:
            hook_name: Name of the hook for logging purposes.
        """
        self.hook_name = hook_name
        self.start_time = time.perf_counter()

    def elapsed_ms(self) -> int:
        """Get elapsed time in milliseconds since timer start.

        Returns:
            Elapsed time in milliseconds (integer).
        """
        elapsed = time.perf_counter() - self.start_time
        return int(elapsed * 1000)

    def elapsed_seconds(self) -> float:
        """Get elapsed time in seconds since timer start.

        Returns:
            Elapsed time in seconds (float).
        """
        return time.perf_counter() - self.start_time


def timed_hook(hook_name: str) -> Callable[[F], F]:
    """Decorator to automatically time hook execution.

    This decorator wraps a hook's main function to:
    1. Start a timer before execution
    2. Log the execution time via log_hook_execution after completion

    Note: The decorated function should NOT call log_hook_execution itself,
    as this decorator will handle it.

    Example:
        @timed_hook("my-hook")
        def main():
            # Do hook work
            result = {"continue": True}
            print(json.dumps(result))
            return "approve"  # Return decision

        if __name__ == "__main__":
            main()

    Args:
        hook_name: Name of the hook for logging.

    Returns:
        Decorator function.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Import here to avoid circular imports
            from lib.execution import log_hook_execution

            timer = HookTimer(hook_name)
            try:
                result = func(*args, **kwargs)
                # If function returns a decision string, use it
                decision = result if isinstance(result, str) else "approve"
                log_hook_execution(
                    hook_name,
                    decision,
                    duration_ms=timer.elapsed_ms(),
                )
                return result
            except SystemExit as e:
                # Handle sys.exit() calls (common in hooks for early termination)
                # Log timing before re-raising to preserve exit code
                # Exit code 0 or None = success, non-zero = block/error
                decision = "approve" if (e.code == 0 or e.code is None) else "block"
                log_hook_execution(
                    hook_name,
                    decision,
                    duration_ms=timer.elapsed_ms(),
                )
                raise
            except Exception:
                # On error, still try to log timing
                log_hook_execution(
                    hook_name,
                    "error",
                    duration_ms=timer.elapsed_ms(),
                    details={"error": "exception_raised"},
                )
                raise

        return wrapper  # type: ignore[return-value]

    return decorator


__all__ = ["HookTimer", "timed_hook"]
