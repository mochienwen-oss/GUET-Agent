"""Cooperative cancellation between expensive retrieval stages/batches."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event
import time

_CANCEL = ContextVar("retrieval_cancel", default=None)
_DEADLINE = ContextVar("request_deadline", default=None)


def check_cancelled():
    event = _CANCEL.get()
    if event is not None and event.is_set():
        raise InterruptedError("请求已取消，未继续执行后续阶段")
    deadline = _DEADLINE.get()
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("本次请求已超时，未继续执行后续阶段")


@contextmanager
def request_scope(seconds, *, cancel_event=None):
    event = cancel_event if cancel_event is not None else Event()
    cancel_token = _CANCEL.set(event)
    deadline_token = _DEADLINE.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        if cancel_event is None:
            event.set()
        _CANCEL.reset(cancel_token)
        _DEADLINE.reset(deadline_token)


def remaining_seconds(limit):
    check_cancelled()
    deadline = _DEADLINE.get()
    return min(limit, max(0.001, deadline - time.monotonic())) if deadline else limit


async def bounded_stream(stream, seconds):
    """One deadline across all awaits, including a silent model response."""
    inherited = _CANCEL.get()
    event = inherited or Event()
    deadline = _DEADLINE.get() or (time.monotonic() + seconds)

    async def invoke(function):
        # wait_for may use a new task for each next(): never carry ContextVar
        # reset tokens across an async-generator yield or task boundary.
        cancel_token = _CANCEL.set(event)
        deadline_token = _DEADLINE.set(deadline)
        try:
            return await function()
        finally:
            _CANCEL.reset(cancel_token)
            _DEADLINE.reset(deadline_token)

    try:
        while True:
            if event.is_set():
                raise InterruptedError("请求已取消")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("本次请求已超时")
            try:
                item = await asyncio.wait_for(invoke(stream.__anext__), remaining)
            except StopAsyncIteration:
                return
            if event.is_set():
                raise InterruptedError("请求已取消")
            if time.monotonic() >= deadline:
                raise TimeoutError("本次请求已超时")
            yield item
    except BaseException:
        event.set()
        raise
    finally:
        if inherited is None:
            event.set()
        await invoke(stream.aclose)


class ConversationLease:
    """Database connection-owned lease; shared by all backend workers."""
    def __init__(self, conversation_id):
        from database.db import engine
        from sqlalchemy import text
        self.connection = engine.connect()
        self.name = "guet:conversation:%s" % conversation_id
        self.conversation_id = int(conversation_id)
        self.postgresql = engine.dialect.name == 'postgresql'
        try:
            if self.postgresql:
                result = self.connection.execute(
                    text("SELECT pg_try_advisory_lock(:namespace, :conversation_id)"),
                    {"namespace": 1196778836, "conversation_id": self.conversation_id}).scalar()
            else:
                result = self.connection.execute(text("SELECT GET_LOCK(:name, 0)"),
                                                 {"name": self.name}).scalar()
            if not result:
                from core.errors import ApiError
                raise ApiError(409, "该会话已有请求正在处理，请完成或停止后重试")
        except BaseException:
            self.connection.close()
            self.connection = None
            raise

    def close(self):
        if self.connection is None:
            return
        from sqlalchemy import text
        connection, self.connection = self.connection, None
        try:
            if self.postgresql:
                connection.execute(text("SELECT pg_advisory_unlock(:namespace, :conversation_id)"),
                                   {"namespace": 1196778836, "conversation_id": self.conversation_id})
            else:
                connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": self.name})
        except BaseException:
            connection.invalidate()  # Do not return a connection holding a lease to the pool.
            raise
        finally:
            connection.close()


def is_controlled():
    return _CANCEL.get() is not None


async def run_request(function, request, *args, **kwargs):
    from core.config import settings
    with request_scope(settings.agent_request_timeout_seconds):
        async def execute():
            task = asyncio.create_task(run_sync(function, *args, **kwargs))
            try:
                while not task.done():
                    await asyncio.wait({task}, timeout=0.1)
                    if not task.done() and await request.is_disconnected():
                        task.cancel()
                        raise asyncio.CancelledError()
                return await task
            finally:
                if not task.done():
                    task.cancel()
        return await asyncio.wait_for(execute(), settings.agent_request_timeout_seconds)


async def run_sync(function, *args, **kwargs):
    event = _CANCEL.get() or Event()

    def invoke():
        token = _CANCEL.set(event)
        try:
            check_cancelled()
            result = function(*args, **kwargs)
            check_cancelled()
            return result
        finally:
            _CANCEL.reset(token)

    try:
        return await asyncio.to_thread(invoke)
    except asyncio.CancelledError:
        event.set()
        raise
