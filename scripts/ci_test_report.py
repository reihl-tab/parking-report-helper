"""Aggregate unittest metrics for CI job summaries. Never include failure bodies."""
import argparse
import html
import re
import json
import os
from pathlib import Path
import sys
import unittest



def safe_text(value):
    text = str(value).replace('\n', ' ')[:500]
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[email]', text)
    text = re.sub(r'\b[0-9][0-9 -]{5,}[0-9]\b', '[number]', text)
    text = re.sub(r'(?:[A-Za-z]:)?/[^ ]+', '[path]', text)
    text = re.sub(r'(?i)(token|password|secret|key)\s*[:=]\s*[^ ]+', r'\1=[redacted]', text)
    return html.escape(text).replace('|', '&#124;')


class DetailedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.details = []

    def record(self, test, status, reason=''):
        # Subtest repr can embed private parameter values; keep only parent ID.
        parent = getattr(test, 'test_case', test)
        self.details.append({'test': html.unescape(safe_text(parent.id())), 'status': status,
            'checks': html.unescape(safe_text(parent.shortDescription() or parent.id().split('.')[-1])),
            'reason': html.unescape(safe_text(reason))})

    def addSuccess(self, test):
        super().addSuccess(test)
        self.record(test, 'passed')

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.record(test, 'failed', err[0].__name__+'; assertion details in job logs')

    def addError(self, test, err):
        super().addError(test, err)
        self.record(test, 'error', err[0].__name__+'; traceback in job logs')

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.record(test, 'skipped', reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self.record(test, 'expected failure', err[0].__name__)

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.record(test, 'unexpected success', 'Expected failure passed')

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self.record(test, 'failed' if issubclass(err[0], test.failureException) else 'error',
                        err[0].__name__+' in subtest; parameters omitted, see logs')

def run_tests(start, output):
    suite = unittest.defaultTestLoader.discover(start)
    result = unittest.TextTestRunner(verbosity=2, resultclass=DetailedResult).run(suite)
    metrics = {'tests': result.testsRun, 'failures': len(result.failures),
               'errors': len(result.errors), 'skipped': len(result.skipped),
               'expected_failures': len(result.expectedFailures),
               'unexpected_successes': len(result.unexpectedSuccesses)}
    metrics['passed'] = metrics['tests'] - sum(metrics[key] for key in
        ('failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'))
    metrics['success'] = result.wasSuccessful()
    metrics['details'] = result.details
    Path(output).write_text(json.dumps(metrics))
    return 0 if result.wasSuccessful() else 1


def summary(metrics_path, coverage_path=None):
    try:
        data = json.loads(Path(metrics_path).read_text())
        fields = [('Tests run', 'tests'), ('Passed', 'passed'), ('Failures', 'failures'),
                  ('Errors', 'errors'), ('Skipped', 'skipped'),
                  ('Expected failures', 'expected_failures'),
                  ('Unexpected successes', 'unexpected_successes')]
        if any(type(data[key]) is not int or data[key] < 0 for _, key in fields):
            raise ValueError('Invalid metrics')
        if type(data['success']) is not bool:
            raise ValueError('Invalid success flag')
        heading = 'PASS' if data['success'] else 'FAIL'
        lines = ['## Test results: '+heading, '', '| Metric | Count |', '| --- | ---: |']
        lines.extend(f'| {title} | {data[key]} |' for title,key in fields)
    except (OSError, ValueError, KeyError, TypeError):
        lines = ['## Test results: unavailable', '',
                 'The test runner did not produce valid aggregate metrics. Check the job logs.']
    coverage = None
    if coverage_path:
        try:
            totals = json.loads(Path(coverage_path).read_text())['totals']
            value = totals['percent_covered']
            if type(value) in (int, float) and 0 <= value <= 100:
                coverage = f'{value:.2f}% (coverage.py statement/branch measurement)'
        except (OSError, ValueError, TypeError, KeyError):
            pass
    lines.extend(['', 'Coverage: '+(coverage or 'not measured or unavailable in this job.')])
    try:
        details = data.get('details', [])
        priority = {'failed':0,'error':1,'unexpected success':2,'skipped':3,
                    'expected failure':4,'passed':5}
        if details:
            lines.extend(['', '<details>', '<summary>Per-test results (failures and skips first)</summary>',
                          '', '| Status | Test | What it checks | Reason |', '| --- | --- | --- | --- |'])
            for row in sorted(details, key=lambda row:priority.get(row.get('status'),9)):
                # Data file is not an authority for HTML or unsafe runtime text.
                values = [safe_text(row.get(key,'')) for key in ('status','test','checks','reason')]
                lines.append('| '+' | '.join(values)+' |')
            lines.extend(['', '</details>'])
    except (UnboundLocalError, TypeError, AttributeError):
        lines.extend(['', 'Per-test details unavailable.'])
    lines.extend(['', 'Assertion bodies/tracebacks stay in job logs; source names and safe reasons are shown.', ''])
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    run = sub.add_parser('run')
    run.add_argument('--start', default='tests')
    run.add_argument('--output', default='test-results.json')
    render = sub.add_parser('summary')
    render.add_argument('--metrics', default='test-results.json')
    render.add_argument('--coverage', default=None)
    args = parser.parse_args(argv)
    if args.action == 'run':
        return run_tests(args.start, args.output)
    text = summary(args.metrics, args.coverage)
    destination = os.environ.get('GITHUB_STEP_SUMMARY')
    if destination:
        with open(destination, 'a') as stream:
            stream.write(text)
    else:
        print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
