"""SSH discovery, private pairing, remote deployment and real terminal selection."""
import contextlib
import io
import json
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mac'))
import bridge_setup as setup


class SetupWizardTests(unittest.TestCase):
    def test_ssh_alias_discovery_includes_and_cycles(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as temp:
            folder = Path(temp)
            (folder / 'config').write_text('Host work *.wild !no\n Include conf.d/*\nHost work\nHost=build\n')
            (folder / 'conf.d').mkdir()
            (folder / 'conf.d/hosts').write_text('Host vps\nInclude config\nHost bad;command /bad\n')
            self.assertEqual(setup.ssh_hosts(folder / 'config'), ['work', 'vps', 'build'])

    def test_pairing_code_is_stdin_only_and_both_ends_are_verified(self):
        ssh = Mock()
        ssh.run.side_effect = [json.dumps({'ok': True, 'code': 'abcd-2345'}), json.dumps({'uplink': True})]
        with patch.object(setup, 'checked', return_value=json.dumps({'ok': True, 'status': {'state': 'connected'}})) as run:
            setup.pair(ssh, ['cli'], 'host.example', 3325)
        self.assertEqual(run.call_args.kwargs['input'], b'abcd-2345\n')
        self.assertNotIn('abcd-2345', repr(run.call_args.args))
        self.assertIn('--code-stdin', run.call_args.args[0])
        self.assertEqual(ssh.run.call_count, 2)

    def test_no_success_if_linux_does_not_confirm_uplink(self):
        ssh = Mock()
        ssh.run.side_effect = [json.dumps({'ok': True, 'code': 'abcd-2345'}), json.dumps({'uplink': False})]
        with patch.object(setup, 'checked', return_value=json.dumps({'ok': True, 'status': {'state': 'connected'}})):
            with self.assertRaises(setup.SetupError):
                setup.pair(ssh, ['cli'], 'host.example', 3325)

    def test_remote_deploy_uploads_verified_assets_and_uses_offline_mode(self):
        ssh = Mock()
        ssh.run.side_effect = ['Linux', '', '/tmp/ego-bridge-setup.abcdef', '', '']
        with patch.object(setup, 'release_files', return_value=[Path('/tmp/install.sh')]):
            with contextlib.redirect_stdout(io.StringIO()):
                setup.deploy(ssh, 'v2.4.0', 3349, Path('/tmp'))
        ssh.upload.assert_called_once()
        command = ssh.run.call_args_list[3].args[0]
        self.assertIn('--from-dir /tmp/ego-bridge-setup.abcdef', command)
        self.assertIn('--non-interactive --port 3349', command)
        self.assertEqual(ssh.run.call_args_list[-1].args[0], 'rm -rf -- /tmp/ego-bridge-setup.abcdef')

    def test_non_linux_target_is_not_installed(self):
        ssh = Mock()
        ssh.run.return_value = 'Darwin'
        with patch.object(setup, 'release_files') as download:
            with self.assertRaises(setup.SetupError):
                setup.deploy(ssh, 'v2.4.0', 3325, Path('/tmp'))
        download.assert_not_called()
        ssh.upload.assert_not_called()

    def test_ssh_target_and_port_validation(self):
        self.assertTrue(setup.valid_target('alice@host.example'))
        self.assertTrue(setup.valid_target('devbox'))
        for invalid in ['-oProxyCommand=command', 'user@host;whoami', 'host with space', '$(touch /tmp/a)']:
            self.assertFalse(setup.valid_target(invalid))
        with self.assertRaises(ValueError):
            setup.port_number('65536')

    def test_commands_cannot_consume_curl_pipeline_stdin(self):
        with patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'ok', b'')) as run:
            setup.checked(['ssh', 'host', 'uname -s'])
        self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)

    def test_yes_requires_explicit_destination(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(setup.main(['--yes']), 1)

    def test_arrow_selection_with_piped_stdin_and_real_controlling_tty(self):
        master, slave = pty.openpty()
        script = ('import fcntl,termios,sys;fcntl.ioctl(0,termios.TIOCSCTTY,0);'
                  'sys.path.insert(0,' + repr(str(ROOT / 'mac')) + ');'
                  'from bridge_setup import Terminal; t=Terminal();'
                  'print("selected="+str(t.select("Choose",["first","second"])));t.close()')
        process = subprocess.Popen([sys.executable, '-c', script], stdin=slave, stdout=slave,
                                   stderr=slave, start_new_session=True)
        os.close(slave)
        output = bytearray()
        try:
            deadline = time.monotonic() + 5
            sent = False
            while time.monotonic() < deadline:
                if select.select([master], [], [], .1)[0]:
                    try: chunk = os.read(master, 65536)
                    except OSError: break
                    if not chunk: break
                    output.extend(chunk)
                    if b'second' in output and not sent:
                        os.write(master, b'\x1b[B\r')
                        sent = True
                if process.poll() is not None:
                    break
            self.assertEqual(process.wait(timeout=2), 0, output.decode(errors='replace'))
            while select.select([master], [], [], 0)[0]:
                try: chunk = os.read(master, 65536)
                except OSError: break
                if not chunk: break
                output.extend(chunk)
            self.assertIn(b'selected=1', output)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)


if __name__ == '__main__':
    unittest.main(verbosity=2)
