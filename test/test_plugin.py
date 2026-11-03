import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from toffee_test.plugin import _resolve_ini_report_dir


def make_config(inipath, addopts, invocation_args=(), override_ini=()):
    return SimpleNamespace(
        inipath=inipath,
        inicfg={"addopts": addopts},
        invocation_params=SimpleNamespace(args=invocation_args),
        _override_ini=override_ini,
    )


@pytest.mark.parametrize(
    "addopts, report_dir",
    [
        ("--report-dir=reports", "reports"),
        ("--report-dir=../reports", "../reports"),
        ('--report-dir "reports with spaces"', "reports with spaces"),
    ],
)
def test_resolves_relative_report_dir_from_ini(tmp_path, addopts, report_dir):
    inipath = tmp_path / "config" / ".pytest.ini"
    config = make_config(inipath, addopts)

    result = _resolve_ini_report_dir(config, report_dir)

    assert result == os.path.normpath(str(inipath.parent / report_dir))


def test_does_not_change_absolute_report_dir_from_ini(tmp_path):
    inipath = tmp_path / "config" / ".pytest.ini"
    report_dir = str(tmp_path / "reports")
    config = make_config(inipath, f"--report-dir={report_dir}")

    assert _resolve_ini_report_dir(config, report_dir) == report_dir


def test_does_not_change_report_dir_without_an_ini_file():
    config = make_config(None, "--report-dir=reports")

    assert _resolve_ini_report_dir(config, "reports") == "reports"


@pytest.mark.parametrize("source", ["command_line", "environment"])
def test_external_report_dir_override_keeps_cwd_semantics(tmp_path, monkeypatch, source):
    inipath = tmp_path / "config" / ".pytest.ini"
    invocation_args = ()
    if source == "command_line":
        invocation_args = ("--report-dir=reports",)
    else:
        monkeypatch.setenv("PYTEST_ADDOPTS", "--report-dir=reports")
    config = make_config(inipath, "--report-dir=reports", invocation_args)

    assert _resolve_ini_report_dir(config, "reports") == "reports"


def test_addopts_override_is_not_treated_as_file_configuration(tmp_path):
    inipath = tmp_path / "config" / ".pytest.ini"
    config = make_config(
        inipath,
        "--report-dir=reports",
        override_ini=("addopts=--report-dir=reports",),
    )

    assert _resolve_ini_report_dir(config, "reports") == "reports"


def add_project_to_pythonpath(monkeypatch, project_root):
    existing_pythonpath = os.environ.get("PYTHONPATH")
    pythonpath = str(project_root)
    if existing_pythonpath:
        pythonpath = os.pathsep.join((pythonpath, existing_pythonpath))
    monkeypatch.setenv("PYTHONPATH", pythonpath)


def test_explicit_ini_report_dir_is_resolved_during_pytest_startup(
    pytester, monkeypatch
):
    project_root = Path(__file__).parents[1]
    add_project_to_pythonpath(monkeypatch, project_root)
    config_dir = pytester.path / "config"
    config_dir.mkdir()
    inipath = config_dir / ".pytest.ini"
    inipath.write_text(
        "[pytest]\n"
        "addopts = --toffee-report --report-name=report.html "
        "--report-dir=reports\n",
        encoding="utf-8",
    )
    test_file = pytester.makepyfile("def test_pass():\n    pass\n")

    result = pytester.runpytest_subprocess("-c", inipath, test_file)

    result.assert_outcomes(passed=1)
    assert (config_dir / "reports" / "report.html").is_file()
    assert not (pytester.path / "reports").exists()


def test_command_line_report_dir_keeps_invocation_directory_base(
    pytester, monkeypatch
):
    project_root = Path(__file__).parents[1]
    add_project_to_pythonpath(monkeypatch, project_root)
    config_dir = pytester.path / "config"
    config_dir.mkdir()
    inipath = config_dir / ".pytest.ini"
    inipath.write_text(
        "[pytest]\n"
        "addopts = --toffee-report --report-name=report.html "
        "--report-dir=from-ini\n",
        encoding="utf-8",
    )
    test_file = pytester.makepyfile("def test_pass():\n    pass\n")

    result = pytester.runpytest_subprocess(
        "-c", inipath, "--report-dir=from-command-line", test_file
    )

    result.assert_outcomes(passed=1)
    assert (pytester.path / "from-command-line" / "report.html").is_file()
    assert not (config_dir / "from-ini").exists()
