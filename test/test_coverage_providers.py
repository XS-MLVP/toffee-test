"""Provider extension and opt-in application links through the public report API."""

import json
import re
import shutil
import subprocess

import pytest

from toffee_test.utils.code_coverage import (
    ApplicationDetail, CodeCoverageResult, CoverageProvider, CoverageRecord,
    CoverageReportContext, HtmlDetail, MetricSummary, convert_case_coverage,
    convert_code_coverage, register_coverage_provider,
)
from test_coverage_report_details import render_coverage, Links


@pytest.mark.parametrize('html,application,enabled,labels', [
    (True, True, False, ['HTML']), (True, True, True, ['HTML', 'Verdi']),
    (False, True, True, ['Verdi']), (False, True, False, []),
    (True, False, True, ['HTML']), (False, False, True, []),
])
def test_detail_combinations(tmp_path, html, application, enabled, labels):
    """Only a configured resolver exposes applications, independently of line totals."""
    details = ([HtmlDetail('native/index.html')] if html else [])
    if application:
        details.append(ApplicationDetail('verdi', 'Verdi', {'database': 'dut.vdb'}))
    calls = []
    context = CoverageReportContext(tmp_path, tmp_path / 'report')
    def resolve(detail, actual_context):
        """Capture the descriptor without starting a GUI."""
        calls.append(detail)
        assert actual_context == context
        return 'apps/verdi.html'
    result = CodeCoverageResult('new_simulator', details=details).to_report_dict(
        application_detail_resolver=resolve if enabled else None, context=context)
    assert [link['label'] for link in result['details']] == labels
    assert len(calls) == int(application and enabled)
    page = render_coverage(result)
    links = Links()
    links.feed(page)
    assert links.hrefs == [link['href'] for link in result['details']]
    assert 'View Details' not in page
    if len(labels) == 2:
        assert '</a> | <a ' in page


@pytest.mark.parametrize('response', [None, 'javascript:alert(1)', '', '//other-host/app'])
def test_unavailable_application_preserves_html_and_metrics(tmp_path, response):
    """Declined and invalid application links never erase measured results."""
    result = CodeCoverageResult('future', metrics={'line': MetricSummary(2, 5, available=True)},
        details=[HtmlDetail('native/index.html'), ApplicationDetail('uvd', 'UVD')])
    report = result.to_report_dict(application_detail_resolver=lambda *_: response,
                                  context=CoverageReportContext(tmp_path, tmp_path))
    assert report['hints'] == 2 and report['total'] == 5
    assert [link['label'] for link in report['details']] == ['HTML']
    assert bool(report['warnings']) == (response is not None)


def test_resolver_error_and_label_escaping(tmp_path):
    """Resolver exceptions are isolated and provider labels remain HTML text."""
    def unavailable(*args):
        """Model an unavailable host integration."""
        raise RuntimeError('host unavailable')
    result = CodeCoverageResult('future', details=[HtmlDetail('native/index.html', '<HTML>'),
        ApplicationDetail('uvd', 'UVD')])
    report = result.to_report_dict(application_detail_resolver=unavailable,
                                  context=CoverageReportContext(tmp_path, tmp_path))
    assert 'host unavailable' in report['warnings'][0]
    assert '>&lt;HTML&gt;</a>' in render_coverage(report)


def test_legacy_detail_and_function_registration(tmp_path):
    """Function providers retain their options and old single-link contract."""
    def convert(records, output, *, token):
        """Use one converter for either public call shape."""
        assert token == 'sentinel' and output == tmp_path
        return CodeCoverageResult('legacy_test', detail='old/index.html')
    register_coverage_provider('legacy_test', case_converter=convert, session_converter=convert)
    record = CoverageRecord('legacy_test', 'data')
    assert convert_case_coverage(record, tmp_path, token='sentinel').detail == 'old/index.html'
    result = convert_code_coverage([record], tmp_path, token='sentinel').to_report_dict()
    assert result['details'][0]['label'] == 'HTML'
    assert result['detail'] == 'old/index.html'


