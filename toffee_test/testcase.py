import functools

import pytest
import pytest_asyncio
import toffee

fixture = pytest_asyncio.fixture


def case(func):
    func.is_toffee_testcase = True

    @functools.wraps(func)
    @pytest.mark.asyncio
    async def wrapper(*args, **kwargs):
        return await toffee.asynchronous.main_coro(func(*args, **kwargs))

    return wrapper
