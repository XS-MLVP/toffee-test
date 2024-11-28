import json
import os
import uuid
from collections import Counter
from datetime import datetime
from filelock import FileLock
from toffee.funcov import CovGroup

from .utils import convert_line_coverage, get_toffee_custom_key_value


LOCK_FILE_FUNC_COV = "/tmp/toffee_func_cov.lock"
LOCK_FILE_LINE_COV = "/tmp/toffee_line_cov.lock"

def get_default_report_name():
    current_time = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"report-{current_time}/report-{current_time}.html"


def get_template_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


__output_report_dir__ = None


def set_output_report(report_path):
    report_dir = os.path.dirname(report_path)
    if not os.path.exists(report_dir):
        os.makedirs(report_dir)

    global __output_report_dir__
    __output_report_dir__ = report_dir


def __update_line_coverage__(line_coverage_list=[], line_grate=99):
    if line_coverage_list is None:
        return None
    if len(line_coverage_list) == 0:
        return None
    (hint, all), ignore = convert_line_coverage(
        line_coverage_list, os.path.join(__output_report_dir__, "line_dat")
    )
    assert os.path.exists(
        os.path.join(__output_report_dir__, "line_dat/index.html")
    ), "Failed to convert line coverage"

    return {
        "hints": hint,
        "total": all,
        "grate": __report_info__.get("line_grate", line_grate),
        "ignore": ignore
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
                    elif key == "functions":
                        for k, v in value.items():
                            if k in result[key]:
                                result[key][k] = list(set(result[key][k] + v))
                            else:
                                result[key][k] = v
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


def set_line_coverage(request, datfile, ignore=[]):
    assert isinstance(datfile, str), "datfile should be a string"
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


def set_user_info(name, code):
    global __report_info__
    __report_info__["user"] = {"name": name, "code": code}


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
