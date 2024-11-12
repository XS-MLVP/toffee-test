
import sys
import re
import fnmatch
from .utils import get_toffee_custom_key_value


def toffee_tags_process(item):
    '''toffee_tags(tag: Optional[list, str],         # eg: ["tag1", "tag2"], "tag1"
                   version: Optional[list,str] = [], # eg: ["v1", "v2"], "v1+", "v1-", "v1<v2"
                   skip=None                         # skip(tag, version, item): (skip, reason)
                   )
        example:
        @pytest.mark.toffee_tags(["tag1", "tag2"], ["kmh-tag-number1", "kmh-tag-number2"])
        def test_case1(...):
            pass
    '''
    import pytest
    marker = item.get_closest_marker("toffee_tags")
    kwargs = {
        "item": item,
    }
    need_args = ["tag", "version", "skip"]
    if marker:
        assert len(marker.args) < len(need_args), "Too many args, only need 3 (tag_list, version_list, skip_call_back)"
        for i, arg in enumerate(marker.args):
            kwargs[need_args[i]] = arg
        for key, value in marker.kwargs.items():
            assert key in need_args, f"Unknown key {key}"
            assert key not in kwargs, f"Duplicate args {key}"
            kwargs[key] = value        
    else:
        for arg in need_args:
            kwargs[arg] = getattr(item.module, "toffee_tags_default_%s"%arg, None)
    skip, reason = skip_process_test_tag_version(**kwargs)
    if skip:
        pytest.skip(reason)
    skip, reason = skip_process_test_cases(item.name, item.module.__name__)
    if skip:
        pytest.skip(reason)


def grep_last_number(s: str):
    m = list(re.finditer(r'(\d+(\.\d+)?)(?!.*\d)', s))
    if m:
        lm = m[-1]
        return float(lm.group(1)), s[:lm.start()]
    return None, ""


def match_version(version, version_list):
    if not version:
        return True
    version = version.strip()
    if not version_list:
        return True
    if isinstance(version_list, list):
        if len(version_list) == 0:
            return True
        return version in version_list
    assert isinstance(version_list, str), "version_list must be list or str"
    version_list = version_list.strip()
    if version in version_list:
        return True
    if "*" in version_list or "?" in version_list:
        return fnmatch.fnmatch(version, version_list)
    version_range = [-sys.maxsize, sys.maxsize]
    value, prefix = grep_last_number(version)
    # check prefix
    if "<" in version_list:
        a, b = version_list.split("<")
        if not a.strip().startswith(prefix):
            return False
        if not b.strip().startswith(prefix):
            return False
    elif not version_list.startswith(prefix):
        return False
    try:
        if "<" in version_list:
            vlist = version_list.split("<")
            assert len(vlist) == 2, "Invalid version range"
            version_range[0], _ = grep_last_number(vlist[0].strip())
            version_range[1], _ = grep_last_number(vlist[1].strip())
        else:
            target_value, _ = grep_last_number(version_list)
            if version_list.endswith("+"):
                version_range[0] = target_value
            elif version_list.endswith("-"):
                version_range[1] = target_value
            else:
                return value == target_value
    except Exception as e:
        assert False, f"Invalid version format '{version_list}', error: {e}, can not find right version number"
    return version_range[0] <= float(value) <= version_range[1]


def match_tags(source_tags, target_tags):
    tmp_normal_tags = []
    tmp_wildcard_tags = []
    if not target_tags:
        return False
    if not source_tags:
        return False
    for t in target_tags:
        t = t.strip()
        if "*" in t or "?" in t:
            tmp_wildcard_tags.append(t)
        else:
            tmp_normal_tags.append(t)
    for t in source_tags:
        t = t.strip()
        if t in tmp_normal_tags:
            return t
        for wt in tmp_wildcard_tags:
            if fnmatch.fnmatch(t, wt):
                return wt
    return False


def skip_process_test_tag_version(tag=[], version=[], skip=None, item=None):
    if isinstance(tag, str):
        tag = [tag]
    if callable(skip):
        assert item, "Case item must be provided, please dont call this function directly, use @pytest.mark.toffee_tags"
        return skip(tag, version, item)
    current_version = get_toffee_custom_key_value().get("toffee_tags_current_version", None)
    skip_tags = get_toffee_custom_key_value().get("toffee_tags_skip_tags", [])
    run_tags =  get_toffee_custom_key_value().get("toffee_tags_run_tags", [])
    if not match_version(current_version, version):
        return True, f"In Skiped version, '{current_version}' not match: '{version}'"
    tag = match_tags(tag, skip_tags)
    if tag:
        return True, f"In Skiped tags: '{tag}'"
    tag = match_tags(tag, run_tags)
    if not tag and len(run_tags) > 0:
        return True, f"No matched tags"
    return False, ""

def skip_process_test_cases(name, module):
    skip_cases = get_toffee_custom_key_value().get("toffee_tags_skip_cases", [])
    run_cases = get_toffee_custom_key_value().get("toffee_tags_run_cases", [])
    c = match_tags([name, module, "%s.%s"%(module, name)], skip_cases)
    if c:
        return True, f"In Skiped cases: '{c}'"
    c = match_tags([name, module, "%s.%s"%(module, name)], run_cases)
    if not c and len(run_cases) > 0:
        return True, f"No matched cases"
    return False, ""
