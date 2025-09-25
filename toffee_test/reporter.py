import json
import os
import uuid
from collections import Counter
from datetime import datetime
from typing import Union
import shutil

from filelock import FileLock
from toffee.funcov import CovGroup, get_func_full_name

from .utils import convert_line_coverage, get_toffee_custom_key_value


LOCK_FILE_FUNC_COV = "/tmp/toffee_func_cov.lock"
LOCK_FILE_LINE_COV = "/tmp/toffee_line_cov.lock"

def get_default_report_name():
    current_time = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"report-{current_time}/report-{current_time}.html"


def get_template_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


__output_report_dir__ = ""


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
    line_dat_dir = os.path.join(__output_report_dir__, "line_dat")
    try:
        (hint, total), ignore = convert_line_coverage(
            line_coverage_list, line_dat_dir
        )
    except Exception as e:
        from toffee.logger import error
        hint = total = 0
        ignore = []
        coverage_error = repr(e)
        error(coverage_error)

    return {
        "hints": hint,
        "total": total,
        "grate": __report_info__.get("line_grate", line_grate),
        "ignore": ignore,
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

    for t in context["tests"]:
        for p in t["phases"]:
            if hasattr(p["report"], "__coverage_group__"):
                for fc_data in p["report"].__coverage_group__:
                    key = "%s-%s" % (fc_data["hash"], fc_data["id"])
                    if key in coverage_func_keys:
                        continue
                    coverage_func_keys.append(key)
                    coverage_func_list.append(fc_data["data"])
            if hasattr(p["report"], "__line_coverage__"):
                lc_data = p["report"].__line_coverage__
                key = "%s-%s" % (lc_data["hash"], lc_data["id"])
                if key in coverage_line_keys:
                    continue
                coverage_line_keys.append(key)
                coverage_line_list.append(lc_data)
    # search data in session
    if hasattr(context["session"], "__coverage_group__"):
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
    test_abstract_info = {
        get_func_full_name(t["item"].function):t["status"]["word"] for t in context["tests"]
    }
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


def set_line_coverage(request, datfile, ignore: Union[list[str], str] = None):
    assert isinstance(datfile, str), "datfile should be a string"
    if ignore is None:
        ignore = []
    elif isinstance(ignore, str):
        ignore = [ignore]
    request.node.__line_coverage__ = json.dumps({"datfile":datfile, "ignore": ignore})
    if request.scope == 'module':
        with FileLock(LOCK_FILE_LINE_COV):
            session = request.session
            if not hasattr(session, "__line_coverage__"):
                session.__line_coverage__ = []
            session.__line_coverage__.append({
                "hash": "%s" % hash(datfile),
                "id": "H%s-P%s" % (uuid.getnode(), os.getpid()),
                "data": datfile,
                "ignore": ignore,
            })


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
        datfile = line_data["datfile"]
        ignore = line_data["ignore"]
        report.__line_coverage__ = {
            "hash": "%s" % hash(datfile),
            "id": "H%s-P%s" % (uuid.getnode(), os.getpid()),
            "data": datfile,
            "ignore": ignore,
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


def get_file_in_tmp_dir(request, workspace, filename, max_tmp_history=1, new_path=False, tmp_prefix = "toffee_tmp"):
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
    starttime = request.config._toffee_test_start_time
    starttime_str = datetime.fromtimestamp(starttime).strftime("%Y%m%d%H%M%S")
    min_sec = int((starttime * 1000) % 1000)
    worker_id = get_worker_id()
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
            # Clean up old main temporary directories (not worker-specific)
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
                except Exception as e:
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
