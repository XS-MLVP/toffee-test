import inspect
import os
import shlex

import pytest
import toffee
from toffee import run
import time

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


def _find_option_value(args, option):
    """Return the last value assigned to an option in an argument sequence."""
    found = False
    value = None
    index = 0
    option_with_equals = f"{option}="

    while index < len(args):
        arg = os.fspath(args[index])
        if arg == "--":
            break
        if arg == option:
            if index + 1 < len(args):
                found = True
                value = os.fspath(args[index + 1])
                index += 2
                continue
        elif arg.startswith(option_with_equals):
            found = True
            value = arg[len(option_with_equals) :]
        index += 1

    return found, value


def _resolve_ini_report_dir(config: pytest.Config, report_dir: str) -> str:
    """Resolve a relative report dir supplied by the selected config file."""
    inipath = config.inipath
    if inipath is None or os.path.isabs(report_dir):
        return report_dir

    ini_addopts = config.inicfg.get("addopts", [])
    if isinstance(ini_addopts, str):
        ini_addopts = shlex.split(ini_addopts)

    found_in_ini, ini_report_dir = _find_option_value(
        ini_addopts, "--report-dir"
    )
    if not found_in_ini or ini_report_dir != report_dir:
        return report_dir

    # An -o addopts=... override replaces the value read from the config file.
    if any(
        override.partition("=")[0] == "addopts"
        for override in getattr(config, "_override_ini", ())
    ):
        return report_dir

    # Environment and command-line values are parsed after file addopts, so
    # they retain their normal cwd semantics when they override the file.
    env_addopts = shlex.split(os.environ.get("PYTEST_ADDOPTS", ""))
    if _find_option_value(env_addopts, "--report-dir")[0] or _find_option_value(
        config.invocation_params.args, "--report-dir"
    )[0]:
        return report_dir

    return os.path.normpath(os.path.join(os.fspath(inipath.parent), report_dir))


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


def pytest_addoption(parser: pytest.Parser):
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
        help="Custom key value pair. dict wtih base64 encoded.",
    )

    group.addoption(
        "--no-func-cov",
        action="store_true",
        default=False,
        help="Run test without functional coverage.",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config):
    if hasattr(config, "workerinput"):
        config._toffee_test_start_time = config.workerinput["toffee_test_start_time"]
    else:
        config._toffee_test_start_time = time.time()
    config.addinivalue_line(
        "markers", "mlvp_async: mark test to run with toffee's event loop"
    )
    config.addinivalue_line("markers", toffee_tags_process.__doc__)
    if config.getoption("--toffee-report"):
        config.option.template = ["html/toffee.html"]
        config.option.template_dir = [get_template_dir()]

        report_name = config.getoption("--report-name")

        if hasattr(config, "workerinput"):
            report_name = config.workerinput["report_name"]
        elif report_name is None:
            report_name = get_default_report_name()
            config.option.report_name = report_name

        report_dir = config.getoption("--report-dir")
        if hasattr(config, "workerinput"):
            report_dir = config.workerinput["report_dir"]
        elif report_dir is None:
            report_dir = "reports"
            config.option.report_dir = report_dir
        else:
            report_dir = _resolve_ini_report_dir(config, report_dir)
            config.option.report_dir = report_dir
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


@pytest.hookimpl()
def pytest_configure_node(node):
    node.workerinput["report_name"] = node.config.option.report_name
    node.workerinput["report_dir"] = node.config.option.report_dir
    node.workerinput["toffee_test_start_time"] = node.config._toffee_test_start_time


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
def toffee_test_worker(request) -> str:
    if hasattr(request.config, "workerinput"):
        return request.config.workerinput["workerid"]
    return "gw0"


@pytest.fixture()
def toffee_request(request):
    request_info = ToffeeRequest(request)

    yield request_info

    request_info.finish(request)


mlvp_pre_request = toffee_request
