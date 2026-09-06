# -*- coding: utf-8 -*-
"""并发执行器（批量查询/批量导出共用，prompt 2026-09-06 新增「并发数」）。

设计要点：
- ThreadPoolExecutor 固定 worker 数（默认 DEFAULT_CONCURRENCY=5）；
- **结果与异常保持输入顺序**（与串行实现一致，清单/表格顺序稳定）；
- 单项失败不中断整体（异常捕获后占位，与既有行为一致）；
- worker 内的异常完整透传给调用方决定占位形式（错误字符串）。

共享 VolcAIBotClient 并发说明：requests.Session 的底层 urllib3 连接池
线程安全，查询/导出均为幂等 GET，实测并发 5 无错误（见 test 与 2026-09-06
验证）；并发下账号守卫/Cookie 刷新有锁保护。

DEFAULT_CONCURRENCY 的取值依据（2026-09-06 实测，脚本测试项目组 9 个剧本
批量导出）：串行 9 请求约 26s；并发 5 约 7s；并发 8 约 6.5s、无明显错误，
但已接近单账号请求速率上限（服务端有频控风险，长时间并发 8+ 建议先观察
日志中是否有 429/超时）。取 5 = 速度收益 ~3.7x 与安全的折中，UI 可调
（1~10），MCP/脚本可传参覆盖。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, TypeVar

T = TypeVar("T")      # 输入项类型
R = TypeVar("R")      # 结果类型（含错误占位）

DEFAULT_CONCURRENCY = 5
MAX_CONCURRENCY = 10


def clamp_concurrency(value: int | None) -> int:
    """并发数入参归一：None/0/非法 -> 默认 5；范围 1~10。"""
    try:
        v = int(value or 0)
    except (TypeError, ValueError):
        v = 0
    if v <= 0:
        return DEFAULT_CONCURRENCY
    return min(v, MAX_CONCURRENCY)


def run_parallel(items: Iterable[T],
                 worker: Callable[[T], R],
                 concurrency: int | None = None,
                 progress: Callable[[T, int, int], None] | None = None,
                 total: int | None = None) -> list[R]:
    """并发执行 worker(items[i])，返回与输入同序的结果列表。

    - worker 异常不传播：结果为 ("error", 异常) 元组——调用方用
      isinstance(r, tuple) and r[0] == "error" 判定（约定简单，避免
      泛型异常包装类）；
    - progress(item, index, total)：每项开始前在 worker 线程回调
      （用于 WebSocket 执行状态上报）；
    - 单项或空输入退化为串行路径（无线程开销）。
    """
    items = list(items)
    n = total if total is not None else len(items)
    c = clamp_concurrency(concurrency)
    if not items or c == 1:
        out: list[R] = []
        for i, it in enumerate(items, 1):
            if progress is not None:
                progress(it, i, n)
            try:
                out.append(worker(it))
            except Exception as e:  # noqa: BLE001 - 单项失败不中断
                out.append(("error", e))   # type: ignore[arg-type]
        return out
    results: list[Any] = [None] * len(items)
    done_index = {"v": 0}

    def run(i: int) -> None:
        it = items[i]
        if progress is not None:
            progress(it, i + 1, n)
        try:
            results[i] = worker(it)
        except Exception as e:  # noqa: BLE001 - 单项失败不中断
            results[i] = ("error", e)

    with ThreadPoolExecutor(max_workers=c) as pool:
        list(pool.map(run, range(len(items))))   # map 保序提交
    return results
