"""Exercise the no-build installer against a local Release fixture."""
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import json
import signal
import socket
import tarfile
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReleaseInstallerTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='ego-release-test-', dir='/tmp')
        self.addCleanup(self.work.cleanup)
        self.base = Path(self.work.name)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.prefix = self.home / 'Space Prefix'
        self.version = ROOT.joinpath('VERSION').read_text().strip()
        self.tag = 'v' + self.version
        self.assets = self.base / 'web/download' / self.tag
        self.assets.mkdir(parents=True)
        self.name = 'ego-bridge-' + self.tag + '-linux.tar.gz'
        with tarfile.open(self.assets / self.name, 'w:gz') as archive:
            for file in ['VERSION', 'linux/ego-browser', 'linux/ego_agent_service.py', 'linux/ego-bridge.service']:
                archive.add(ROOT / file, arcname=file)
        digest = hashlib.sha256((self.assets / self.name).read_bytes()).hexdigest()
        (self.assets / 'SHA256SUMS').write_text(digest + '  ' + self.name + '\n')
        fake_bin = self.base / 'fake-bin'
        fake_bin.mkdir()
        (fake_bin / 'uname').write_text('#!/bin/sh\ncase "$1" in -s) echo Linux;; -m) echo x86_64;; esac\n')
        (fake_bin / 'uname').chmod(0o755)
        (fake_bin / 'systemctl').write_text('#!/bin/sh\nexit 1\n')
        (fake_bin / 'systemctl').chmod(0o755)
        class Quiet(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(self.base / 'web')))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(fake_bin) + ':' + os.environ['PATH'],
                        EGO_BRIDGE_RELEASE_BASE_URL='http://127.0.0.1:' + str(self.server.server_port))
        self.env.pop('EGO_BRIDGE_APP_DIR', None)
        self.env.pop('EGO_BRIDGE_PREFIX', None)
        self.env['EGO_AGENT_CONF'] = str(self.home / '.config/ego-bridge/server')
        self.env['EGO_BRIDGE_CONFIG'] = str(self.home / '.config/ego-bridge/shim.json')

    def install(self):
        return subprocess.run(['bash', str(ROOT / 'install.sh'), '--version', self.tag,
                               '--no-start', '--prefix', str(self.prefix)],
                              env=self.env, text=True, capture_output=True, timeout=20)

    def test_install_reinstall_and_pairing_preservation(self):
        credential = self.home / '.config/ego-bridge/server/token'
        credential.parent.mkdir(parents=True)
        credential.write_text('preserve-pairing')
        for _ in range(2):
            result = self.install()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((self.prefix / 'bin/ego-browser').is_symlink())
            service = subprocess.run([str(self.prefix / 'bin/ego-agent-service'), '--help'],
                                     capture_output=True, text=True, timeout=5)
            self.assertEqual(service.returncode, 0, service.stderr)
            self.assertEqual(credential.read_text(), 'preserve-pairing')

    def test_corrupt_download_does_not_install(self):
        (self.assets / self.name).write_bytes(b'corrupted payload')
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SHA-256 mismatch', result.stderr)
        self.assertFalse((self.prefix / 'bin').exists())

    def test_unrelated_browser_command_is_preserved(self):
        command = self.prefix / 'bin/ego-browser'
        command.parent.mkdir(parents=True)
        command.write_text('unrelated-command')
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(command.read_text(), 'unrelated-command')

    def test_offline_install_starts_service_and_provides_json_pairing(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        def stop():
            pid = self.prefix / 'share/ego-bridge/run/service.pid'
            if pid.exists():
                try: os.kill(int(pid.read_text()), signal.SIGTERM)
                except ProcessLookupError: pass
        self.addCleanup(stop)
        result = subprocess.run(['bash', str(ROOT / 'install.sh'), '--version', self.tag,
                                 '--from-dir', str(self.assets), '--non-interactive',
                                 '--prefix', str(self.prefix), '--port', str(port)],
                                env=self.env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        pair = subprocess.run([str(self.prefix / 'bin/ego-browser'), 'pair', '--json'],
                              env=self.env, text=True, capture_output=True, timeout=5)
        self.assertEqual(pair.returncode, 0, pair.stderr)
        self.assertRegex(json.loads(pair.stdout)['code'], r'^[a-z2-9]{4}-[a-z2-9]{4}$')
        self.assertEqual(json.loads(Path(self.env['EGO_BRIDGE_CONFIG']).read_text())['port'], port)


if __name__ == '__main__':
    unittest.main(verbosity=2)
