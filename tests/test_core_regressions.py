#!/usr/bin/env python3
"""Socket timeout/framing, control ownership, persisted state and command results."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mac'))
import bridge_core as bridge


class CoreRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ego-regression-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.socket_path = self.temp.name + '/control/control.sock'
        for key, value in [('CONTROL_SOCKET', self.socket_path),
                           ('SETTINGS_PATH', self.temp.name + '/control/settings.json')]:
            p = patch.object(bridge, key, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(bridge, 'conn_load', return_value=(None, None))
        p.start()
        self.addCleanup(p.stop)
        self.core = bridge.Core(argparse.Namespace(cli='/usr/bin/false',
                                sessions_dir=self.temp.name + '/sessions'))
        self.core.versions.collect = lambda: {'appRunning': True}
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)
        self.addCleanup(self.core.want_exit.set)
        self.addCleanup(self.core.close_uplink)

    def start_reader(self, peer):
        self.core.sock = peer
        errors = []
        def read():
            try:
                self.core.uplink_read_loop(peer)
            except (ConnectionError, OSError) as e:
                errors.append(str(e))
        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.core.want_exit.set)
        return thread, errors

    def test_idle_heartbeats_and_partial_json_survive_timeouts(self):
        left, right = socket.socketpair()
        self.addCleanup(right.close)
        with patch.object(bridge, 'PING_INTERVAL', .05), patch.object(bridge, 'UPLINK_READ_TIMEOUT', .6):
            messages = []
            self.core.handle_result_get = messages.append
            thread, errors = self.start_reader(left)
            right.settimeout(1)
            right.sendall(b'{"op":"result_')
            # Deliberately leave a frame incomplete through several socket timeouts.
            time.sleep(.18)
            right.sendall(b'get","requestId":"once"}\n{"op":"pong"}\n')
            buffer = b''
            pings = 0
            deadline = time.monotonic() + .4
            while time.monotonic() < deadline:
                buffer += right.recv(4096)
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    if json.loads(line)['op'] == 'ping':
                        pings += 1
                        right.sendall(b'{"op":"pong"}\n')
            self.assertGreaterEqual(pings, 3)
            self.assertTrue(thread.is_alive(), errors)
            self.assertEqual(messages, [{'op': 'result_get', 'requestId': 'once'}])
            self.core.want_exit.set()
            thread.join(1)

    def test_silent_peer_is_detected(self):
        left, right = socket.socketpair()
        self.addCleanup(right.close)
        with patch.object(bridge, 'PING_INTERVAL', .04), patch.object(bridge, 'UPLINK_READ_TIMEOUT', .16):
            thread, errors = self.start_reader(left)
            thread.join(1)
            self.assertFalse(thread.is_alive())
            self.assertIn('keepalive timeout', errors)

    def test_cli_control_pushes_status_and_hides_credentials(self):
        self.core.conn_info = {'host': 'example.test', 'port': 3325,
                               'token': 'private-token', 'certFp': 'private-pin'}
        self.core.start_control()
        self.addCleanup(self.core.control_listener.close)
        self.addCleanup(self.core.control_lock.close)
        env = dict(os.environ, EGO_BRIDGE_CONTROL_SOCKET=self.socket_path)
        command = [sys.executable, str(ROOT / 'mac/ego-bridge'), '--json']
        result = subprocess.run(command + ['disconnect'], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['status']['enabled'])
        self.assertNotIn('private-token', result.stdout + self.output.getvalue())
        self.assertNotIn('private-pin', result.stdout + self.output.getvalue())
        self.assertTrue(any(json.loads(line)['state'] == 'off'
                            for line in self.output.getvalue().splitlines()))
        self.core.dispatch({'cmd': 'start'})
        restored = bridge.Core(argparse.Namespace(cli='/usr/bin/false',
                              sessions_dir=self.temp.name + '/sessions'))
        # Loading a saved true flag without credentials must stay disabled.
        self.assertFalse(restored.enabled)
        with patch.object(bridge, 'conn_load', return_value=(self.core.conn_info, 'test')):
            restored = bridge.Core(argparse.Namespace(cli='/usr/bin/false',
                                  sessions_dir=self.temp.name + '/sessions'))
        self.assertTrue(restored.enabled)

    def test_failed_pair_reports_completion_preserves_existing_link(self):
        self.core.conn_info = {'host': 'old.test', 'port': 3325, 'token': 'keep'}
        self.core.enabled = True
        self.core.state = 'connected'
        with patch.object(bridge, 'dial', return_value=bridge.DialResult(False, fail='bad code')):
            reply = self.core.dispatch({'cmd': 'pair', 'host': 'new.test', 'port': 3325,
                                        'code': 'wrong', 'source': 'app', 'operation': True})
        self.assertFalse(reply['ok'])
        self.assertEqual(self.core.conn_info['host'], 'old.test')
        self.assertTrue(self.core.enabled)
        events = [json.loads(line) for line in self.output.getvalue().splitlines()]
        self.assertTrue(events[-1]['commandDone'])
        self.assertIn('bad code', events[-1]['operationError'])
        self.core.dispatch({'cmd': 'refresh'})
        self.assertIn('bad code', self.core.operation_error)

    def test_invalid_configuration_leaves_state_untouched(self):
        reply = self.core.dispatch({'cmd': 'configure', 'host': 'host', 'port': 70000})
        self.assertFalse(reply['ok'])
        self.assertIsNone(self.core.conn_info)
        self.assertFalse(self.core.enabled)

    def test_activity_subscription_never_completes_a_pending_form(self):
        self.core.operation_error = 'keep form error'
        reply = self.core.dispatch({'cmd': 'watch_activity', 'enabled': True, 'source': 'app'})
        self.assertTrue(reply['ok'])
        self.assertTrue(self.core.watch_activity.is_set())
        self.assertEqual(self.core.operation_error, 'keep form error')
        self.assertFalse(json.loads(self.output.getvalue().splitlines()[-1])['commandDone'])
        self.core.dispatch({'cmd': 'watch_activity', 'enabled': False})
        self.assertFalse(self.core.watch_activity.is_set())
        self.assertEqual(self.core.activity.snapshot()['state'], 'idle')

    def test_old_task_cannot_send_on_new_connection(self):
        old, old_peer = socket.socketpair()
        new, new_peer = socket.socketpair()
        for sock in [old, old_peer, new, new_peer]:
            self.addCleanup(sock.close)
        self.core.sock = new
        self.assertFalse(self.core.send_uplink({'op': 'ev'}, expected=old))
        self.assertTrue(self.core.send_uplink({'op': 'ping'}, expected=new))
        self.assertEqual(json.loads(new_peer.recv(1024)), {'op': 'ping'})

    def test_second_owner_cannot_replace_control_socket(self):
        self.core.start_control()
        self.addCleanup(self.core.control_listener.close)
        self.addCleanup(self.core.control_lock.close)
        other = bridge.Core(argparse.Namespace(cli='/usr/bin/false',
                            sessions_dir=self.temp.name + '/sessions'))
        with self.assertRaises(SystemExit):
            other.start_control()
        other.control_lock.close()
        self.assertTrue(os.path.exists(self.socket_path))
        self.assertEqual(os.stat(self.socket_path).st_mode & 0o777, 0o600)

    def test_stop_during_dial_rejects_late_success(self):
        self.core.conn_info = {'host': 'host', 'port': 3325, 'token': 'test'}
        self.core.enabled = True
        entered, release = threading.Event(), threading.Event()
        left, right = socket.socketpair()
        self.addCleanup(right.close)
        def delayed(*args, **kwargs):
            entered.set()
            release.wait(2)
            return bridge.DialResult(True, sock=left)
        with patch.object(bridge, 'dial', side_effect=delayed):
            thread = threading.Thread(target=self.core.supervisor, daemon=True)
            thread.start()
            self.assertTrue(entered.wait(1))
            self.core.dispatch({'cmd': 'stop'})
            release.set()
            deadline = time.monotonic() + 1
            while left.fileno() != -1 and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(left.fileno(), -1)
            self.assertFalse(self.core.enabled)
            self.assertEqual(self.core.state, 'off')
            self.assertIsNone(self.core.sock)
            self.core.want_exit.set()
            self.core.kick.set()
            thread.join(1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
