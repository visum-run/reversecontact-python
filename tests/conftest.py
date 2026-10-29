import inspect
from collections import deque

import httpx
import pytest

from reversecontact import AsyncReverseContact, ReverseContact


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay

    async def asleep(self, delay):
        self.sleep(delay)


class Harness:
    def __init__(self, asynchronous):
        self.asynchronous = asynchronous
        self.clock = Clock()
        self.responses = deque()
        self.requests = []
        self.handler = None
        transport = httpx.MockTransport(self.handle)
        self.http = (
            httpx.AsyncClient(transport=transport)
            if asynchronous
            else httpx.Client(transport=transport)
        )
        factory = AsyncReverseContact if asynchronous else ReverseContact
        self.client = factory(
            api_key="rc_test",
            http_client=self.http,
            sleep=self.clock.asleep if asynchronous else self.clock.sleep,
            clock=self.clock.time,
        )

    def handle(self, request):
        self.requests.append(request)
        if self.handler:
            return self.handler(request)
        value = self.responses.popleft()
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value(request)
        return value

    async def call(self, method, *args, **kwargs):
        value = method(*args, **kwargs)
        return await value if inspect.isawaitable(value) else value

    async def collect(self, items):
        return [item async for item in items] if self.asynchronous else list(items)


@pytest.fixture(params=[False, True], ids=["sync", "async"])
async def h(request):
    harness = Harness(request.param)
    yield harness
    if harness.asynchronous:
        await harness.http.aclose()
    else:
        harness.http.close()


def ok(data=None, **kwargs):
    return httpx.Response(
        200,
        json={
            "data": data,
            "quotas": {"creditsConsumed": 0},
            "metadata": {"requestId": "req_test"},
        },
        **kwargs,
    )


def failure(status, code="REQUEST_ERROR", details=None, **kwargs):
    return httpx.Response(
        status,
        json={
            "error": {"code": code, "message": "Test error", "details": details},
            "metadata": {"requestId": "req_error"},
        },
        **kwargs,
    )
