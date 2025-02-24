import asyncio
import functools

import pytest
import pytest_asyncio
import toffee

fixture = pytest_asyncio.fixture


def testcase(func):
    func.is_toffee_testcase = True

    @functools.wraps(func)
    @pytest.mark.asyncio
    async def wrapper(*args, **kwargs):
        ret = await toffee.asynchronous.main_coro(func(*args, **kwargs))
        asyncio.get_event_loop().stop()
        return ret

    return wrapper
