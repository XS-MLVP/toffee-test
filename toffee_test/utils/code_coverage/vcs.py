from __future__ import annotations

import os
import json
import fnmatch
import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .models import CodeCoverageResult, CoverageRecord, MetricSummary


def find_urg_entry(output_dir: Path) -> Path | None:
    for name in ("dashboard.html", "index.html", "hierarchy.html"):
        candidate = output_dir / name
        if candidate.is_file():
            return candidate
    html_files = sorted(output_dir.rglob("*.html"))
    return html_files[0] if html_files else None


def _resolve_urg(urg: str | None, vcs_home: str | None) -> str:
    if urg:
        return urg
    home = vcs_home or os.environ.get("VCS_HOME")
    if home:
        candidate = Path(home) / "bin" / "urg"
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("urg")
    if found:
        return found
    raise FileNotFoundError("URG was not found; set VCS_HOME or add urg to PATH")


def _resolve_helper(records: list[CoverageRecord], helper: str | None) -> str:
    if helper:
        candidate = Path(helper)
        if candidate.is_file():
            return str(candidate)
        raise FileNotFoundError(f"picker coverage helper was not found: '{candidate}'")

    for record in reversed(records):
        if record.helper and Path(record.helper).is_file():
            return str(Path(record.helper))

    for record in reversed(records):
        if not record.summary:
            continue
        summary = Path(record.summary)
        if not summary.is_file():
            continue
        try:
            raw = json.loads(summary.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        candidate = Path(str((raw.get("provider") or {}).get("helper") or ""))
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError(
        "picker coverage helper was not recorded; regenerate the DUT package and coverage summaries"
    )


def _merge_ranges(lines: set[int]) -> list[str]:
    ordered = sorted(line for line in lines if line > 0)
    if not ordered:
        return []
    ranges: list[str] = []
    start = end = ordered[0]
    for line in ordered[1:]:
        if line == end + 1:
            end = line
            continue
        ranges.append(f"{start}-{end}")
        start = end = line
    ranges.append(f"{start}-{end}")
    return ranges


def _merge_bit_ranges(bits: set[int]) -> list[str]:
    """Merge observed numeric bit indices into inclusive ranges."""
    ordered = sorted(bit for bit in bits if bit >= 0)
    if not ordered:
        return []
    ranges: list[str] = []
    start = end = ordered[0]
    for bit in ordered[1:]:
        if bit == end + 1:
            end = bit
            continue
        ranges.append(f"{start}-{end}")
        start = end = bit
    ranges.append(f"{start}-{end}")
    return ranges


def _split_bit_object(value: str) -> tuple[str, int | None]:
    """Return a base signal and final numeric bit select when it is unambiguous."""
    text = str(value or "")
    if text.endswith("]") and "[" in text:
        base, bit = text.rsplit("[", 1)
        bit = bit[:-1]
        if base and bit.isdigit():
            return base, int(bit)
    return text, None


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _build_atom_inventory(raw: dict[str, Any], items: list[dict[str, Any]], detail_kind: str) -> dict[str, Any]:
    """Preserve exact provider identity and publish deterministic group membership."""
    provider = raw.get("provider") or {}
    provider_identity = str(provider.get("identity") or "picker.vcs.ucapi")
    provider_schema_version = int(raw.get("schema_version") or 0)
    identity_fields = []
    for item in [*(raw.get("items") or []), *(raw.get("covered_items") or [])]:
        identity_fields.append({
            key: item.get(key) for key in
            ("kind", "file", "module", "instance", "line", "column", "object", "detail")
        })
    design_revision = _canonical_hash(sorted(identity_fields, key=lambda value: json.dumps(value, sort_keys=True)))
    atoms: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    groups: dict[str, dict[str, Any]] = {}
    for item in items:
        metric = str(item.get("kind") or "")
        raw_object = str(item.get("object") or "")
        object_name, bit = _split_bit_object(raw_object) if metric == "toggle" else (raw_object, None)
        anchor_payload = {
            "file_logical_id": Path(str(item.get("file") or "")).name,
            "module": str(item.get("module") or ""),
            "object": object_name,
            "metric": metric,
            "line_fallback": int(item.get("line") or 0),
            "column_fallback": int(item.get("column") or 0),
        }
        identity = {
            "provider_identity": provider_identity,
            "design_revision": design_revision,
            "file_logical_id": anchor_payload["file_logical_id"],
            "module": anchor_payload["module"],
            "instance": str(item.get("instance") or ""),
            "normalized_object": object_name,
            "metric": metric,
            "bit": bit,
            "transition_or_arm": str(item.get("detail") or ""),
            "structural_source_anchor": _canonical_hash(anchor_payload),
        }
        atom_id = "atom:" + _canonical_hash(identity)
        if atom_id in atoms:
            duplicates.add(atom_id)
            continue
        atom = {
            "atom_id": atom_id,
            "identity": identity,
            "source_location": {
                "file": str(item.get("file") or ""), "line": int(item.get("line") or 0),
                "column": int(item.get("column") or 0),
            },
            "observation": detail_kind,
        }
        atoms[atom_id] = atom
        group_identity = {key: value for key, value in identity.items() if key != "bit"}
        group_key = "group:" + _canonical_hash(group_identity)
        groups.setdefault(group_key, {"group_key": group_key, "identity": group_identity, "members": []})["members"].append(atom_id)
    output_groups = []
    for group in groups.values():
        group["members"].sort()
        group["atom_count"] = len(group["members"])
        group["member_hash"] = _canonical_hash(group["members"])
        output_groups.append(group)
    ordered_atoms = [atoms[key] for key in sorted(atoms)]
    enumerated_by_metric: dict[str, int] = {}
    for atom in ordered_atoms:
        metric = atom["identity"]["metric"]
        enumerated_by_metric[metric] = enumerated_by_metric.get(metric, 0) + 1
    expected_by_metric = {}
    for metric, value in ((raw.get("summary") or {}).get("metrics") or {}).items():
        total = int(value.get("total", 0)); uncovered = int(value.get("uncovered", total-int(value.get("covered", 0))))
        expected_by_metric[metric] = total-uncovered if detail_kind == "covered" else uncovered
    unresolved = [
        {"metric": metric, "provider_metric_items": expected_by_metric.get(metric, 0),
         "enumerated_atoms": enumerated_by_metric.get(metric, 0)}
        for metric in sorted(set(expected_by_metric) | set(enumerated_by_metric))
        if expected_by_metric.get(metric, 0) != enumerated_by_metric.get(metric, 0)
    ]
    return {
        "schema_version": 1,
        "provider_identity": provider_identity,
        "provider_schema_version": provider_schema_version,
        "toffee_schema_version": 3,
        "design_revision": design_revision,
        "observation": detail_kind,
        "atoms": ordered_atoms,
        "groups": sorted(output_groups, key=lambda value: value["group_key"]),
        "reconciliation": {
            "provider_item_count": len(items), "enumerated_atom_count": len(ordered_atoms),
            "atom_identity_hash": _canonical_hash(sorted(atoms)),
            "duplicate_identities": sorted(duplicates), "unresolved": unresolved,
        },
    }


def _metric_maps(metrics: dict[str, Any]) -> tuple[dict[str, int], dict[str, int]]:
    total: dict[str, int] = {}
    miss: dict[str, int] = {}
    for name in ("line", "toggle", "branch"):
        if name not in metrics:
            continue
        value = metrics[name]
        total[name] = int(value.get("total", 0))
        covered = int(value.get("covered", 0))
        miss[name] = int(value.get("uncovered", total[name] - covered))
    return total, miss


def _read_ignore_rules(values: list[str]) -> tuple[list[str], dict[str, set[int]]]:
    patterns: list[str] = []
    line_ranges: dict[str, set[int]] = {}

    def add_rule(rule: str) -> None:
        rule = rule.strip()
        if not rule or rule.startswith("#"):
            return
        rule = rule.split("#", 1)[0].strip()
        separator = rule.rfind(":")
        if separator > 0:
            ranges = rule[separator + 1:]
            try:
                lines: set[int] = set()
                for value in ranges.split(","):
                    if "-" in value:
                        start, end = (int(part) for part in value.split("-", 1))
                        lines.update(range(start, end + 1))
                    else:
                        lines.add(int(value))
                line_ranges.setdefault(rule[:separator], set()).update(lines)
                return
            except ValueError:
                pass
        patterns.append(rule)

    for value in values:
        path = Path(value)
        if path.is_dir():
            for ignore_file in sorted(path.rglob("*.ignore")):
                for line in ignore_file.read_text(encoding="utf-8").splitlines():
                    add_rule(line)
        elif path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                add_rule(line)
        else:
            add_rule(value)
    return sorted(set(patterns)), line_ranges


def _path_matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern) or path.endswith(pattern)


