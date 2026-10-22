"""Aggregate unittest metrics for CI job summaries. Never include failure bodies."""
import argparse
import json
import os
from pathlib import Path
import sys
import unittest


def run_tests(start, output):
    suite = unittest.defaultTestLoader.discover(start)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    metrics = {'tests': result.testsRun, 'failures': len(result.failures),
               'errors': len(result.errors), 'skipped': len(result.skipped),
               'expected_failures': len(result.expectedFailures),
               'unexpected_successes': len(result.unexpectedSuccesses)}
    metrics['passed'] = metrics['tests'] - sum(metrics[key] for key in
        ('failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'))
    metrics['success'] = result.wasSuccessful()
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
    lines.extend(['', 'Only aggregate counts are shown. Test details remain in the job logs.', ''])
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
