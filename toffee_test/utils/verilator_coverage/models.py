__all__ = [
    "VerilatorCoverage",
    "CoveragePoint",
    "MetricStats",
    "ModuleCoverage",
    "FileCoverage",
]

import re
from dataclasses import dataclass, field


class VerilatorCoverage:
    __slots__ = (
        'path', 'line', 'column', 'type', 'module_name',
        'comment', 'block', 'hierarchy',
        '_has_type_field', '_has_comment', '_has_blocks'
    )

    _RE_PARSER = re.compile(r"\x01([^\x02]+)\x02([^\x01]*)")

    def __init__(self, raw: str):
        self.comment = ""
        self.block = []
        self._has_type_field = False
        self._has_comment = False
        self._has_blocks = False

        for k, v in self._RE_PARSER.findall(raw):
            if k == "f":
                self.path = v
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
                self.block = block_list
                self._has_blocks = True
            elif k == "h":
                self.hierarchy = v

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
            for b in self.block:
                if b.start + 1 == b.stop:
                    block_parts.append(str(b.start))
                else:
                    block_parts.append(f"{b.start}-{b.stop - 1}")
            fields.append(f"\x01S\x02{','.join(block_parts)}")

        fields.append(f"\x01h\x02{self.hierarchy}")

        return ''.join(fields)


@dataclass
class CoveragePoint:
    module: str = None
    type: str = None
    line: int = None
    column: int = None
    comment: str = None
    block: set[int] = field(default_factory=set)


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
