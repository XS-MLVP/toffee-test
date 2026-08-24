import json
import hashlib
import os
import uuid
from collections import Counter
from datetime import datetime
from typing import Union
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from filelock import FileLock
from toffee.funcov import CovGroup, get_func_full_name

from .utils import get_toffee_custom_key_value
from .utils.code_coverage import (
    CoverageRecord,
    convert_case_coverage,
    convert_code_coverage,
)


LOCK_FILE_FUNC_COV = "/tmp/toffee_func_cov.lock"
LOCK_FILE_LINE_COV = "/tmp/toffee_line_cov.lock"

def get_default_report_name():
    current_time = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"report-{current_time}/report-{current_time}.html"


def get_template_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


__output_report_dir__ = ""


def _code_coverage_dir_name(records):
    simulators = set()
    for value in records or []:
        try:
            record = value if isinstance(value, CoverageRecord) else CoverageRecord.from_dict(value)
        except (TypeError, ValueError):
            continue
        simulators.add(record.simulator)
    return "coverage_data" if simulators == {"vcs"} else "line_dat"


def set_output_report(report_path):
    report_dir = os.path.dirname(report_path)
    if not os.path.exists(report_dir):
        os.makedirs(report_dir)

    global __output_report_dir__
    __output_report_dir__ = report_dir


def __update_line_coverage__(line_coverage_list: list[dict]=None, line_grate=99):
    if not line_coverage_list:
        return None
    coverage_error = ""
    coverage_dir_name = _code_coverage_dir_name(line_coverage_list)
    line_dat_dir = os.path.join(__output_report_dir__, coverage_dir_name)
    vcs_mode = os.environ.get("TOFFEE_VCS_AGGREGATE_MODE", "cumulative").strip().lower()
    if coverage_dir_name == "coverage_data" and vcs_mode == "deferred":
        # Coverage-refine stage C queries only its fixed batch targets after
        # pytest.  Do not run the full VCS/URG aggregate from the reporter.
        return {
            "hints": 0, "total": 0, "grate": 0, "ignore": [], "detail": "",
            "summary_file": "", "metrics": {}, "databases": [], "warnings": [],
            "error": "", "collection_mode": "deferred",
        }
    if vcs_mode not in {"cumulative", "deferred"}:
        raise ValueError("TOFFEE_VCS_AGGREGATE_MODE must be 'cumulative' or 'deferred'")
    try:
        result = convert_code_coverage(line_coverage_list, line_dat_dir)
        return result.to_report_dict(
            __report_info__.get("line_grate", line_grate)
        )
    except Exception as e:
        from toffee.logger import error
        hint = total = 0
        ignore = []
        coverage_error = repr(e)

    return {
        "hints": hint,
        "total": total,
        "grate": __report_info__.get("line_grate", line_grate),
        "ignore": ignore,
        "detail": "",
        "summary_file": "",
        "metrics": {},
        "databases": [],
        "warnings": [],
        "error": coverage_error,
    }