def _update_rate(metric: dict[str, Any]) -> None:
    total = int(metric.get("total", 0))
    covered = int(metric.get("covered", 0))
    metric["uncovered"] = total - covered
    metric["rate"] = 100.0 * covered / total if total else 0.0


def _subtract_metrics(target: dict[str, Any], values: dict[str, Any]) -> None:
    for name, value in values.items():
        if name not in target:
            continue
        target[name]["total"] = max(0, int(target[name].get("total", 0)) - int(value.get("total", 0)))
        target[name]["covered"] = max(0, int(target[name].get("covered", 0)) - int(value.get("covered", 0)))
        _update_rate(target[name])


def _apply_ignores(raw: dict[str, Any], ignore: list[str]) -> tuple[list[str], dict[str, set[int]]]:
    patterns, line_ranges = _read_ignore_rules(ignore)
    files = (raw.get("breakdown") or {}).get("files") or {}
    overview = (raw.get("summary") or {}).get("metrics") or {}

    excluded_files = [path for path in files if any(_path_matches(path, pattern) for pattern in patterns)]
    for path in excluded_files:
        _subtract_metrics(overview, files[path].get("metrics") or {})
        del files[path]

    def ignored_line(item: dict[str, Any]) -> bool:
        path = str(item.get("file") or "")
        if path in excluded_files:
            return True
        line = int(item.get("line", 0))
        return any(line in lines and _path_matches(path, pattern) for pattern, lines in line_ranges.items())

    def identity(item: dict[str, Any]) -> tuple[Any, ...]:
        return tuple(
            item.get(name)
            for name in ("kind", "file", "module", "instance", "line", "column", "object", "detail")
        )

    ignored_items: dict[tuple[Any, ...], dict[str, Any]] = {}
    for field in ("items", "covered_items"):
        kept_items = []
        for item in raw.get(field) or []:
            if not ignored_line(item):
                kept_items.append(item)
                continue
            path = str(item.get("file") or "")
            if path in excluded_files:
                continue
            key = identity(item)
            previous = ignored_items.get(key)
            if previous is None or int(item.get("covered", 0)) > int(previous.get("covered", 0)):
                ignored_items[key] = item
        raw[field] = kept_items

    for item in ignored_items.values():
        path = str(item.get("file") or "")
        module = str(item.get("module") or "")
        kind = str(item.get("kind") or "")
        total_delta = max(1, int(item.get("coverable", 1)))
        covered_delta = max(0, min(total_delta, int(item.get("covered", 0))))
        for metrics in (
            overview,
            (files.get(path) or {}).get("metrics") or {},
            (((files.get(path) or {}).get("modules") or {}).get(module) or {}).get("metrics") or {},
        ):
            metric = metrics.get(kind)
            if metric:
                metric["total"] = max(0, int(metric.get("total", 0)) - total_delta)
                metric["covered"] = max(0, int(metric.get("covered", 0)) - covered_delta)
                _update_rate(metric)
    raw["summary"]["total_uncovered"] = len(raw.get("items") or [])
    return patterns, line_ranges


