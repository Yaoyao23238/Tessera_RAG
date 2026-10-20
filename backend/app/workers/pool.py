"""CPU 密集任务进程池封装。

Windows 下 ProcessPoolExecutor 必须使用 spawn 启动方式，且入口模块需要
``if __name__ == "__main__":`` 保护。本模块提供懒加载单例进程池。

当前仅 Docling 解析走进程池（CPU 密集且可 pickle）；
切分/向量化/rerank 依赖主进程模型单例，走 asyncio.to_thread——
spawn 子进程无法共享已加载的 BGE-M3，也不会重复加载它。
子进程仅在使用时加载 Docling 布局模型（每 worker 一份），
因此 worker 上限压到 2，控制常驻内存。

注意：提交到进程池的函数必须是模块级可 pickle 的函数，
参数与返回值也必须可 pickle（core 层的 dataclass 均满足）。
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_pool: ProcessPoolExecutor | None = None


def _default_workers() -> int:
    """默认 worker 数：CPU 核数减一，至多 2。

    解析是低频任务；每个 spawn 子进程会常驻一份 Docling 布局模型，
    本地工具不压满机器也控制内存。
    """
    cpu = os.cpu_count() or 2
    return max(1, min(cpu - 1, 2))


def get_process_pool(max_workers: int | None = None) -> ProcessPoolExecutor:
    """获取进程池单例（spawn 上下文，兼容 Windows）。"""
    global _pool
    if _pool is None:
        ctx = mp.get_context("spawn")
        workers = max_workers or _default_workers()
        _pool = ProcessPoolExecutor(max_workers=workers, mp_context=ctx)
        logger.info("进程池已启动：max_workers=%d (spawn)", workers)
    return _pool


def submit_cpu(fn: Callable[..., T], *args: Any, **kwargs: Any) -> Future[T]:
    """提交一个 CPU 密集任务到进程池，返回 Future。

    Args:
        fn: 模块级可 pickle 函数。
        *args / **kwargs: 可 pickle 参数。

    Returns:
        concurrent.futures.Future，可用 asyncio.wrap_future 包装。
    """
    return get_process_pool().submit(fn, *args, **kwargs)


async def run_cpu(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """异步执行 CPU 密集任务（不阻塞事件循环）。"""
    import asyncio

    future = submit_cpu(fn, *args, **kwargs)
    return await asyncio.wrap_future(future)


def shutdown_pool(wait: bool = True) -> None:
    """关闭进程池（应用退出时调用）。"""
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=wait, cancel_futures=False)
        _pool = None
        logger.info("进程池已关闭")