def __update_func_coverage__(__func_coverage__):
    if __func_coverage__ is None:
        return None
    coverage = {}
    group_num_hints = 0
    group_num_total = 0
    point_num_hints = 0
    point_num_total = 0
    bin_num_hints = 0
    bin_num_total = 0
    has_once = False

    """
        Data Stucture Hierachy:
            Dict(CovGroup): list[dict]  |- Points:   list[dict] |- bins:     list[dict]  |-   hints:  int
                                                                |                        |-   name:   str
                                                                |- hinted:   boolean
                                                                |- name:     str
                                                                |- functions:list[str]
                                        |- name:     str
                                        |- hinted:   boolean
                                        |- bin_num_total:  int
                                        |- bin_num_hints:  int
                                        |- point_num_total:  int
                                        |- point_num_hints:  int
                                        |- has_once:  boolean
    """

    def parse_group(g):
        nonlocal point_num_hints, point_num_total, bin_num_hints, bin_num_total, has_once
        data = json.loads(g)
        if data["has_once"]:
            has_once = True
        return data

    def merge_dicts(dict1, dict2):
        """Recursively merge two dictionaries."""
        result = dict1.copy()  # Start with keys and values of dict1
        for key, value in dict2.items():
            if key.startswith("__"):
                continue
            if key in result:
                if isinstance(result[key], dict) and isinstance(value, dict):
                    result[key] = merge_dicts(result[key], value)
                elif isinstance(result[key], list) and isinstance(value, list):
                    if key == "points" or key == "bins":
                        if key == "bins" and result.get("dynamic_bin", False) == False:
                            assert Counter([x["name"] for x in value]) == Counter(
                                x["name"] for x in result[key]
                            ), f"bins in points {dict1['name']} should be same, merge function coverage: {dict1['name']} failed (when add_watch_point, use dynamic_bin=True to ignore this error)"
                        old_keys = {a["name"]: i for i, a in enumerate(result[key])}
                        for data in value:
                            if data["name"] not in old_keys:
                                result[key].append(data)
                            else:
                                result[key][old_keys[data["name"]]] = merge_dicts(
                                    result[key][old_keys[data["name"]]], data
                                )
                    else: # Normal list merge, use unique items
                        result[key] = list(set(result[key] + value))
                elif isinstance(result[key], bool) and isinstance(value, bool):
                    if key == "has_once":
                        result[key] = result[key] and value
                    else:
                        result[key] = result[key] or value
                elif isinstance(result[key], int) and isinstance(value, int):
                    result[key] += value
                elif result[key] == value:
                    continue  # Same value, do nothing
                else:
                    raise ValueError(
                        f"Conflict at key '{key}': {result[key]} vs {value}"
                    )
            else:
                result[key] = value
        return result

    def merge_dicts_list(dicts: list):
        """Merge a list of dictionaries."""
        result = {}
        for d in dicts:
            if d["name"] in result:
                result[d["name"]] = merge_dicts(result[d["name"]], d)
            else:
                result[d["name"]] = d
        return [v for _, v in result.items()]

    coverage["groups"] = merge_dicts_list([parse_group(g) for g in __func_coverage__])

    # Recalculate the groups hinted situation
    for group in coverage["groups"]:
        group_num_total += 1

        group["hinted"] = True

        # Point Hinted
        group["point_num_total"] = len(group["points"])
        group["point_num_hints"] = 0
        group["point_functions"] = []
        group["bin_num_total"] = 0
        group["bin_num_hints"] = 0

        for point in group["points"]:
            # Bins Hinted
            group["bin_num_total"] += len(point["bins"])
            tmp_bin_hints = 0
            for bin in point["bins"]:
                group["bin_num_hints"] += int(bin["hints"] > 0)
                tmp_bin_hints += int(bin["hints"] > 0)
            point["hinted"] = tmp_bin_hints == len(point["bins"])

            group["point_num_hints"] += int(point["hinted"])
            group["hinted"] &= point["hinted"]
            group["point_functions"].extend([item for funcs in point["functions"].items() for item in funcs[1]])
        group["point_functions"] = list(set(group["point_functions"]))

        # Group Hinted
        if group["hinted"]:
            group_num_hints += 1

        point_num_hints += group["point_num_hints"]
        point_num_total += group["point_num_total"]
        bin_num_hints += group["bin_num_hints"]
        bin_num_total += group["bin_num_total"]

    coverage["group_num_total"] = group_num_total
    coverage["group_num_hints"] = group_num_hints
    coverage["point_num_total"] = point_num_total
    coverage["point_num_hints"] = point_num_hints
    coverage["bin_num_total"] = bin_num_total
    coverage["bin_num_hints"] = bin_num_hints
    coverage["grate"] = 100
    coverage["has_once"] = has_once
    return coverage


def _testcase_function_id(item):
    try:
        return get_func_full_name(item.function)
    except (OSError, TypeError, ValueError):
        # pytest-forked may reconstruct the function without retrievable source.
        return None


