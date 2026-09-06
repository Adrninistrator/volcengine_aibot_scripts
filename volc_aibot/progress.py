# -*- coding: utf-8 -*-
"""进度事件总线（进程内发布订阅）。

用途（prompt 2026-09-06 新增要求）：
- 长耗时功能（快捷工具批量查询/批量导出、MCP 批量工具等）执行过程中，
  把「当前正在执行的请求 / 阶段，以及对应的剧本ID/剧本名称等参数」
  实时发布；
- web_server 的 WebSocket 端点订阅后广播给页面展示（见 /ws）；
- 每个功能**独立的展示**：事件带 source（"query"=批量查询剧本信息 /
  "export"=批量导出剧本 / "search"=搜索导出内容），页面按 source 分流到
  各工具自己的执行状态面板；无 source 的事件（普通 MCP 工具请求等）
  不进入快捷工具面板。

事件（dict，均含 event 与 ts）：
- request_start / request_done：单个 HTTP 请求发起/完成
  （what/method/path，完成时带 elapsed_ms/status/bytes）；
- stage：业务阶段推进（批量循环的每一步，带 script_id/script_name/
  group/index/total 等参数）；
- done：操作完成（清空面板的进行中状态，显示完成提示与总耗时
  elapsed_s；页面在用户未聚焦时闪烁标签页标题提醒）。

线程安全：发布方可能在 starlette 线程池（快捷工具重活）或事件循环线程，
订阅回调必须快速返回且不得抛异常（异常被隔离，不影响业务与其它订阅者）。

source 的线程上下文：批量操作经 set_source() 在**当前线程**标记功能来源，
该线程上发出的全部事件（含 client 的 HTTP 请求事件）自动携带 source；
run_parallel 的 worker 线程在 on_progress 回调里先 set_source 再上报，
使并发 worker 的请求事件同样归到正确功能。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

Listener = Callable[[dict], None]

_lock = threading.Lock()
_listeners: list[Listener] = []
_local = threading.local()          # 线程级功能来源（source）


def subscribe(fn: Listener) -> Callable[[], None]:
    """注册监听器，返回取消订阅函数。"""
    with _lock:
        if fn not in _listeners:
            _listeners.append(fn)

    def _unsubscribe() -> None:
        with _lock:
            if fn in _listeners:
                _listeners.remove(fn)
    return _unsubscribe


def set_source(source: str) -> None:
    """标记当前线程的功能来源（query/export/search；空=清除）。

    该线程上后续全部 report 自动携带 source。线程池线程会被复用，
    长流程入口必须先调用（worker 线程在 on_progress 回调中先 set_source
    再上报，见模块说明）。
    """
    _local.source = source or ""


def current_source() -> str:
    return getattr(_local, "source", "")


def report(event: str, message: str = "", **params: Any) -> None:
    """发布事件（无订阅者时为空操作，零开销）。"""
    with _lock:
        fns = list(_listeners)
    if not fns:
        return
    data = dict(params)
    data["event"] = event
    data["message"] = message
    data["ts"] = time.time()
    src = current_source()
    if src:
        data["source"] = src
    for fn in fns:
        try:
            fn(data)
        except Exception:  # noqa: BLE001 - 订阅者异常不影响发布方
            pass


def report_request_start(what: str, method: str, path: str) -> None:
    report("request_start", what, what=what, method=method, path=path)


def report_request_done(what: str, method: str, path: str,
                        elapsed_ms: float, status: str = "",
                        bytes: int = -1) -> None:
    """请求完成事件；bytes 为返回包大小（-1 表示无响应/超时/异常）。"""
    report("request_done", what, what=what, method=method, path=path,
           elapsed_ms=round(elapsed_ms), status=status, bytes=bytes)


def report_stage(message: str, **params: Any) -> None:
    """业务阶段事件（如「查询剧本基本信息 llm_xxx (3/68)」）。"""
    report("stage", message, **params)


def report_done(message: str = "", **params: Any) -> None:
    """操作完成事件：页面清空「正在执行/当前进度」，显示完成提示。

    params 可带 elapsed_s（总耗时秒）与结果摘要字段。
    """
    report("done", message, **params)