def _build_code_coverage_report(
    raw: dict[str, Any],
    database: str,
    tests: list[str],
    html_report: str,
    excludes: list[str],
    detail_kind: str = "uncovered",
) -> dict[str, Any]:
    if detail_kind not in {"covered", "uncovered"}:
        raise ValueError(f"unsupported coverage detail kind: {detail_kind}")
    is_covered = detail_kind == "covered"
    count_key = "covered" if is_covered else "miss"
    items_key = "covered_items" if is_covered else "items"
    item_adjective = "covered" if is_covered else "missing"
    raw_metrics = (raw.get("summary") or {}).get("metrics") or {}
    overview_total, overview_miss = _metric_maps(raw_metrics)
    overview_values = (
        {name: total - overview_miss[name] for name, total in overview_total.items()}
        if is_covered
        else overview_miss
    )
    items = raw.get(items_key) or []
    items_by_scope: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        key = (str(item.get("file") or ""), str(item.get("module") or ""))
        items_by_scope.setdefault(key, []).append(item)

    files: dict[str, Any] = {}
    raw_files = (raw.get("breakdown") or {}).get("files") or {}
    for file_path in sorted(raw_files):
        raw_file = raw_files[file_path]
        file_total, file_miss = _metric_maps(raw_file.get("metrics") or {})
        file_values = (
            {name: total - file_miss[name] for name, total in file_total.items()}
            if is_covered
            else file_miss
        )
        modules: dict[str, Any] = {}
        for module_name in sorted((raw_file.get("modules") or {})):
            raw_module = raw_file["modules"][module_name]
            module_total, module_miss = _metric_maps(raw_module.get("metrics") or {})
            module_values = (
                {name: total - module_miss[name] for name, total in module_total.items()}
                if is_covered
                else module_miss
            )
            scoped_items = items_by_scope.get((file_path, module_name), [])
            line_ranges = _merge_ranges(
                {
                    int(item.get("line", 0))
                    for item in scoped_items
                    if item.get("kind") == "line"
                }
            )

            toggle_groups: dict[tuple[int, int, str], dict[str, set[int | None]]] = {}
            branch_groups: dict[tuple[int, int, str], set[str]] = {}
            for item in scoped_items:
                key = (
                    int(item.get("line", 0)),
                    int(item.get("column", 0)),
                    str(item.get("object") or ""),
                )
                if item.get("kind") == "toggle":
                    original_object = str(item.get("object") or "")
                    base, bit = _split_bit_object(original_object)
                    toggle_key = (key[0], key[1], base if bit is not None else original_object)
                    transitions = toggle_groups.setdefault(toggle_key, {})
                    transitions.setdefault(str(item.get("detail") or ""), set()).add(bit)
                elif item.get("kind") == "branch":
                    branch_groups.setdefault(key, set()).add(str(item.get("detail") or ""))

            toggles = []
            for (line, column, obj), transitions in sorted(toggle_groups.items()):
                values = sorted(transitions)
                numeric_bits = {bit for bits in transitions.values() for bit in bits if bit is not None}
                exact_objects = any(bit is None for bits in transitions.values() for bit in bits)
                item = {
                    "line": line,
                    "column": column,
                    "object": obj,
                    "transitions": values,
                    "atom_count": sum(len(bits) for bits in transitions.values()),
                }
                if numeric_bits and not exact_objects:
                    item["aggregation"] = "base_signal"
                    item["bits"] = {
                        transition: _merge_bit_ranges({bit for bit in bits if bit is not None})
                        for transition, bits in sorted(transitions.items())
                    }
                else:
                    item["aggregation"] = "exact_object"
                toggles.append(item)
            branches = [
                {"line": line, "column": column, "object": obj, "arms": sorted(values), "atom_count": len(values)}
                for (line, column, obj), values in sorted(branch_groups.items())
            ]
            modules[module_name] = {
                "total": module_total,
                count_key: module_values,
                "line": line_ranges,
                "toggle": toggles,
                "branch": branches,
            }
        files[file_path] = {
            "total": file_total,
            count_key: file_values,
            "modules": modules,
        }

    atom_inventory = _build_atom_inventory(raw, items, detail_kind)
    detail_counts = {"line": 0, "toggle": 0, "branch": 0}
    group_counts = {"line": 0, "toggle": 0, "branch": 0}
    for atom in atom_inventory["atoms"]:
        detail_counts[atom["identity"]["metric"]] += 1
    for group in atom_inventory["groups"]:
        group_counts[group["identity"]["metric"]] += 1
    metric_items = overview_values
    detail_summary = {
        name: {
            "metric_items": int(metric_items.get(name, 0)),
            "enumerated_atoms": detail_counts.get(name, 0),
            "gap_count": group_counts.get(name, 0),
            "unresolved": abs(int(metric_items.get(name, 0)) - detail_counts.get(name, 0)),
        }
        for name in ("line", "toggle", "branch")
        if name in overview_total
    }

    return {
        "schema_version": 3,
        "description": f"Code coverage summary. `{detail_kind}.data` contains {detail_kind} items.",
        "simulator": "vcs",
        "metadata": {
            "database": database,
            "tests": tests,
            "exclude": excludes,
            "html": html_report,
            "detail_level": {
                "line": "range",
                "toggle": "base_signal_transitions",
                "branch": "object_arms",
            },
        },
        "overview": {
            "total": overview_total,
            count_key: overview_values,
        },
        "detail_summary": detail_summary,
        "atom_inventory": atom_inventory,
        detail_kind: {
            "schema": {
                "file_path": {
                    "total": ["line", "toggle", "branch"],
                    count_key: ["line", "toggle", "branch"],
                    "modules": {
                        "module_name": {
                            "total": ["line", "toggle", "branch"],
                            count_key: ["line", "toggle", "branch"],
                            "line": ["inclusive start-end ranges"],
                            "toggle": [f"base signal objects with {item_adjective} transitions and bit ranges"],
                            "branch": [f"branch objects with {item_adjective} arms"],
                        }
                    },
                }
            },
            "data": files,
        },
    }