def test_new_provider_needs_no_template_or_registry_changes(tmp_path):
    """A new provider supplies conversion and arbitrary labeled detail descriptions."""
    class NewProvider(CoverageProvider):
        """A minimal application-only provider."""
        def convert_session(self, records, output_dir, **options):
            """Return synthetic measured coverage to test the public extension boundary."""
            return CodeCoverageResult('new_test', metrics={'line': MetricSummary(3, 4, available=True)})

        def build_details(self, result, output_dir):
            """Advertise a viewer without adding a template branch."""
            return [ApplicationDetail('uvd', 'UVD', {'database': 'data.cdb'})]
    register_coverage_provider('new_test', NewProvider())
    result = convert_code_coverage([CoverageRecord('new_test', 'data.cdb')], tmp_path)
    report = result.to_report_dict(application_detail_resolver=lambda *_: 'uvd.html',
                                  context=CoverageReportContext(tmp_path, tmp_path))
    assert '>UVD</a>' in render_coverage(report)
    assert '75.0%' in render_coverage(report)


@pytest.mark.parametrize('enabled', [False, True])
def test_pytest_report_resolver_option(pytester, enabled):
    """The CLI opt-in reaches final HTML and JSON in a real pytest invocation."""
    pytester.makepyfile(coverage_host='''
def resolve(detail, context):
    assert context.workspace.is_dir()
    assert context.report_dir.name == 'report'
    return 'application.html'
''')
    pytester.makeconftest('''
import pytest
from toffee_test.reporter import set_code_coverage
from toffee_test.utils.code_coverage import *
def pytest_configure(config):
    def convert(records, output_dir):
        return CodeCoverageResult('integration_test', metrics={'line': MetricSummary(1, 2, available=True)},
            details=[HtmlDetail('native.html'), ApplicationDetail('uvd', 'UVD')])
    register_coverage_provider('integration_test', case_converter=convert, session_converter=convert)
@pytest.fixture
def coverage(request):
    yield
    set_code_coverage(request, CoverageRecord('integration_test', 'data.cdb'))
''')
    pytester.makepyfile('def test_example(coverage):\n    assert True\n')
    args = ['--toffee-report', '--report-name=index.html', '--report-dir=report', '--report-dump-json']
    if enabled:
        args += ['--report-custom=coverage-gui-xpra', '--coverage-application-resolver=coverage_host:resolve',
                 '--coverage-application-workspace=' + str(pytester.path)]
    result = pytester.runpytest(*args)
    result.assert_outcomes(passed=1)
    page = (pytester.path / 'report/index.html').read_text()
    assert '>HTML</a>' in page
    assert ('>UVD</a>' in page) == enabled


