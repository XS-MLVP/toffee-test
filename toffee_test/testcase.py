import asyncio
import functools

import pytest
import pytest_asyncio
import toffee

fixture = pytest_asyncio.fixture


async def cancel_all_tasks():
    tasks = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def testcase(func):
    func.is_toffee_testcase = True

    @functools.wraps(func)
    @pytest.mark.asyncio
    async def wrapper(*args, **kwargs):
        ret = await toffee.asynchronous.main_coro(func(*args, **kwargs))
        await cancel_all_tasks()
        return ret

    return wrapper
