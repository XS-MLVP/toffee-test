__all__ = ["verilator_coverage_miss"]

from typing import TypedDict, Optional, NamedTuple, Union
from collections import Counter, deque


class CoveragePoint(NamedTuple):
    type: str
    line: int
    column: int
    comment: str
    block: Optional[Union[int, range]]


def _parse_line(line: str) -> deque[tuple[str, str]]:
    q = deque()
    soh = line.find("\x01")
    while soh > -1:
        stx = line.find("\x02", soh + 1)
        key = line[soh + 1:stx]
        soh = line.find("\x01", stx + 1)
        value = line[stx + 1:soh]
        q.append((key, value))
    return q


def _set_coverage_point(q: deque[tuple[str, str]]) -> CoveragePoint:
    _type = ""
    _line = 0
    _column = 0
    _comment = ""
    _block = None
    for k, v in q:
        if k == "l":
            _line = int(v)
        elif k == "n":
            _column = int(v)
        elif k == "page":
            t = v.split("/")[0].lstrip("v_")
            _type = t
        elif k == "o":
            _comment = v
        elif k == "S":
            if "-" in v:
                l, r = map(int, v.split("-"))
                _block = range(l, r + 1)
            elif "," in v:
                l, r = map(int, v.split(","))
                _block = range(l, r + 1)
            else:
                _block = int(v)
    return CoveragePoint(type=_type, line=_line, column=_column, comment=_comment, block=_block)


def verilator_coverage_miss(merged_coverage: list[tuple[str, int]], out_file: str) -> None:
    miss_coverage = [entry for entry, hit in merged_coverage if hit == 0]
    if not miss_coverage:
        return
    miss_coverage.sort()
    # Parse missing coverage
    coverages = [_parse_line(c) for c in miss_coverage]
    # Parse file
    miss_tree: dict[str, dict[str, Union[set[int], list[Union[str, int]]]]] = {}
    for c in coverages:
        # Lookup path
        path = c.popleft()[-1]
        # The first level of miss_tree is rtl_path
        last_path = next(reversed(miss_tree), None)
        if last_path != path:
            miss_tree[path] = {}
        # The second level of miss_tree is module name
        hierarchy = c.pop()[-1]
        module_name = hierarchy[hierarchy.rfind(".") + 1:]
        p = _set_coverage_point(c)
        if module_name not in miss_tree[path]:
            miss_tree[path][module_name] = {p.line}
        elif p.block is not None and isinstance(p.block, range):
            miss_tree[path][module_name].update(p.block)
        else:
            miss_tree[path][module_name].add(p.line)

    for file in miss_tree:
        for module in miss_tree[file]:
            sorted_line = sorted(miss_tree[file][module])
            final_line: list[Union[int, str]] = []
            i = 0
            while i < len(sorted_line):
                l = sorted_line[i]
                j = i
                while j + 1 < len(sorted_line) and sorted_line[j + 1] == sorted_line[j] + 1:
                    j += 1
                if i == j:
                    final_line.append(l)
                else:
                    r = sorted_line[j]
                    final_line.append(f"{l}-{r}")
                i = j + 1
            miss_tree[file][module] = final_line

    structure = {"file_path": {"module": ["line"]}}
    desc = ""
    empty_coverage = {
        "description": desc,
        "miss/total": f"{len(miss_coverage)}/{len(merged_coverage)}",
        "simulator": "verilator",
        "structure": structure,
        "uncovered": miss_tree,
    }
    # Export json
    import json
    with open(out_file, "w") as f:
        json.dump(empty_coverage, f, indent=2)