def test_application_links_retain_host_context_for_new_tabs():
    """Resolve links before clicks, including middle-click and copied-link navigation."""
    if not shutil.which('node'):
        pytest.skip('Node.js is required for the report link contract')
    page = render_coverage({'hints': 1, 'total': 2, 'grate': 99,
                           'details': [{'label': 'UVD', 'kind': 'application', 'href': 'app.html'}]})
    script = next(value for value in re.findall(r'<script>(.*?)</script>', page, re.S)
                  if 'data-coverage-application' in value)
    harness = '''
const vm = require('vm'), assert = require('assert');
const links = [{href:'app.html?existing=1'}, {href:'https://elsewhere.test/app'}];
let ready;
vm.runInNewContext(SCRIPT, {URL,
  location: {href:'https://host/task/id/cmd/workspace/report/index.html?sub_workspace=child', origin:'https://host'},
  document: {addEventListener: (event, callback) => {assert.equal(event, 'DOMContentLoaded'); ready=callback;},
             querySelectorAll: () => links}});
ready();
assert.equal(links[0].href, 'https://host/task/id/cmd/workspace/report/app.html?existing=1&sub_workspace=child');
assert.equal(links[1].href, 'https://elsewhere.test/app');
'''
    result = subprocess.run(['node', '-e', 'const SCRIPT=' + json.dumps(script) + ';\n' + harness],
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("names", [("uvs1",), ("uvs", "uvs1")])
def test_builtin_provider_uses_its_declared_picker_names(monkeypatch, tmp_path, names):
    """Changing UVS identifiers in its class is sufficient for session dispatch."""
    import toffee_test.utils.code_coverage as coverage
    from toffee_test.utils.code_coverage.uvs import UvsCoverageProvider

    monkeypatch.setattr(coverage, "_PROVIDERS", {})
    monkeypatch.setattr(coverage, "_BUILTINS_READY", False)
    monkeypatch.setattr(UvsCoverageProvider, "simulator_names", names)
    seen = []
    def convert(self, records, output_dir, **options):
        seen.append(records[0].simulator)
        return CodeCoverageResult(records[0].simulator,
                                  metrics={"line": MetricSummary(2, 3, available=True)},
                                  detail="native/uvs/index.html")
    monkeypatch.setattr(UvsCoverageProvider, "convert_session", convert)
    provider = coverage.get_coverage_provider(" UVS1 ")
    assert isinstance(provider, UvsCoverageProvider)
    if "uvs" in names:
        assert coverage.get_coverage_provider("uvs") is provider
    else:
        with pytest.raises(ValueError, match="unsupported coverage simulator: uvs$"):
            coverage.get_coverage_provider("uvs")
    result = coverage.convert_code_coverage([CoverageRecord("uvs1", "design.cdb")], tmp_path)
    assert seen == ["uvs1"]
    assert result.metrics["line"].covered == 2
    assert result.details == [HtmlDetail("native/uvs/index.html")]


def test_custom_provider_registers_all_declared_names(monkeypatch):
    """External providers need not repeat their simulator identifiers at registration."""
    import toffee_test.utils.code_coverage as coverage

    monkeypatch.setattr(coverage, "_PROVIDERS", {})
    monkeypatch.setattr(coverage, "_BUILTINS_READY", False)
    class MyProvider(CoverageProvider):
        simulator_names = ("my_sim", " MY_SIM_V2 ")
    provider = MyProvider()
    register_coverage_provider(provider)
    assert coverage.get_coverage_provider("my_sim") is provider
    assert coverage.get_coverage_provider("my_sim_v2") is provider


@pytest.mark.parametrize("names", [(), "uvs", ("",), ("valid", None)])
def test_declared_names_are_validated_before_registration(monkeypatch, names):
    """Invalid declarations never leave partially registered aliases."""
    import toffee_test.utils.code_coverage as coverage

    monkeypatch.setattr(coverage, "_PROVIDERS", {})
    provider = CoverageProvider()
    provider.simulator_names = names
    with pytest.raises(ValueError, match="simulator_names"):
        register_coverage_provider(provider)
    assert coverage._PROVIDERS == {}


@pytest.mark.parametrize('mode,expected', [
    ('off', ['HTML']), ('native', ['HTML']), ('xpra', ['HTML', 'UVD']),
])
def test_provider_url_bypasses_launch_generation(mode, expected):
    """Existing viewer pages need no host resolver or application artifacts."""
    def unexpected(*args):
        raise AssertionError('A provider-owned URL must not invoke the launcher')
    result = CodeCoverageResult('uvs', details=[
        HtmlDetail('coverage/index.html'),
        ApplicationDetail('uvd', 'UVD', href='https://viewer.example/session/123', mode='xpra'),
    ])
    report = result.to_report_dict(coverage_gui=mode, application_detail_resolver=unexpected)
    assert [link['label'] for link in report['details']] == expected
    assert not report['warnings']
    if mode == 'xpra':
        assert report['details'][1]['href'] == 'https://viewer.example/session/123'


@pytest.mark.parametrize('href', ['javascript:alert(1)', '//other.test/app', 'file:///etc/passwd', 'apps\\viewer.html'])
def test_provider_application_url_uses_shared_validation(href):
    """Direct links obey the same URL rules as generated links."""
    result = CodeCoverageResult('uvs', metrics={'line': MetricSummary(3, 4, available=True)},
        details=[HtmlDetail('coverage/index.html'), ApplicationDetail('uvd', 'UVD', href=href)])
    report = result.to_report_dict(coverage_gui='xpra')
    assert [link['label'] for link in report['details']] == ['HTML']
    assert report['hints'] == 3 and report['warnings']


def test_vcs_optional_xpra_page_failure_preserves_html(tmp_path, monkeypatch):
    """An unavailable launch page must not erase the URG report or native inputs."""
    from toffee_test.utils.code_coverage.vcs import VcsCoverageProvider
    import importlib
    xpra = importlib.import_module("toffee_test.utils.code_coverage.xpra")
    def fail(*args, **kwargs):
        raise OSError("Cannot write application page")
    monkeypatch.setattr(xpra, 'write_xpra_link', fail)
    result = CodeCoverageResult('vcs', detail='native/dashboard.html', databases=['dut.vdb'])
    details = VcsCoverageProvider().build_details(result, tmp_path)
    assert isinstance(details[0], HtmlDetail)
    assert details[1].artifacts == {'database': 'dut.vdb'}
    assert 'Cannot write application page' in result.warnings[0]
