#!/usr/bin/env python3
"""线程级任务钩子：把「取消探针」与「进度回调」下沉到耗时循环内部。

背景：
  GUI 的后台任务此前只能在写日志的瞬间响应取消——LLM 一次请求 1-2 分钟，
  期间点「取消」毫无反应。本模块用 thread-local 存放当前任务的钩子，
  llm_client（流式分块）、topic_search（逐条检索）等在各自循环里调用
  check_cancelled() / report_progress()，取消从分钟级降到秒级。

约定：
  - gui_server._run_task 的 worker 线程负责 set_hooks / clear_hooks
  - 命令行 pipeline 不设钩子，两个函数自动退化为 no-op，行为不变
  - TaskCancelled 继承 BaseException：业务层宽泛的 except Exception
    不会吞掉取消信号，能一路抛到 worker 顶层
"""

from __future__ import annotations

import threading
from typing import Callable

_local = threading.local()


class TaskCancelled(BaseException):
    """任务被用户取消（协作式，在最近的探针点抛出）。"""


def set_hooks(
    cancel_check: Callable[[], bool] | None = None,
    progress: Callable[[str], None] | None = None,
) -> None:
    """绑定当前线程的任务钩子（worker 线程入口调用）。"""
    _local.cancel_check = cancel_check
    _local.progress = progress


def clear_hooks() -> None:
    """解绑（worker 结束时调用，防线程复用串味）。"""
    _local.cancel_check = None
    _local.progress = None


def check_cancelled() -> None:
    """探针：任务已被取消则立刻抛 TaskCancelled；无钩子时为 no-op。"""
    fn = getattr(_local, "cancel_check", None)
    if fn is not None and fn():
        raise TaskCancelled("已取消")


def report_progress(msg: str) -> None:
    """上报单行实时进度（界面原地刷新，不追加日志）；无钩子时为 no-op。"""
    fn = getattr(_local, "progress", None)
    if fn is not None:
        try:
            fn(str(msg))
        except Exception:
            pass
