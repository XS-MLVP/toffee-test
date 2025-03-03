import inspect
import os

import pytest
import toffee
from toffee import run

from .markers import toffee_tags_process
from .reporter import get_default_report_name
from .reporter import get_template_dir
from .reporter import process_context
from .reporter import process_func_coverage
from .reporter import set_output_report
from .utils import base64_decode
from .utils import get_toffee_custom_key_value
from .utils import set_toffee_custom_key_value


"""
toffee plugin
"""


@pytest.hookimpl(trylast=True, optionalhook=True)
def pytest_reporter_context(context, config):
    process_context(context, config)


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_call(item):
    call = yield
    if call.excinfo is not None:
        eclass, evalue, _ = call.excinfo
        ignore_exceptions = get_toffee_custom_key_value().get(
            "toffee_ignore_exceptions", []
        )
        if eclass.__name__ in ignore_exceptions:
            call.force_exception(
                pytest.skip.Exception(
                    "Skiped exception: '%s(%s)'" % (eclass.__name__, evalue)
                )
            )


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    return process_func_coverage(item, call, report)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    toffee_tags_process(item)


def pytest_addoption(parser):
    group = parser.getgroup("reporter")
    group.addoption(
        "--toffee-report",
        action="store_true",
        default=False,
        help="Generate the report.",
    )

    group.addoption(
        "--report-name", action="store", default=None, help="The name of the report."
    )

    group.addoption(
        "--report-dir", action="store", default=None, help="The dir of the report."
    )

    group.addoption(
        "--report-dump-json",
        action="store_true",
        default=False,
        help="Dump json report.",
    )

    group.addoption(
        "--custom-key-value",
        action="store",
        default=None,
        help="Custom key value pair. dict wtih base64 encoded",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    config.addinivalue_line(
        "markers", "mlvp_async: mark test to run with toffee's event loop"
    )
    config.addinivalue_line("markers", toffee_tags_process.__doc__)
    if config.getoption("--toffee-report"):
        config.option.template = ["html/toffee.html"]
        config.option.template_dir = [get_template_dir()]

        report_name = config.getoption("--report-name")
        if report_name is None:
            report_name = get_default_report_name()

        report_dir = config.getoption("--report-dir")
        if report_dir is None:
            report_dir = "reports"
        report_name = os.path.join(report_dir, report_name)

        config.option.report = [report_name]
        set_output_report(report_name)
    if config.getoption("--report-dump-json"):
        config.option.toffee_report_dump_json = True
    else:
        config.option.toffee_report_dump_json = False
    # Custom key value pair
    ckv = config.getoption("--custom-key-value")
    if ckv:
        set_toffee_custom_key_value(base64_decode(ckv))

    if "asyncio_default_fixture_loop_scope" not in config._inicache:
        config._inicache["asyncio_default_fixture_loop_scope"] = "function"


"""
toffee async test
"""


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if "mlvp_async" in pyfuncitem.keywords:
        toffee.warning(
            "test marked with mlvp_async will be deprecated in the future, please use \
                        @toffee_test.case instead"
        )

        func = pyfuncitem.obj
        assert inspect.iscoroutinefunction(
            func
        ), "test marked with mlvp_async must be a coroutine function"

        signature = inspect.signature(func)
        filtered_funcargs = {
            k: v for k, v in pyfuncitem.funcargs.items() if k in signature.parameters
        }

        run(func(**filtered_funcargs))

        return True

    return None


from .request import ToffeeRequest


@pytest.fixture()
def toffee_request(request):
    request_info = ToffeeRequest(request)
    request_info.request_name = (
        str(request._pyfuncitem).strip("<").strip(">").split(" ")[-1]
    )
    yield request_info

    request_info.finish(request)


mlvp_pre_request = toffee_request