def _testcase_nodeid(item):
    nodeid = getattr(item, "nodeid", None)
    if nodeid:
        return str(nodeid)
    function_id = _testcase_function_id(item)
    if function_id:
        return function_id
    return f"{getattr(item, 'path', '')}::{getattr(item, 'name', '')}"


def _testcase_functional_coverage(test):
    item = test["item"]
    function_id = _testcase_function_id(item)
    function_name = str(
        getattr(item, "originalname", None)
        or getattr(item, "name", "").split("[", 1)[0]
    )
    function_path = os.path.abspath(str(getattr(item, "path", "")))

    def belongs_to_test(candidate):
        if function_id is not None and candidate == function_id:
            return True
        return bool(
            function_name
            and function_path
            and candidate.startswith(f"{function_path}:")
            and candidate.endswith(f"::{function_name}")
        )

    declared = set()
    observed = Counter()
    seen = set()
    for phase in test["phases"]:
        report = phase["report"]
        for entry in getattr(report, "__coverage_group__", []):
            key = (entry["hash"], entry["id"])
            if key in seen:
                continue
            seen.add(key)
            group = json.loads(entry["data"])
            for point in group.get("points", []):
                prefix = f"{group['name']}/{point['name']}"
                for bin_data in point.get("bins", []):
                    bin_id = f"{prefix}/{bin_data['name']}"
                    hints = int(bin_data.get("hints", 0))
                    if hints > 0:
                        observed[bin_id] += hints
                for bin_name, functions in point.get("functions", {}).items():
                    if any(belongs_to_test(candidate) for candidate in functions):
                        declared.add(f"{prefix}/{bin_name}")
    return {
        "declared": sorted(declared),
        "hit": [
            {"id": bin_id, "count": count}
            for bin_id, count in sorted(observed.items())
        ],
    }


def _testcase_record(test):
    for phase in reversed(test["phases"]):
        raw = getattr(phase["report"], "__line_coverage__", None)
        if raw:
            return CoverageRecord.from_dict(raw)
    return None


def _testcase_duration(test):
    duration = 0.0
    for phase in test["phases"]:
        value = getattr(phase["report"], "duration", 0.0)
        try:
            duration += float(value)
        except (TypeError, ValueError):
            pass
    return duration