def _query_picker_helper(helper: str, database: str, tests: list[str]) -> dict[str, Any]:
    command = [helper, "--database", database]
    for test in tests:
        command.extend(("--test", test))
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(
            f"picker coverage helper failed with exit code "
            f"{completed.returncode}: {detail}"
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"picker coverage helper returned invalid JSON: {error}") from error
    provider = result.get("provider") or {}
    if (result.get("schema_version"), provider.get("identity"), provider.get("identity_version")) != (
        3, "picker.vcs.ucapi", 1,
    ):
        raise RuntimeError(
            "picker coverage schema mismatch: rebuild Picker helper with "
            "schema_version=3/provider_identity_version=1"
        )
    if not result.get("success", True):
        raise RuntimeError(
            f"picker coverage helper failed: "
            f"{result.get('errors') or 'unknown error'}"
        )
    return result


def convert_vcs_case_coverage(
    record: CoverageRecord,
    output_dir: Path,
    *,
    helper: str | None = None,
) -> CodeCoverageResult:
    if not record.test:
        raise ValueError("VCS testcase coverage requires a testdata name")
    helper_path = _resolve_helper([record], helper)
    raw_report = _query_picker_helper(helper_path, record.database, [record.test])
    if "covered_items" not in raw_report:
        raise RuntimeError(
            "picker coverage helper does not provide covered_items; regenerate the DUT package"
        )
    patterns, line_ranges = _apply_ignores(raw_report, record.ignore)
    report = _build_code_coverage_report(
        raw_report,
        str(Path(record.database).resolve()),
        [record.test],
        "",
        patterns + [
            f"{path}:{','.join(str(line) for line in sorted(lines))}"
            for path, lines in sorted(line_ranges.items())
        ],
        detail_kind="covered",
    )
    report["metadata"].pop("html", None)
    report["scope"] = {
        "type": "testcase",
        "nodeid": record.nodeid,
        "testdata": record.test,
    }
    summary_path = output_dir / "code_coverage.json"
    summary_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    metrics = {}
    for name, total in report["overview"]["total"].items():
        covered = report["overview"]["covered"][name]
        uncovered = total - covered
        metrics[name] = MetricSummary(
            covered=covered,
            total=total,
            uncovered=uncovered,
            rate=100.0 * covered / total if total else 0.0,
            available=True,
            details_available=True,
        )
    return CodeCoverageResult(
        simulator="vcs",
        metrics=metrics,
        summary_file=str(summary_path),
        databases=[record.database],
        ignore=list(record.ignore),
    )


