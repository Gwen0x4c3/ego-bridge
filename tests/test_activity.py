import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'mac'))
from ego_activity import BrowserActivity, MARKER


class ActivityTests(unittest.TestCase):
    def test_parse_keeps_only_display_fields_and_limits_rows(self):
        rows = [{'id': i, 'name': 'Agent ' + str(i), 'ownership': 'agent',
                 'recentTitle': 'Example Domain', 'token': 'private'} for i in range(5)]
        parsed = BrowserActivity.parse('notice\n' + MARKER + json.dumps(rows))
        self.assertEqual(parsed['total'], 5)
        self.assertEqual(len(parsed['spaces']), 3)
        self.assertNotIn('private', json.dumps(parsed))
        self.assertEqual(parsed['spaces'][0]['recentTitle'], 'Example Domain')

    def test_closed_browser_does_not_invoke_cli(self):
        monitor = BrowserActivity('/fake/ego-browser')
        with patch('ego_activity.subprocess.run', return_value=subprocess.CompletedProcess([], 1)) as run:
            monitor.collect()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][0], 'pgrep')
        self.assertEqual(monitor.snapshot()['state'], 'unavailable')

    def test_stderr_result_is_observed_without_retaining_history(self):
        monitor = BrowserActivity('/fake/ego-browser')
        output = MARKER + '[{"id":7,"name":"Inspect","ownership":"agent","recentTitle":"Example"}]'
        responses = [subprocess.CompletedProcess([], 0),
                     subprocess.CompletedProcess([], 0, stdout='', stderr=output)]
        with patch('ego_activity.subprocess.run', side_effect=responses):
            monitor.collect()
        self.assertEqual(monitor.snapshot()['spaces'][0]['name'], 'Inspect')
        with patch('ego_activity.subprocess.run', side_effect=subprocess.TimeoutExpired('pgrep', 2)):
            monitor.collect()
        self.assertEqual(monitor.snapshot()['state'], 'unavailable')
        self.assertEqual(monitor.snapshot()['spaces'], [])
        monitor.clear()
        self.assertIsNone(monitor.snapshot()['updatedAt'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
