"""Final report links follow the provider contract, independent of simulator."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
import pytest

from toffee_test import reporter
from toffee_test.utils.code_coverage import CodeCoverageResult, MetricSummary
from toffee_test.utils.code_coverage.common import html_report_detail


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.hrefs.append(dict(attrs).get('href'))


def render_coverage(line):
    env = Environment(loader=ChoiceLoader([
        DictLoader({'html/index.html': '{% block coverage %}{% endblock %}'}),
        FileSystemLoader(reporter.get_template_dir()),
    ]), autoescape=True)
    return env.get_template('html/toffee.html').render(
        coverages={'line': line, 'functional': None},
    )


@pytest.mark.parametrize('simulator,entry', [
    ('vcs', 'native/vcs_urg/dashboard.html'),
    ('verilator', 'index.html'),
    ('future_simulator', 'native/report with spaces/index.html'),
])
def test_final_report_opens_provider_html(tmp_path, monkeypatch, simulator, entry):
    report_dir = tmp_path / 'report with spaces'
    output_dir = report_dir / 'coverage_data'
    page = output_dir / entry
    page.parent.mkdir(parents=True)
    page.write_text('<html>Native report</html>')
    (page.parent / 'style.css').write_text('body {}')
    detail = html_report_detail(page.parent, output_dir, entry_names=(page.name,))
    result = CodeCoverageResult(
        simulator, metrics={'line': MetricSummary(covered=8, total=10, available=True)},
        detail=detail,
    )
    monkeypatch.setattr(reporter, '__output_report_dir__', str(report_dir))
    monkeypatch.setattr(reporter, 'convert_code_coverage', lambda *args: result)
    monkeypatch.delenv('TOFFEE_VCS_AGGREGATE_MODE', raising=False)
    line = reporter.__update_line_coverage__([{'simulator': simulator, 'database': 'database'}])
    html = render_coverage(line)
    links = Links()
    links.feed(html)
    assert 'Line Coverage' in html and '80.0%' in html
    assert links.hrefs == [detail]
    assert (report_dir / unquote(links.hrefs[0])).resolve() == page.resolve()
    assert (page.parent / 'style.css').is_file()
    assert 'target="_blank"' not in html


def test_missing_html_entry_is_explicit(tmp_path):
    with pytest.raises(FileNotFoundError, match='HTML entry'):
        html_report_detail(tmp_path, tmp_path)


def test_native_html_stays_inside_coverage_output(tmp_path):
    outside = tmp_path / 'outside.html'
    outside.write_text('outside')
    output = tmp_path / 'coverage_data'
    output.mkdir()
    (output / 'index.html').symlink_to(outside)
    with pytest.raises(ValueError, match='inside'):
        html_report_detail(output, output)


def test_report_shows_failure_without_fake_zero_coverage(tmp_path, monkeypatch):
    def fail(*args):
        raise RuntimeError('native report <failed>')

    monkeypatch.setattr(reporter, '__output_report_dir__', str(tmp_path))
    monkeypatch.setattr(reporter, 'convert_code_coverage', fail)
    monkeypatch.delenv('TOFFEE_VCS_AGGREGATE_MODE', raising=False)
    line = reporter.__update_line_coverage__([{'database': 'missing'}])
    html = render_coverage(line)
    assert 'Coverage report generation failed:' in html
    assert '&lt;failed&gt;' in html
    assert '0.0%' not in html
    assert 'View Details' not in html


def test_zero_line_coverage_still_links_to_native_other_metrics():
    line = CodeCoverageResult('vcs', detail='coverage_data/native/vcs_urg/dashboard.html').to_report_dict()
    html = render_coverage(line)
    assert 'View Details' in html
    assert '0.0%' not in html


def test_report_shows_deferred_and_warning_states():
    html = render_coverage({
        'total': 0, 'hints': 0, 'grate': 99, 'detail': '',
        'collection_mode': 'deferred', 'warnings': ['Source <unavailable>'],
    })
    assert 'Coverage collection is deferred' in html
    assert 'Source &lt;unavailable&gt;' in html
    assert 'View Details' not in html


def test_native_report_summary_is_separate_from_dut_metrics():
    result = CodeCoverageResult(
        'another_simulator',
        metrics={'line': MetricSummary(covered=0, total=0)},
        report_metrics={'line': MetricSummary(covered=1, total=3, rate=33.33, available=True)},
        report_scope='Native report: full hierarchy, including generated wrappers.',
        detail='coverage_data/native/index.html',
    )
    line = result.to_report_dict()
    assert (line['hints'], line['total']) == (1, 3)
    assert line['metrics']['line']['total'] == 0
    assert line['report_metrics']['line']['total'] == 3
    html = render_coverage(line)
    assert '33.33%' in html
    assert 'including generated wrappers' in html
    assert 'View Details' in html


@pytest.mark.parametrize('line_rate', ['33.33 1/3', '100.00 3/3', '-- 0/0'])
def test_urg_native_summary_reads_exact_counts(tmp_path, line_rate):
    from toffee_test.utils.code_coverage.vcs import _load_urg_report_metrics

    (tmp_path / 'dashboard.txt').write_text(
        'Dashboard\nTotal Coverage Summary \n'
        'SCORE LINE COND TOGGLE FSM BRANCH\n'
        f'33.07 {line_rate} -- 0/0 15.89 123/774 -- 0/0 50.00 1/2\n'
        '\nTotal Module Definition Coverage Summary\n'
        'SCORE LINE\n0.00 0.00 0/100\n'
    )
    metrics = _load_urg_report_metrics(tmp_path)
    expected = line_rate.split()[1].split('/')
    assert (metrics['line'].covered, metrics['line'].total) == tuple(map(int, expected))
    assert metrics['line'].available == (line_rate != '-- 0/0')
    assert not metrics['condition'].available
    assert (metrics['toggle'].covered, metrics['toggle'].total) == (123, 774)


def test_malformed_native_summary_is_not_silently_displayed_as_zero(tmp_path):
    from toffee_test.utils.code_coverage.vcs import _load_urg_report_metrics

    (tmp_path / 'dashboard.txt').write_text('Total Coverage Summary\nSCORE LINE\n33.33\n')
    with pytest.raises(ValueError, match='ratios'):
        _load_urg_report_metrics(tmp_path)


@pytest.mark.parametrize('simulator', ['vcs', 'verilator'])
@pytest.mark.parametrize('outcome', ['FAILED', 'PASSED', 'ERROR'])
def test_final_coverage_includes_registered_records_from_failed_tests(monkeypatch, simulator, outcome):
    """Test outcomes stay intact while every actual coverage record reaches its provider."""
    from types import SimpleNamespace
    from toffee_test.utils.code_coverage import CoverageRecord

    record = dict(CoverageRecord(simulator, 'native.vdb' if simulator == 'vcs' else 'case.dat').to_dict(),
                  hash='coverage', id='worker')
    report = SimpleNamespace(__line_coverage__=record)
    context = {'metadata': {}, 'session': SimpleNamespace(), 'tests': [
        {'item': SimpleNamespace(nodeid='test_device.py::test_device', function=None),
         'status': {'word': outcome}, 'phases': [{'report': report}, {'report': report}]}]}
    converted = []
    def convert(records, grate):
        """Capture provider inputs and return an explicit measured summary."""
        converted.extend(records)
        return {'hints': 1, 'total': 2, 'grate': grate, 'detail': 'native/index.html'}
    monkeypatch.setattr(reporter, '__update_line_coverage__', convert)
    monkeypatch.setattr(reporter, 'get_toffee_custom_key_value', lambda: {})
    config = SimpleNamespace(getoption=lambda name: False,
                             option=SimpleNamespace(toffee_report_dump_json=False))
    reporter.process_context(context, config)
    assert converted == [record]
    assert context['coverages']['line']['total'] == 2
    assert context['tests'][0]['status']['word'] == outcome
    assert list(context['test_abstract_info'].values()) == [outcome]


def test_final_coverage_without_registered_data_remains_unavailable(monkeypatch):
    """A setup failure with no hardware trace must never invent coverage."""
    from types import SimpleNamespace
    context = {'metadata': {}, 'session': SimpleNamespace(), 'tests': [
        {'item': SimpleNamespace(nodeid='test_device.py::test_device', function=None),
         'status': {'word': 'ERROR'}, 'phases': [{'report': SimpleNamespace()}]}]}
    monkeypatch.setattr(reporter, 'get_toffee_custom_key_value', lambda: {})
    config = SimpleNamespace(getoption=lambda name: False,
                             option=SimpleNamespace(toffee_report_dump_json=False))
    reporter.process_context(context, config)
    assert context['coverages']['line'] is None