def __update_testcase_coverage__(tests):
    records_by_nodeid = {}
    for test in tests:
        record = _testcase_record(test)
        if record is not None:
            records_by_nodeid[_testcase_nodeid(test["item"])] = record
    coverage_dir_name = _code_coverage_dir_name(list(records_by_nodeid.values()))
    line_dat_dir = os.path.join(__output_report_dir__, coverage_dir_name)
    cases_dir = os.path.join(line_dat_dir, "cases")
    os.makedirs(cases_dir, exist_ok=True)
    testcase_data = {}
    functional_index = {}
    databases = set()
    simulators = set()
    status_counts = Counter()
    coverage_errors = 0
    coverage_jobs = []
    coverage_mode = os.environ.get("TOFFEE_CASE_COVERAGE_MODE", "full").strip().lower()
    if coverage_mode not in {"full", "metadata"}:
        raise ValueError("TOFFEE_CASE_COVERAGE_MODE must be 'full' or 'metadata'")

    for test in tests:
        item = test["item"]
        nodeid = _testcase_nodeid(item)
        function_name = getattr(item, "name", None)
        if function_name is None:
            function_name = getattr(getattr(item, "function", None), "__name__", "")
        case_id = hashlib.sha256(nodeid.encode("utf-8")).hexdigest()[:12]
        status = str(test.get("status", {}).get("word") or "UNKNOWN").lower()
        status_counts[status] += 1
        functional = _testcase_functional_coverage(test)
        record = records_by_nodeid.get(nodeid)
        case = {
            "id": case_id,
            "status": status,
            "duration": _testcase_duration(test),
            "source": {
                "file": str(getattr(item, "path", "")),
                "function": str(function_name),
            },
            "included_in_merged": status == "passed",
            "functional": functional,
            "coverage": None,
            "code": {},
            "error": "",
        }

        for bin_id in functional["declared"]:
            functional_index.setdefault(
                bin_id, {"declared_by": [], "hit_by": []}
            )["declared_by"].append(nodeid)
        for hit in functional["hit"]:
            functional_index.setdefault(
                hit["id"], {"declared_by": [], "hit_by": []}
            )["hit_by"].append(nodeid)

        if record is not None:
            databases.add(record.database)
            simulators.add(record.simulator)
            case["provider"] = {
                "simulator": record.simulator,
                "database": record.database,
                "testdata": record.test,
                "worker": record.worker,
            }
            case_report_name = "observation.json" if coverage_dir_name == "coverage_data" else "code_coverage.json"
            relative = f"{coverage_dir_name}/cases/{case_id}/{case_report_name}"
            if coverage_mode == "metadata":
                # Coverage refinement performs one authoritative cumulative
                # VDB query later. Avoid reopening the same VDB for every
                # testcase when per-case atom attribution is not requested.
                case["coverage_mode"] = "metadata"
            else:
                coverage_jobs.append((nodeid, record, os.path.join(cases_dir, case_id), relative))
        testcase_data[nodeid] = case

    # VCS queries are independent read-only operations on the same VDB.  The
    # Picker helper holds a shared lock, and every case has its own output
    # directory, so bounded parallelism safely avoids reopening the VDB one
    # testcase at a time.  Keep other providers serial until they explicitly
    # document equivalent concurrency guarantees.
    workers = 1
    if coverage_jobs and all(record.simulator == "vcs" for _, record, _, _ in coverage_jobs):
        try:
            configured = int(os.environ.get("TOFFEE_CASE_COVERAGE_WORKERS", "0"))
        except ValueError:
            configured = 0
        workers = configured if configured > 0 else min(8, os.cpu_count() or 1)
        workers = min(max(1, workers), len(coverage_jobs))

    def collect_case(job):
        nodeid, record, output_dir, relative = job
        try:
            result = convert_case_coverage(record, output_dir)
            return nodeid, relative, result, ""
        except Exception as error:
            return nodeid, relative, None, repr(error)

    if workers == 1:
        completed = map(collect_case, coverage_jobs)
    else:
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="toffee-vcs-coverage")
        futures = [executor.submit(collect_case, job) for job in coverage_jobs]
        completed = (future.result() for future in as_completed(futures))
    try:
        for nodeid, relative, result, error in completed:
            case = testcase_data[nodeid]
            if error:
                case["error"] = error
                coverage_errors += 1
                continue
            case["coverage"] = relative
            case["code"] = {
                name: {
                    "covered": metric.covered,
                    "total": metric.total,
                    "miss": metric.uncovered,
                    "rate": metric.rate,
                }
                for name, metric in result.metrics.items()
            }
    finally:
        if workers > 1:
            executor.shutdown(wait=True)

    report = {
        "schema_version": 1,
        "simulator": next(iter(simulators)) if len(simulators) == 1 else None,
        "databases": sorted(databases),
        "summary": {
            "total": len(tests),
            "passed": status_counts["passed"],
            "failed": status_counts["failed"],
            "skipped": status_counts["skipped"],
            "error": status_counts["error"],
            "coverage_errors": coverage_errors,
        },
        "testcases": testcase_data,
        "functional_index": {
            bin_id: {
                "declared_by": sorted(set(value["declared_by"])),
                "hit_by": sorted(set(value["hit_by"])),
            }
            for bin_id, value in sorted(functional_index.items())
        },
    }
    index_name = "testcases.json" if coverage_dir_name == "coverage_data" else "testcase_coverage.json"
    output = os.path.join(line_dat_dir, index_name)
    with open(output, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    return f"{coverage_dir_name}/{index_name}"


__report_info__ = {
    "user": None,
    "title": None,
    "meta": {},
}


def process_context(context, config):
    def set_ctx(key, value):
        if value is None:
            return
        context[key] = value

    for k in ["Plugins", "Packages"]:
        context["metadata"].pop(k, None)

    global_report_info = get_toffee_custom_key_value().get("toffee_report_information", {})
    set_ctx("user", global_report_info.get("user", {"name":"Tofee", "email":"-"}))
    set_ctx("title", global_report_info.get("title", "Toffee Test Report"))
    context["metadata"].update(global_report_info.get("meta", {}))

    set_ctx("user", __report_info__["user"])
    set_ctx("title", __report_info__["title"])
    context["metadata"].update(__report_info__["meta"])

    coverage_func_list = []
    coverage_func_keys = []
    coverage_line_list = []
    coverage_line_keys = []
    has_testcase_func_coverage = False

    for t in context["tests"]:
        test_passed = t.get("status", {}).get("word") == "PASSED"
        for p in t["phases"]:
            if hasattr(p["report"], "__coverage_group__"):
                has_testcase_func_coverage = True
                for fc_data in p["report"].__coverage_group__:
                    key = "%s-%s" % (fc_data["hash"], fc_data["id"])
                    if key in coverage_func_keys:
                        continue
                    coverage_func_keys.append(key)
                    coverage_func_list.append(fc_data["data"])
            if hasattr(p["report"], "__line_coverage__"):
                if not test_passed:
                    continue
                lc_data = p["report"].__line_coverage__
                key = "%s-%s" % (lc_data["hash"], lc_data["id"])
                if key in coverage_line_keys:
                    continue
                coverage_line_keys.append(key)
                coverage_line_list.append(lc_data)
    # search data in session
    if not has_testcase_func_coverage and hasattr(context["session"], "__coverage_group__"):
        for fc_data in context["session"].__coverage_group__:
            key = "%s-%s" % (fc_data["hash"], fc_data["id"])
            if key in coverage_func_keys:
                continue
            coverage_func_keys.append(key)
            coverage_func_list.append(fc_data["data"])
    if hasattr(context["session"], "__line_coverage__"):
        for lc_data in context["session"].__line_coverage__:
            key = "%s-%s" % (lc_data["hash"], lc_data["id"])
            if key in coverage_line_keys:
                continue
            coverage_line_keys.append(key)
            coverage_line_list.append(lc_data)
    context["coverages"] = {
        "line": __update_line_coverage__(coverage_line_list, global_report_info.get("line_grate", 99)),
        "functional": __update_func_coverage__(coverage_func_list),
    }
    context["case_coverage"] = (
        __update_testcase_coverage__(context["tests"])
        if config.getoption("--case-coverage")
        else None
    )
    test_abstract_info = {}
    for test in context["tests"]:
        item = test["item"]
        key = _testcase_function_id(item) or _testcase_nodeid(item)
        test_abstract_info[key] = test["status"]["word"]
    context["test_abstract_info"] = test_abstract_info
    if config.option.toffee_report_dump_json:
        def default(o):
            try:
                return str(o)
            except:
                return None
        with open(os.path.join(__output_report_dir__, "toffee_report.json"), "w") as f:
            json.dump(context, f, indent=4, default=default)


def set_func_coverage(request, g):
    if not isinstance(g, list):
        g = [g]
    for i in g:
        assert isinstance(
            i, CovGroup
        ), "g should be an instance of CovGroup or list of CovGroup"
    request.node.__coverage_group__ = [str(x) for x in g]
    if request.scope == 'module':
        with FileLock(LOCK_FILE_FUNC_COV):
            session = request.session
            if not hasattr(session, "__coverage_group__"):
                session.__coverage_group__ = []
            session.__coverage_group__.extend([{
                "hash": "%s" % hash(g),
                "id": "H%s-P%s" % (uuid.getnode(), os.getpid()),
                "data": g,
            } for g in request.node.__coverage_group__])


def set_code_coverage(request, record: CoverageRecord | dict):
    if not isinstance(record, CoverageRecord):
        record = CoverageRecord.from_dict(record)
    else:
        record = record.normalized()
    data = record.to_dict()
    request.node.__line_coverage__ = json.dumps(data)
    if request.scope == 'module':
        with FileLock(LOCK_FILE_LINE_COV):
            session = request.session
            if not hasattr(session, "__line_coverage__"):
                session.__line_coverage__ = []
            session.__line_coverage__.append({
                "hash": "%s" % hash((record.simulator, record.database, record.test)),
                "id": "H%s-P%s" % (uuid.getnode(), os.getpid()),
                **data,
            })


def set_line_coverage(request, datfile, ignore: Union[list[str], str] = None):
    """Compatibility wrapper for legacy Verilator-only callers."""
    assert isinstance(datfile, str), "datfile should be a string"
    if ignore is None:
        ignore = []
    elif isinstance(ignore, str):
        ignore = [ignore]
    set_code_coverage(
        request,
        CoverageRecord(simulator="verilator", database=datfile, ignore=ignore),
    )


def process_func_coverage(item, call, report):
    if call.when != "teardown":
        return
    if hasattr(item, "__coverage_group__"):
        groups = []
        for g in item.__coverage_group__:
            assert isinstance(
                g, str
            ), "item.__coverage_group__ should be an instance of CovGroup"
            groups.append(
                {
                    "hash": "%s" % hash(g),
                    "id": "H%s-P%s" % (uuid.getnode(), os.getpid()),
                    "data": g,
                }
            )
        report.__coverage_group__ = groups
    if hasattr(item, "__line_coverage__"):
        assert isinstance(
            item.__line_coverage__, str
        ), "item.__line_coverage__ should be a string"
        line_data = json.loads(item.__line_coverage__)
        record = CoverageRecord.from_dict(line_data)
        report.__line_coverage__ = {
            "hash": "%s" % hash((record.simulator, record.database, record.test)),
            "id": "H%s-P%s" % (uuid.getnode(), os.getpid()),
            **record.to_dict(),
        }
    return report


def set_user_info(name, email):
    global __report_info__
    __report_info__["user"] = {"name": name, "email": email}


def set_title_info(title):
    global __report_info__
    __report_info__["title"] = title


def set_meta_info(key, value, is_del=False):
    global __report_info__
    if is_del:
        del __report_info__["meta"][key]
    else:
        __report_info__["meta"][key] = value


def set_line_good_rate(rate):
    global __report_info__
    __report_info__["line_grate"] = rate


def get_file_in_tmp_dir(request, workspace, filename,
                        max_tmp_history=1, new_path=False, tmp_prefix="toffee_tmp",
                        fix_timestamp=None
                        ):
    """
    Get or create a file path in temporary directory with version management and history cleanup.

    This function creates a unique temporary directory based on the test session start time
    and manages file versions within worker-specific subdirectories. It's primarily used for
    storing temporary files generated during testing, such as coverage data files.

    The function supports multi-process testing (pytest-xdist) by automatically detecting
    the worker ID and creating separate subdirectories for each worker within the main
    temporary directory to avoid conflicts.

    Directory Structure:
        workspace/
        ├── toffee_tmp_{timestamp}_{milliseconds}/
        │   ├── master/          # Single process files
        │   ├── gw0/            # Worker 0 files
        │   ├── gw1/            # Worker 1 files
        │   └── gw2/            # Worker 2 files
        └── toffee_tmp_{old_timestamp}/  # Old directories (cleaned up)

    Args:
        request: pytest request object containing session information
        workspace (str): workspace root directory path
        filename (str): target filename (including extension)
        max_tmp_history (int, optional): number of historical temporary directories to keep, default is 1
        new_path (bool, optional): whether to force creation of a new file path, default is False
        tmp_prefix (str, optional): prefix for temporary directory names, default is "toffee_tmp"
        fix_timestamp (float, optional): fixed timestamp for directory naming (for testing purposes), default is None

    Returns:
        str: complete file path in the worker-specific temporary subdirectory

    Raises:
        OSError: when unable to create directories or access the file system
        AttributeError: when request object lacks necessary attributes

    Examples:
        Single process:
        >>> get_file_in_tmp_dir(request, "/tmp", "coverage.dat")
        "/tmp/toffee_tmp_20231225120000123/master/coverage.dat"

        Multi-process (pytest-xdist):
        >>> get_file_in_tmp_dir(request, "/tmp", "test.log", new_path=True)
        "/tmp/toffee_tmp_20231225120000123/gw0/test1.log"
    """
    # Get worker ID for multi-process support (pytest-xdist)
    def get_worker_id():
        """Extract worker ID from environment or request object"""
        import os
        # Method 1: Environment variable (most reliable)
        worker_id = os.environ.get('PYTEST_XDIST_WORKER', None)
        if worker_id:
            return worker_id
        # Method 2: Check request object for worker info (only for real pytest objects)
        try:
            if hasattr(request, 'node') and hasattr(request.node, 'workerinput'):
                worker_attr = getattr(request.node.workerinput, 'workerid', None)
                if worker_attr and isinstance(worker_attr, str):
                    return worker_attr
            if hasattr(request, 'config') and hasattr(request.config, 'workerinput'):
                worker_attr = getattr(request.config.workerinput, 'workerid', None)
                if worker_attr and isinstance(worker_attr, str):
                    return worker_attr
        except (AttributeError, TypeError):
            # Ignore errors from mock objects or invalid attributes
            pass
        # Method 3: Fall back to master (single process)
        return 'master'
    # Extract session information and worker ID
    if not request:
        starttime = 0 # Used for checker
    else:
        starttime = request.config._toffee_test_start_time
    if fix_timestamp is not None:
        starttime = fix_timestamp
    starttime_str = datetime.fromtimestamp(starttime).strftime("%Y%m%d%H%M%S")
    min_sec = int((starttime * 1000) % 1000)
    worker_id = get_worker_id()
    is_worker_process = worker_id != 'master'
    # Create main temporary directory (shared across workers)
    tmp_dir = f"{tmp_prefix}_{starttime_str}_{min_sec:03d}"
    tmp_path = os.path.join(workspace, tmp_dir)
    # Create worker-specific subdirectory
    worker_path = os.path.join(tmp_path, worker_id)
    if not os.path.exists(workspace):
        try:
            os.makedirs(workspace, exist_ok=True)
        except OSError as e:
            raise OSError(f"create {workspace} fail: {e}")
    if not os.path.exists(tmp_path):
        try:
            # Only the master/single-process context may clean old shared tmp roots.
            # Workers must not remove sibling workers' coverage directories.
            if not is_worker_process:
                old_tmp_list = [
                    f for f in os.listdir(workspace)
                    if f.startswith(tmp_prefix + "_") and os.path.isdir(os.path.join(workspace, f))
                ]
                old_tmp_list.sort()  # Sort by name to ensure chronological order
                # Keep only the specified number of historical main directories
                while len(old_tmp_list) > max_tmp_history:
                    old_tmp = old_tmp_list.pop(0)
                    old_tmp_path = os.path.join(workspace, old_tmp)
                    try:
                        shutil.rmtree(old_tmp_path)
                    except Exception:
                        # Silent cleanup failure - don't interrupt main functionality
                        pass
        except OSError:
            # If workspace access fails, continue without cleanup
            pass
        try:
            os.makedirs(tmp_path, exist_ok=True)
        except OSError as e:
            raise OSError(f"create {tmp_path} fail: {e}")
    # Ensure worker-specific subdirectory exists
    if not os.path.exists(worker_path):
        try:
            os.makedirs(worker_path, exist_ok=True)
        except OSError as e:
            raise OSError(f"create worker directory {worker_path} fail: {e}")
    name, ext = os.path.splitext(filename)
    try:
        old_files = [
            f for f in os.listdir(worker_path)
            if f.startswith(name) and (not ext or f.endswith(ext))
        ]
        old_files.sort()
    except OSError:
        old_files = []
    if not new_path and old_files:
        target_filename = os.path.join(worker_path, old_files[-1])
    else:
        # For regular files, use existing logic
        index = len(old_files)
        if index == 0:
            target_filename = os.path.join(worker_path, f"{name}{ext}")
        else:
            target_filename = os.path.join(worker_path, f"{name}{index:03d}{ext}")
    return target_filename
