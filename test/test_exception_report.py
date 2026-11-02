"""Exception identities must survive pytest worker transport in JSON reports."""
import json
import os
from pathlib import Path

import pytest


@pytest.mark.parametrize('worker', ['normal', 'forked', 'xdist'])
@pytest.mark.parametrize('phase', ['setup', 'call', 'teardown'])
def test_json_exception_identity_survives_worker_transport(pytester, monkeypatch, worker, phase):
    """Record real exception data without requiring a parent-side CallInfo object."""
    root = str(Path(__file__).parents[1])
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join([root, os.environ.get('PYTHONPATH', '')]))
    monkeypatch.delenv('PYTEST_ADDOPTS', raising=False)
    if worker == 'forked':
        pytest.importorskip('pytest_forked')
    if phase == 'call':
        pytester.makepyfile('def test_failure():\n    assert False, "Not implemented"\n')
        exception_type, message = 'AssertionError', 'Not implemented'
    else:
        body = '    raise ValueError("fixture failed")\n'
        if phase == 'teardown':
            body = '    yield\n' + body
        pytester.makepyfile('import pytest\n@pytest.fixture\ndef resource():\n' + body +
                           '\ndef test_failure(resource):\n    pass\n')
        exception_type, message = 'ValueError', 'fixture failed'
    args = ['--toffee-report', '--report-dump-json', '--report-name=index.html', '--report-dir=report']
    if worker == 'forked':
        args.append('--forked')
    elif worker == 'xdist':
        pytest.importorskip('xdist')
        args += ['-n', '2']
    result = pytester.runpytest_subprocess(*args)
    assert result.ret != 0
    data = json.loads((pytester.path / 'report/toffee_report.json').read_text())
    failures = [p['exception'] for t in data['tests'] for p in t['phases'] if 'exception' in p]
    assert len(failures) == 1
    assert failures[0]['phase'] == phase
    assert failures[0]['type'] == exception_type
    assert message in failures[0]['message']
