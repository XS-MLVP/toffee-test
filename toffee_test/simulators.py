"""Simulator capability registry.

The pytest lifecycle should depend on simulator capabilities rather than
scattered simulator-name conditionals.  Picker-generated DUTs identify their
backend through ``GetSimulator()`` and ``SIMULATOR``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SimulatorBackend:
    name: str
    forked_execution: bool = False
    coverage_suffix: str = ".dat"
    finish_none_is_success: bool = True

    def waveform_name(self, stem: str, dut: Any) -> str:
        suffix = str(dut.GetWaveFormat() or "").lstrip(".")
        return f"{stem}.{suffix}" if suffix else stem

    def coverage_name(self, stem: str) -> str:
        return stem if not self.coverage_suffix else f"{stem}{self.coverage_suffix}"

    def coverage_path(self, dut: Any, filename: str | None) -> str:
        get_path = getattr(dut, "GetCoveragePath", None)
        if callable(get_path):
            return get_path()
        if self.name == "verilator" and filename:
            return filename
        raise RuntimeError(f"{self.name} coverage requires a DUT with GetCoveragePath()")

    def finish_ok(self, status: Any) -> bool:
        return status == 0 or (status is None and self.finish_none_is_success)


_BACKENDS: dict[str, SimulatorBackend] = {
    "vcs": SimulatorBackend(
        name="vcs", forked_execution=True, coverage_suffix="", finish_none_is_success=True
    ),
    "uvs": SimulatorBackend(
        name="uvs", forked_execution=True, coverage_suffix=".cdb", finish_none_is_success=True
    ),
    "verilator": SimulatorBackend(
        name="verilator", forked_execution=False, coverage_suffix=".dat", finish_none_is_success=True
    ),
}


def register_backend(backend: SimulatorBackend) -> None:
    key = backend.name.strip().lower()
    if not key:
        raise ValueError("simulator backend name cannot be empty")
    _BACKENDS[key] = backend


def get_backend(name: str | None) -> SimulatorBackend:
    key = str(name or "verilator").strip().lower()
    return _BACKENDS.get(key, SimulatorBackend(name=key))


def simulator_name(dut: Any) -> str:
    value = getattr(dut, "SIMULATOR", None)
    if value is None and callable(getattr(dut, "GetSimulator", None)):
        value = dut.GetSimulator()
    return str(value or "verilator").strip().lower()