def convert_vcs_coverage(
    records: list[CoverageRecord],
    output_dir: Path,
    *,
    urg: str | None = None,
    vcs_home: str | None = None,
    source_root: str | Path | None = None,
    helper: str | None = None,
) -> CodeCoverageResult:
    databases = list(dict.fromkeys(str(Path(record.database).resolve()) for record in records))
    if len(databases) != 1:
        raise ValueError("VCS session coverage requires exactly one shared VDB")
    for database in databases:
        path = Path(database)
        coverage_db = path / "snps" / "coverage" / "db"
        if (
            not path.is_dir()
            or path.suffix.lower() != ".vdb"
            or not coverage_db.is_dir()
        ):
            raise ValueError(f"invalid VCS coverage database: '{database}'")

    output_dir.mkdir(parents=True, exist_ok=True)
    tests = sorted({record.test for record in records if record.test})
    helper_path = _resolve_helper(records, helper)
    raw_report = _query_picker_helper(helper_path, databases[0], tests)
    ignore = list(dict.fromkeys(item for record in records for item in record.ignore))
    patterns, line_ranges = _apply_ignores(raw_report, ignore)
    urg_output = output_dir / "vcs_urg"
    urg_output.mkdir(parents=True, exist_ok=True)
    executable = _resolve_urg(urg, vcs_home)
    command = [executable, "-full64"]
    for database in databases:
        command.extend(("-dir", database))
    command.extend(("-report", str(urg_output)))

    env = os.environ.copy()
    if vcs_home:
        env["VCS_HOME"] = vcs_home
    completed = subprocess.run(
        command,
        cwd=None if source_root is None else str(source_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    stdout_log = output_dir / "urg.stdout.log"
    stderr_log = output_dir / "urg.stderr.log"
    stdout_log.write_text(completed.stdout, encoding="utf-8")
    stderr_log.write_text(completed.stderr, encoding="utf-8")
    entry = find_urg_entry(urg_output)
    if completed.returncode != 0 or entry is None:
        reason = f"exit code {completed.returncode}" if completed.returncode else "no HTML entry"
        raise RuntimeError(f"URG failed ({reason}); see '{stderr_log}'")

    warnings = []
    if completed.stderr.strip():
        warnings.append(f"URG emitted diagnostics; see {output_dir.name}/{stderr_log.name}")
    detail = f"{output_dir.name}/{entry.relative_to(output_dir).as_posix()}"
    summary_path = output_dir / "code_coverage.json"
    summary_file = f"{output_dir.name}/{summary_path.name}"
    final_report = _build_code_coverage_report(
        raw_report,
        databases[0],
        tests,
        detail,
        patterns + [
            f"{path}:{','.join(str(line) for line in sorted(lines))}"
            for path, lines in sorted(line_ranges.items())
        ],
    )
    summary_path.write_text(json.dumps(final_report, indent=2) + "\n", encoding="utf-8")
    metrics = {}
    for name, total in final_report["overview"]["total"].items():
        uncovered = final_report["overview"]["miss"][name]
        covered = total - uncovered
        metrics[name] = MetricSummary(
            covered=covered,
            total=total,
            uncovered=uncovered,
            rate=100.0 * covered / total if total else 0.0,
            available=bool(
                (raw_report.get("summary") or {})
                .get("metrics", {})
                .get(name, {})
                .get("available")
            ),
            details_available=True,
        )
    return CodeCoverageResult(
        simulator="vcs",
        metrics=metrics,
        detail=detail,
        summary_file=summary_file,
        databases=databases,
        ignore=ignore,
        warnings=warnings,
    )
