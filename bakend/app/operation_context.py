"""Cooperative checkpoints for read/validation operations running outside the API loop."""
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Event
from typing import Callable


class OperationCancelled(RuntimeError):
    pass


@dataclass
class OperationContext:
    cancel_event: Event
    report: Callable[[str], None]


current_operation: ContextVar[OperationContext | None] = ContextVar("current_operation", default=None)


def checkpoint(message: str = "") -> None:
    context = current_operation.get()
    if context:
        if context.cancel_event.is_set():
            raise OperationCancelled("请求已停止")
        if message:
            context.report(message)


async def cancellable(awaitable):
    """Interrupt async transport on an explicit stop, then await its cleanup."""
    context = current_operation.get()
    task = asyncio.ensure_future(awaitable)
    if context is None:
        return await task

    async def stopping():
        while not context.cancel_event.is_set():
            await asyncio.sleep(0.05)

    watcher = asyncio.create_task(stopping())
    try:
        await asyncio.wait((task, watcher), return_when=asyncio.FIRST_COMPLETED)
        if context.cancel_event.is_set():
            task.cancel()
            await _drain(task)
            raise OperationCancelled("请求已停止")
        return await task
    except asyncio.CancelledError:
        context.cancel_event.set()
        if not task.cancelling():
            task.cancel()
        await _drain(task)
        raise
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


async def _drain(task):
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    if not task.cancelled():
        task.exception()  # Retrieve a cleanup failure without masking the stop.


async def operation_thread(function, *args, **kwargs):
    """Keep the turn occupied until a cancelled thread has actually exited.

    Cancelling to_thread only discards its awaiter. A plan writer would otherwise
    outlive the turn and race a retry. Pass the cooperative event and drain it.
    """
    checkpoint()
    context = current_operation.get()
    class ThreadStop(Event):
        def is_set(self):
            return super().is_set() or bool(context and context.cancel_event.is_set())
    stop = ThreadStop()
    def invoke():
        token = current_operation.set(OperationContext(stop, context.report if context else lambda _: None))
        try:
            checkpoint()
            return function(*args, **kwargs)
        finally:
            current_operation.reset(token)
    task = asyncio.create_task(asyncio.to_thread(invoke))
    try:
        result = await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        await _drain(task)
        raise
    checkpoint()
    return result
