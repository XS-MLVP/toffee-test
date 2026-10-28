from __future__ import annotations

import os
import re
import json
import fnmatch
import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .common import html_report_detail
from .models import CodeCoverageResult, CoverageRecord, MetricSummary


VCS_METRICS = ("line", "branch", "toggle", "condition", "fsm")
PICKER_VCS_SCHEMA_VERSION = 1


def find_urg_entry(output_dir: Path) -> Path | None:
    for name in ("dashboard.html", "index.html", "hierarchy.html"):
        candidate = output_dir / name
        if candidate.is_file():
            return candidate
    html_files = sorted(output_dir.rglob("*.html"))
    return html_files[0] if html_files else None


def _load_urg_report_metrics(report_dir: Path) -> dict[str, MetricSummary]:
    """Read exact native totals from URG's text dashboard generated with ratios.

    The dashboard covers the full reported hierarchy, including generated
    wrappers. It must not replace Picker's DUT-only query data used for gaps.
    """
    path = report_dir / "dashboard.txt"
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(
        r"^Total Coverage Summary[^\n]*\n([^\n]+)\n([^\n]+)", text, re.MULTILINE
    )
    if match is None:
        raise ValueError("URG text dashboard has no total coverage summary")
    names = match[1].split()
    # SCORE is a single composite percentage; every metric has a rate and an
    # exact covered/total ratio. Accept unavailable metrics without inventing 0%.
    values = re.findall(r"(--|\d+(?:\.\d+)?)\s+(\d+)/(\d+)", match[2])
    metric_names = [name for name in names if name != "SCORE"]
    if len(values) != len(metric_names):
        raise ValueError("URG text dashboard is missing coverage ratios")
    aliases = {"COND": "condition", "FSM": "fsm"}
    metrics = {}
    for name, (rate, covered, total) in zip(metric_names, values):
        covered, total = int(covered), int(total)
        if covered > total:
            raise ValueError("URG text dashboard has invalid coverage counts")
        metrics[aliases.get(name, name.lower())] = MetricSummary(
            covered=covered, total=total, uncovered=total - covered,
            rate=float(rate) if rate != "--" else 0.0,
            available=rate != "--" and total > 0,
            details_available=total > 0,
        )
    return metrics


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

    raise FileNotFoundError(
        "picker coverage executable was not recorded; regenerate the DUT package"
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


def _build_atom_inventory(raw: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a deterministic Toffee gap catalog from Picker item observations."""
    provider_schema_version = int(raw.get("schema_version") or 0)
    # Picker reports the current uncovered set, not a complete design inventory.
    # Version the accepted gap catalog locally instead of requiring a provider
    # owned design revision.
    design_revision = str(raw.get("design_revision") or _canonical_hash({
        "simulator": raw.get("simulator") or "vcs",
        "metrics": raw.get("metrics") or (raw.get("summary") or {}).get("metrics") or {},
        "items": items,
    }))
    atoms: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    groups: dict[str, dict[str, Any]] = {}
    for item in items:
        metric = str(item.get("kind") or "")
        raw_object = str(item.get("object") or "")
        object_name, bit = _split_bit_object(raw_object) if metric == "toggle" else (raw_object, None)
        anchor_payload = {
            "file_logical_id": str(item.get("file") or ""),
            "module": str(item.get("module") or ""),
            "object": object_name,
            "metric": metric,
            "line_fallback": int(item.get("line") or 0),
            "column_fallback": int(item.get("column") or 0),
        }
        identity = {
            "file_logical_id": anchor_payload["file_logical_id"],
            "module": anchor_payload["module"],
            "instance": str(item.get("instance") or ""),
            "normalized_object": object_name,
            "metric": metric,
            "bit": bit,
            "transition_or_arm": str(item.get("detail") or ""),
            "atom_kind": str(item.get("atom_kind") or ""),
            "native_id": str(item.get("native_id") or ""),
            "native_name": str(item.get("native_name") or ""),
            "from_state": str(item.get("from_state") or ""),
            "to_state": str(item.get("to_state") or ""),
            "counts_toward_rate": bool(item.get("counts_toward_rate", True)),
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
                "file": str(item.get("file") or ""),
                # Keep Toffee/UCAgent catalog aliases without adding them to
                # the Picker IPC protocol.
                "logical_file": str(item.get("file") or ""),
                "resolved_file": str(item.get("file") or ""),
                "line": int(item.get("line") or 0),
                "column": int(item.get("column") or 0),
            },
            "observation": "uncovered",
        }
        atoms[atom_id] = atom
        excluded = {"bit"}
        if metric == "condition":
            excluded.update({"native_id", "native_name", "transition_or_arm"})
        elif metric == "fsm" and identity["atom_kind"] == "fsm_state":
            excluded.update({"native_id", "native_name", "transition_or_arm"})
        elif metric == "fsm" and identity["atom_kind"] == "fsm_transition":
            excluded.update({"native_id", "native_name", "to_state", "transition_or_arm"})
        group_identity = {key: value for key, value in identity.items() if key not in excluded}
        group_key = "group:" + _canonical_hash(group_identity)
        atom["group_key"] = group_key
        groups.setdefault(group_key, {"group_key": group_key, "identity": group_identity, "members": []})["members"].append(atom_id)
    output_groups = []
    for group in groups.values():
        group["members"].sort()
        group["atom_count"] = len(group["members"])
        group["member_hash"] = _canonical_hash(group["members"])
        output_groups.append(group)
    ordered_atoms = [atoms[key] for key in sorted(atoms)]
    enumerated_by_metric: dict[str, int] = {}
    rate_atoms_by_metric: dict[str, int] = {}
    for atom in ordered_atoms:
        metric = atom["identity"]["metric"]
        enumerated_by_metric[metric] = enumerated_by_metric.get(metric, 0) + 1
        if atom["identity"].get("counts_toward_rate", True):
            rate_atoms_by_metric[metric] = rate_atoms_by_metric.get(metric, 0) + 1
    expected_by_metric = {}
    for metric, value in ((raw.get("summary") or {}).get("metrics") or {}).items():
        total = int(value.get("total", 0)); uncovered = int(value.get("uncovered", total-int(value.get("covered", 0))))
        expected_by_metric[metric] = uncovered
    unresolved = [
        {"metric": metric, "provider_metric_items": expected_by_metric.get(metric, 0),
         "enumerated_atoms": enumerated_by_metric.get(metric, 0),
         "enumerated_rate_atoms": rate_atoms_by_metric.get(metric, 0)}
        for metric in sorted(set(expected_by_metric) | set(enumerated_by_metric))
        if expected_by_metric.get(metric, 0) != rate_atoms_by_metric.get(metric, 0)
    ]
    return {
        "schema_version": 2,
        "provider_schema_version": provider_schema_version,
        "toffee_schema_version": 5,
        "design_revision": design_revision,
        "inventory_revision": _canonical_hash({
            "design_revision": design_revision,
            "provider_revision": str(raw.get("inventory_revision") or ""),
            "atom_ids": sorted(atoms),
        }),
        "observation": "uncovered",
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
    for name in VCS_METRICS:
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
    for field in ("items",):
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
) -> dict[str, Any]:
    raw_metrics = (raw.get("summary") or {}).get("metrics") or {}
    overview_total, overview_miss = _metric_maps(raw_metrics)
    metric_names = tuple(name for name in VCS_METRICS if name in overview_total)
    overview_values = overview_miss
    items = raw.get("items") or []
    items_by_scope: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        key = (str(item.get("file") or ""), str(item.get("module") or ""))
        items_by_scope.setdefault(key, []).append(item)

    files: dict[str, Any] = {}
    raw_files = (raw.get("breakdown") or {}).get("files") or {}
    for file_path in sorted(raw_files):
        raw_file = raw_files[file_path]
        file_total, file_miss = _metric_maps(raw_file.get("metrics") or {})
        file_values = file_miss
        modules: dict[str, Any] = {}
        for module_name in sorted((raw_file.get("modules") or {})):
            raw_module = raw_file["modules"][module_name]
            module_total, module_miss = _metric_maps(raw_module.get("metrics") or {})
            module_values = module_miss
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
            condition_groups: dict[tuple[int, int, str], set[str]] = {}
            fsm_groups: dict[tuple[int, int, str], dict[str, Any]] = {}
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
                elif item.get("kind") == "condition":
                    condition_groups.setdefault(key, set()).add(
                        str(item.get("native_id") or item.get("detail") or "")
                    )
                elif item.get("kind") == "fsm":
                    group = fsm_groups.setdefault(
                        key, {"states": set(), "transitions": set()}
                    )
                    if item.get("atom_kind") == "fsm_state":
                        group["states"].add(str(item.get("native_name") or item.get("detail") or ""))
                    elif item.get("atom_kind") == "fsm_transition":
                        group["transitions"].add((
                            str(item.get("from_state") or ""),
                            str(item.get("to_state") or ""),
                        ))

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
            conditions = [
                {"line": line, "column": column, "object": obj, "bins": sorted(values), "atom_count": len(values)}
                for (line, column, obj), values in sorted(condition_groups.items())
            ]
            fsms = [
                {
                    "line": line,
                    "column": column,
                    "object": obj,
                    "states": sorted(values["states"]),
                    "transitions": [
                        {"from": source, "to": target}
                        for source, target in sorted(values["transitions"])
                    ],
                    "state_atom_count": len(values["states"]),
                    "transition_atom_count": len(values["transitions"]),
                }
                for (line, column, obj), values in sorted(fsm_groups.items())
            ]
            module_report = {
                "total": module_total,
                "miss": module_values,
                "line": line_ranges,
                "toggle": toggles,
                "branch": branches,
            }
            if "condition" in metric_names:
                module_report["condition"] = conditions
            if "fsm" in metric_names:
                module_report["fsm"] = fsms
            modules[module_name] = module_report
        files[file_path] = {
            "total": file_total,
            "miss": file_values,
            "modules": modules,
        }

    atom_inventory = _build_atom_inventory(raw, items)
    detail_counts = {name: 0 for name in VCS_METRICS}
    rate_detail_counts = {name: 0 for name in VCS_METRICS}
    group_counts = {name: 0 for name in VCS_METRICS}
    for atom in atom_inventory["atoms"]:
        detail_counts[atom["identity"]["metric"]] += 1
        if atom["identity"].get("counts_toward_rate", True):
            rate_detail_counts[atom["identity"]["metric"]] += 1
    for group in atom_inventory["groups"]:
        group_counts[group["identity"]["metric"]] += 1
    metric_items = overview_values
    detail_summary = {
        name: {
            "metric_items": int(metric_items.get(name, 0)),
            "enumerated_atoms": detail_counts.get(name, 0),
            "enumerated_rate_atoms": rate_detail_counts.get(name, 0),
            "gap_count": group_counts.get(name, 0),
            "unresolved": abs(int(metric_items.get(name, 0)) - rate_detail_counts.get(name, 0)),
        }
        for name in metric_names
        if name in overview_total
    }

    return {
        "schema_version": 5,
        "description": "Code coverage summary. `uncovered.data` contains current gaps.",
        "simulator": "vcs",
        "snapshot": {
            "design_revision": atom_inventory["design_revision"],
            "inventory_revision": atom_inventory["inventory_revision"],
            "tests": tests,
            "artifacts": [database],
        },
        "metrics": {
            name: dict(value)
            for name, value in ((raw.get("summary") or {}).get("metrics") or {}).items()
            if name in metric_names
        },
        "gaps": files,
        "metadata": {
            "database": database,
            "tests": tests,
            "exclude": excludes,
            "html": html_report,
            "detail_level": {
                "line": "range",
                "toggle": "base_signal_transitions",
                "branch": "object_arms",
                **({"condition": "native_bins"} if "condition" in metric_names else {}),
                **({"fsm": "states_and_transitions"} if "fsm" in metric_names else {}),
            },
        },
        "overview": {
            "total": overview_total,
            "miss": overview_values,
        },
        "detail_summary": detail_summary,
        "atom_inventory": atom_inventory,
        "uncovered": {
            "schema": {
                "file_path": {
                    "total": list(metric_names),
                    "miss": list(metric_names),
                    "modules": {
                        "module_name": {
                            "total": list(metric_names),
                            "miss": list(metric_names),
                            "line": ["inclusive start-end ranges"],
                            "toggle": ["base signal objects with missing transitions and bit ranges"],
                            "branch": ["branch objects with missing arms"],
                            **({"condition": ["condition expressions with missing native bins"]} if "condition" in metric_names else {}),
                            **({"fsm": ["FSM objects with missing states and transitions"]} if "fsm" in metric_names else {}),
                        }
                    },
                }
            },
            "data": files,
        },
    }


def _coverage_item_dict(item: Any) -> dict[str, Any]:
    location = item.location
    file = str(getattr(location, "file", "") or "")
    return {
        "kind": str(getattr(item, "kind_name", "") or ""),
        "file": file,
        "line": int(getattr(location, "line", 0)),
        "column": int(getattr(location, "column", 0)),
        "module": str(item.module),
        "instance": str(item.instance),
        "object": str(item.object),
        "count": int(item.count),
        "detail": str(item.detail),
        "covered": 0,
        "coverable": 1,
        "counts_toward_rate": True,
    }


def _normalize_picker_report(report: dict[str, Any]) -> dict[str, Any]:
    """Normalize the native helper JSON without requiring a separate xcomm ABI."""
    items = []
    for raw_item in report.get("items", []):
        item = {name: raw_item.get(name, default) for name, default in {
            "kind": "", "file": "", "line": 0, "column": 0, "module": "",
            "instance": "", "object": "", "count": 0, "detail": "",
        }.items()}
        item.update(covered=0, coverable=1, counts_toward_rate=True)
        if item["kind"] == "fsm" and "->" not in item.get("detail", ""):
            item["atom_kind"] = "fsm_auxiliary"
            item["counts_toward_rate"] = False
        items.append(item)
    metrics = {
        name: {**value, "available": True, "details_available": True}
        for name, value in (report.get("metrics") or report.get("summary", {}).get("metrics", {})).items()
    }
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in items:
        key = tuple(item.get(name) for name in (
            "kind", "source_id", "logical_file", "module", "instance", "line", "column", "object",
            "detail", "atom_kind", "native_id", "from_state", "to_state",
        ))
        previous = unique.get(key)
        if previous is None or int(item.get("covered", 0)) > int(previous.get("covered", 0)):
            unique[key] = item
    files: dict[str, Any] = {}
    for item in unique.values():
        file_data = files.setdefault(item["file"], {"metrics": {}, "modules": {}})
        module_data = file_data["modules"].setdefault(item["module"], {"metrics": {}})
        if not item.get("counts_toward_rate", True):
            continue
        for target in (file_data["metrics"], module_data["metrics"]):
            value = target.setdefault(item["kind"], {"covered": 0, "total": 0})
            value["covered"] += max(0, min(int(item.get("covered", 0)), int(item.get("coverable", 0))))
            value["total"] += max(0, int(item.get("coverable", 0)))
    for file_data in files.values():
        for target in [file_data["metrics"], *(module["metrics"] for module in file_data["modules"].values())]:
            for value in target.values():
                value["uncovered"] = value["total"] - value["covered"]
                value["rate"] = 100.0 * value["covered"] / value["total"] if value["total"] else 0.0
    return {
        **report,
        "metrics": metrics,
        "summary": {"metrics": metrics, "total_uncovered": len(items)},
        "breakdown": {"files": files},
        "items": items,
    }


def _query_picker_helper(
    helper: str,
    database: str,
    tests: list[str],
) -> dict[str, Any]:
    """Query the isolated VCS helper with a bounded execution time."""
    command = [helper, "--database", database]
    for metric in VCS_METRICS:
        command.extend(["--kind", metric])
    for test in tests:
        command.extend(["--test", test])
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=120, check=False)
    if completed.returncode:
        raise RuntimeError(f"picker coverage query failed: {completed.stderr[-2000:]}")
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("picker coverage helper returned invalid JSON") from error
    if not isinstance(report, dict) or report.get("schema_version") != PICKER_VCS_SCHEMA_VERSION:
        raise RuntimeError("Picker VCS coverage schema mismatch: expected schema_version=1")
    if report.get("success") is not True or report.get("simulator") != "vcs":
        raise RuntimeError(f"picker coverage query failed: {report.get('errors', [])}")
    return _normalize_picker_report(report)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Atomically write deterministic compact JSONL and return its manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    digest = hashlib.sha256()
    size = 0
    with temporary.open("wb") as stream:
        for row in rows:
            encoded = (
                json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
            stream.write(encoded)
            digest.update(encoded)
            size += len(encoded)
    temporary.replace(path)
    return {"path": path.as_posix(), "sha256": digest.hexdigest(), "rows": len(rows), "bytes": size}


def _write_json_document(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _externalize_atom_catalog(report: dict[str, Any], output_dir: Path) -> None:
    """Move the large VCS atom inventory out of cumulative.json.

    The manifest is deliberately small so coverage-refine can validate and
    cache a revision without parsing hundreds of megabytes on every launch.
    """
    inventory = report.pop("atom_inventory", None)
    if not isinstance(inventory, dict):
        raise ValueError("VCS cumulative report is missing atom_inventory")
    catalog_dir = output_dir / "catalog"
    raw_atoms = list(inventory.pop("atoms", []))
    raw_groups = list(inventory.pop("groups", []))
    source_rows: dict[str, dict[str, Any]] = {}
    compact_atoms: list[dict[str, Any]] = []
    group_identities = {
        str(group["group_key"]): dict(group.get("identity") or {}) for group in raw_groups
    }
    common_identity = {
        "design_revision": inventory.get("design_revision"),
    }
    for atom in raw_atoms:
        location = dict(atom.get("source_location") or {})
        logical_file = str(location.get("logical_file") or location.get("file") or "")
        resolved_file = str(location.get("resolved_file") or logical_file)
        source_id = "source:" + _canonical_hash({"file": logical_file})
        source_rows[source_id] = {
            "source_id": source_id,
            "logical_file": logical_file,
            "resolved_file": resolved_file,
        }
        group_key = str(atom.get("group_key") or "")
        group_identity = group_identities[group_key]
        identity = dict(atom.get("identity") or {})
        variant = {
            key: value for key, value in identity.items()
            if key not in common_identity and group_identity.get(key) != value
        }
        compact_atoms.append({
            "atom_id": atom["atom_id"],
            "group_key": group_key,
            "variant": variant,
            "source": {
                "source_id": source_id,
                "line": int(location.get("line") or 0),
                "column": int(location.get("column") or 0),
            },
            "observation": atom.get("observation"),
        })
    compact_groups = []
    for group in raw_groups:
        identity = {
            key: value for key, value in dict(group.get("identity") or {}).items()
            if key not in common_identity
        }
        compact_groups.append({
            "group_key": group["group_key"],
            "identity": identity,
            "atom_count": int(group.get("atom_count") or 0),
            "member_hash": group.get("member_hash"),
        })
    sources = _write_jsonl(catalog_dir / "sources.jsonl", [source_rows[key] for key in sorted(source_rows)])
    atoms = _write_jsonl(catalog_dir / "atoms.jsonl", compact_atoms)
    groups = _write_jsonl(catalog_dir / "groups.jsonl", compact_groups)
    sources["path"] = "catalog/sources.jsonl"
    atoms["path"] = "catalog/atoms.jsonl"
    groups["path"] = "catalog/groups.jsonl"
    manifest = {
        "schema_version": 2,
        **inventory,
        "common_identity": common_identity,
        "files": {"sources": sources, "atoms": atoms, "groups": groups},
    }
    encoded = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    manifest_path = output_dir / "catalog.json"
    temporary = manifest_path.with_name(".catalog.json.tmp")
    temporary.write_bytes(encoded)
    temporary.replace(manifest_path)
    report["catalog"] = {
        "path": "catalog.json",
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "schema_version": 2,
        "design_revision": manifest.get("design_revision"),
        "inventory_revision": manifest.get("inventory_revision"),
        "atom_count": atoms["rows"],
        "group_count": groups["rows"],
    }


def _compact_cumulative(report: dict[str, Any]) -> None:
    """Drop development-era duplicate views after the v5 fields are populated."""
    report.pop("overview", None)
    report.pop("uncovered", None)


def convert_vcs_case_coverage(
    record: CoverageRecord,
    output_dir: Path,
    *,
    helper: str | None = None,
) -> CodeCoverageResult:
    if not record.test:
        raise ValueError("VCS testcase coverage requires a testdata name")
    helper_path = _resolve_helper([record], helper)
    # Picker exposes the testcase's current uncovered set.  Covered accepted
    # gaps are derived only when a baseline target set is available.
    raw_report = _query_picker_helper(helper_path, record.database, [record.test])
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
    )
    report["metadata"].pop("html", None)
    report["scope"] = {
        "type": "testcase",
        "nodeid": record.nodeid,
        "testdata": record.test,
    }
    normalized_layout = "coverage_data" in output_dir.parts
    if normalized_layout:
        report.pop("atom_inventory", None)
    summary_path = output_dir / ("observation.json" if normalized_layout else "code_coverage.json")
    _write_json_document(summary_path, report)

    metrics = {}
    for name, value in report["metrics"].items():
        total = int(value.get("total") or 0)
        covered = int(value.get("covered") or 0)
        uncovered = int(value.get("uncovered", total - covered))
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


def convert_vcs_preview_coverage(
    records: list[CoverageRecord],
    output_dir: Path,
    *,
    helper: str | None = None,
) -> CodeCoverageResult:
    """Write the candidate query's current uncovered atom catalog."""
    databases = list(dict.fromkeys(str(Path(record.database).resolve()) for record in records))
    if len(databases) != 1:
        raise ValueError("VCS preview coverage requires exactly one shared VDB")
    database = Path(databases[0])
    if not database.is_dir() or database.suffix.lower() != ".vdb":
        raise ValueError(f"invalid VCS coverage database: '{database}'")
    tests = sorted({record.test for record in records if record.test})
    if not tests:
        raise ValueError("VCS preview coverage requires at least one testdata name")
    helper_path = _resolve_helper(records, helper)
    raw_report = _query_picker_helper(helper_path, databases[0], tests)
    ignore = list(dict.fromkeys(item for record in records for item in record.ignore))
    patterns, line_ranges = _apply_ignores(raw_report, ignore)
    final_report = _build_code_coverage_report(
        raw_report,
        databases[0],
        tests,
        "",
        patterns + [
            f"{path}:{','.join(str(line) for line in sorted(lines))}"
            for path, lines in sorted(line_ranges.items())
        ],
    )
    final_report.setdefault("metadata", {})["preview"] = True
    # UCAgent owns accepted targets and computes U-C.  Toffee publishes C
    # without applying workflow-specific target filtering.
    final_report["uncovered"]["data"] = {}
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "cumulative.json"
    _externalize_atom_catalog(final_report, output_dir)
    _compact_cumulative(final_report)
    _write_json_document(summary_path, final_report)
    metrics = {}
    for name, value in final_report["metrics"].items():
        total = int(value.get("total") or 0)
        covered = int(value.get("covered") or 0)
        uncovered = int(value.get("uncovered") or (total - covered))
        metrics[name] = MetricSummary(
            covered=covered, total=total, uncovered=uncovered,
            rate=100.0 * covered / total if total else 0.0,
            available=True, details_available=True,
        )
    return CodeCoverageResult(
        simulator="vcs", metrics=metrics, summary_file="coverage_data/cumulative.json",
        databases=databases, ignore=ignore,
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
    normalized_layout = output_dir.name == "coverage_data"
    urg_output = output_dir / "native" / "vcs_urg" if normalized_layout else output_dir / "vcs_urg"
    urg_output.mkdir(parents=True, exist_ok=True)
    executable = _resolve_urg(urg, vcs_home)
    command = [executable, "-full64"]
    for database in databases:
        command.extend(("-dir", database))
    command.extend(("-format", "both", "-show", "ratios", "-report", str(urg_output)))

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
    report_metrics = {}
    try:
        report_metrics = _load_urg_report_metrics(urg_output)
    except ValueError as error:
        warnings.append(f"Native coverage summary unavailable: {error}")
    report_scope = "Native report: full hierarchy, including generated wrappers and all database tests."
    if completed.stderr.strip():
        warnings.append(f"URG emitted diagnostics; see {output_dir.name}/{stderr_log.name}")
    detail = html_report_detail(
        urg_output, output_dir, entry_names=(entry.relative_to(urg_output).as_posix(),)
    )
    summary_path = output_dir / ("cumulative.json" if normalized_layout else "code_coverage.json")
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
    if report_metrics:
        final_report["metadata"]["report_summary"] = {
            "scope": report_scope,
            "metrics": {name: metric.to_dict() for name, metric in report_metrics.items()},
        }
    if normalized_layout:
        _externalize_atom_catalog(final_report, output_dir)
        _compact_cumulative(final_report)
    _write_json_document(summary_path, final_report)
    metrics = {}
    for name, value in final_report["metrics"].items():
        total = int(value.get("total") or 0)
        covered = int(value.get("covered") or 0)
        uncovered = int(value.get("uncovered") or (total - covered))
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
        report_metrics=report_metrics,
        report_scope=report_scope,
    )
