__all__ = [
    "VerilatorCoverage",
    "MetricStats",
    "ModuleCoverage",
    "FileCoverage",
    "CoverageSummary"
]

import re
from dataclasses import dataclass, field
from typing import NamedTuple


class VerilatorCoverage:
    __slots__ = (
        'path', 'line', 'column', 'type', 'module_name',
        'comment', '_block', 'hierarchy',
        '_has_type_field', '_has_comment', '_has_blocks',
        '_block_set_cache_valid', '_block_set'
    )

    _RE_PARSER = re.compile(r"\x01([^\x02]+)\x02([^\x01]*)")

    def __init__(self, raw: str):
        self.comment = ""
        self._block: list[range] = []
        self._has_type_field = False
        self._has_comment = False
        self._has_blocks = False
        self._block_set_cache_valid = False
        self._block_set: set[int] = set()

        for k, v in self._RE_PARSER.findall(raw):
            if k == "f":
                self.path: str = v
            elif k == "l":
                self.line = int(v)
            elif k == "n":
                self.column = int(v)
            elif k == "t":
                self._has_type_field = True
            elif k == "page":
                type_part, module_part = v.split("/")
                self.type = type_part.lstrip("v_")
                self.module_name = module_part
            elif k == "o":
                self.comment = v
                self._has_comment = True
            elif k == "S":
                block_list = []
                for b in v.split(","):
                    if "-" in b:
                        l, r = map(int, b.split("-"))
                        block_list.append(range(l, r + 1))
                    else:
                        num = int(b)
                        block_list.append(range(num, num + 1))
                self._block = block_list
                self._has_blocks = True
            elif k == "h":
                self.hierarchy = v

    @property
    def block_set(self) -> set[int]:
        if not self._has_blocks:
            return set()
        elif not self._block_set_cache_valid:
            self._block_set_cache_valid = True
            for b in self._block:
                self._block_set.update(b)
        return self._block_set

    @property
    def block(self):
        return self._block

    @block.setter
    def block(self, new_blocks: list[range]):
        if not self._has_blocks or not new_blocks:
            return
        self._block_set_cache_valid = False
        self._block_set.clear()
        self._block = new_blocks

    def __lt__(self, other):
        x = (self.path, self.line, self.column, self.type, self.module_name)
        y = (other.path, other.line, other.column, other.type, other.module_name)
        return x < y

    def __str__(self):
        fields = [
            f"\x01f\x02{self.path}",
            f"\x01l\x02{self.line}",
            f"\x01n\x02{self.column}"
        ]

        if self._has_type_field:
            fields.append(f"\x01t\x02{self.type}")

        fields.append(f"\x01page\x02v_{self.type}/{self.module_name}")

        if self._has_comment:
            fields.append(f"\x01o\x02{self.comment}")

        if self._has_blocks:
            block_parts = []
            for b in self._block:
                if b.start + 1 == b.stop:
                    block_parts.append(str(b.start))
                else:
                    block_parts.append(f"{b.start}-{b.stop - 1}")
            fields.append(f"\x01S\x02{','.join(block_parts)}")

        fields.append(f"\x01h\x02{self.hierarchy}")

        return ''.join(fields)


@dataclass
class MetricStats:
    line: int = 0
    toggle: int = 0
    branch: int = 0
    expr: int = 0


@dataclass
class ModuleCoverage:
    total: MetricStats = field(default_factory=MetricStats)
    miss: MetricStats = field(default_factory=MetricStats)
    line: set[int] = field(default_factory=set)
    toggle: set[int] = field(default_factory=set)
    branch: set[int] = field(default_factory=set)
    expr: set[int] = field(default_factory=set)

    def get_metric_set(self, metric_type: str) -> set[int]:
        metric_map = {
            "line": self.line,
            "toggle": self.toggle,
            "branch": self.branch,
            "expr": self.expr,
        }
        return metric_map.get(metric_type, None)


@dataclass
class FileCoverage:
    total: MetricStats = field(default_factory=MetricStats)
    miss: MetricStats = field(default_factory=MetricStats)
    modules: dict[str, ModuleCoverage] = field(default_factory=dict)


class CoverageSummary(NamedTuple):
    description: str
    simulator: str
    overview: dict[str, dict]
    uncovered: dict[str, dict]
