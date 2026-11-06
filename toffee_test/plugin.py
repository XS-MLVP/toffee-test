import dis
import inspect
import importlib
from pathlib import Path
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
from .forked import run_simulator_protocol
from .simulators import get_backend

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


def _fixture_declares_forked_dut(item: pytest.Item) -> bool:
    """Inspect fixture factories without constructing a native DUT in the parent.

    Follow referenced Python functions, closures and module attributes so that
    existing ``fixture -> create_dut -> DUT`` APIs need no isolation marker.
    A visited set bounds recursive helper graphs; no inspected callable runs.
    """
    fixture_info = getattr(item, "_fixtureinfo", None)
    if fixture_info is None:
        return False
    pending = [
        definition.func
        for name in getattr(item, "fixturenames", ())
        for definition in fixture_info.name2fixturedefs.get(name, ())
    ]
    seen = set()
    while pending:
        function = inspect.unwrap(pending.pop())
        if not inspect.isfunction(function) or id(function) in seen:
            continue
        seen.add(id(function))
        namespace = dict(function.__globals__)
        namespace.update(inspect.getclosurevars(function).nonlocals)
        candidate = None
        for instruction in dis.get_instructions(function):
            if instruction.opname in {"LOAD_GLOBAL", "LOAD_NAME", "LOAD_DEREF"}:
                candidate = namespace.get(instruction.argval)
            elif instruction.opname in {"LOAD_ATTR", "LOAD_METHOD"}:
                # Read static namespaces only; avoid executing module __getattr__
                # or descriptors while examining a possible factory.
                candidate = vars(candidate).get(instruction.argval) if (
                    inspect.ismodule(candidate) or inspect.isclass(candidate)
                ) else None
            else:
                candidate = None
            if inspect.isclass(candidate):
                simulator = inspect.getattr_static(candidate, "SIMULATOR", None)
                if isinstance(simulator, str) and get_backend(simulator).forked_execution:
                    return True
            elif inspect.isfunction(candidate):
                pending.append(candidate)
    return False


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
    """Attach portable exception and coverage data before worker transport."""
    outcome = yield
    report = outcome.get_result()
    # CallInfo is not transported by forked/xdist workers. Keep only plain
    # exception data on TestReport, whose extra attributes survive transport.
    if report.failed and call.excinfo is not None:
        report.__toffee_exception__ = {
            "phase": report.when,
            "type": call.excinfo.type.__name__,
            "message": str(call.excinfo.value)[:2000],
        }
    return process_func_coverage(item, call, report)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    toffee_tags_process(item)


def pytest_addoption(parser: pytest.Parser):
    group = parser.getgroup("reporter")
    group.addoption(
        "--coverage-gui", choices=("off", "native", "xpra"), default="off",
        help="Coverage application links: off (HTML only), native (local desktop application), or xpra (host resolver).",
    )
    group.addoption(
        "--coverage-application-resolver", default=None, metavar="MODULE:CALLABLE",
        help="Optional custom resolver for application descriptors without a URL.",
    )
    group.addoption(
        "--coverage-application-workspace", default=None, metavar="PATH",
        help="Override the host workspace (default: pytest invocation directory).",
    )
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

    group.addoption(
        "--case-coverage",
        action="store_true",
        default=False,
        help="Generate normalized code coverage data for every testcase.",
    )
    group.addoption(
        "--toffee-forked",
        action="store_true",
        default=False,
        help="Force the VCS DUT isolation backend (enabled automatically for VCS DUTs).",
    )
    group.addoption(
        "--toffee-forked-timeout",
        action="store",
        type=float,
        default=300.0,
        help="Timeout in seconds for one forked VCS test.",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem=None):
    if os.environ.get("TOFFEE_TEST_FORKED_CHILD"):
        return None
    if not _fixture_declares_forked_dut(item):
        return None
    run_simulator_protocol(
        item,
        nextitem=nextitem,
        timeout=item.config.getoption("--toffee-forked-timeout"),
    )
    return True


def _load_application_resolver(resolver_name):
    """Load an explicit host callback from CLI options or host metadata."""
    try:
        module, name = resolver_name.split(":", 1)
        resolver = getattr(importlib.import_module(module), name)
        if not callable(resolver):
            raise TypeError("resolver is not callable")
        return resolver
    except (ValueError, ImportError, AttributeError, TypeError) as error:
        raise pytest.UsageError(f"Invalid coverage application resolver: {error}") from error


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config):
    mode = config.getoption("--coverage-gui")
    resolver_name = config.getoption("--coverage-application-resolver")
    if mode == "native":
        if resolver_name:
            raise pytest.UsageError("--coverage-application-resolver is only used with --coverage-gui=xpra")
        from .utils.code_coverage.native import resolve_native_detail
        config.toffee_coverage_application_resolver = resolve_native_detail
    elif mode == "xpra":
        if resolver_name:
            config.toffee_coverage_application_resolver = _load_application_resolver(resolver_name)
    else:
        config.toffee_coverage_application_resolver = None
    if config.getoption("--case-coverage") and not config.getoption("--toffee-report"):
        raise pytest.UsageError("--case-coverage requires --toffee-report")
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
    custom = base64_decode(ckv) if ckv else {}
    if ckv:
        set_toffee_custom_key_value(custom)
    gui_options = custom.get("coverage_gui", {})
    if not isinstance(gui_options, dict):
        raise pytest.UsageError("custom-key-value coverage_gui must be an object")
    workspace = gui_options.get("workspace")
    if workspace is not None and (
        not isinstance(workspace, str) or not workspace or not Path(workspace).is_absolute()
    ):
        raise pytest.UsageError("custom-key-value coverage_gui.workspace must be an absolute path")
    config.toffee_coverage_gui_workspace = workspace
    config.toffee_coverage_gui_resolver = gui_options.get("resolver")

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


@pytest.hookimpl(trylast=True)
def pytest_sessionstart(session):
    """Load an optional custom host callback; provider URLs need no resolver."""
    config = session.config
    if (config.getoption("--coverage-gui") == "xpra"
            and not callable(getattr(config, "toffee_coverage_application_resolver", None))
            and getattr(config, "toffee_coverage_gui_resolver", None) is not None):
        config.toffee_coverage_application_resolver = _load_application_resolver(
            config.toffee_coverage_gui_resolver
        )
