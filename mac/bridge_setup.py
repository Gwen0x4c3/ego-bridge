#!/usr/bin/env python3
"""Terminal setup: SSH selection, offline Linux deployment, and explicit CLI pairing."""
import argparse
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shlex
import subprocess
import sys
import tarfile
import tempfile
import termios
import tty


class SetupError(Exception):
    pass


def ssh_hosts(config=None):
    """Read explicit aliases and Include directives, never expand wildcard Hosts."""
    home = Path.home() / '.ssh'
    config = Path(config) if config else home / 'config'
    home = config.parent
    visited, found = set(), []
    def visit(path):
        path = Path(path).resolve()
        if path in visited or len(visited) >= 100 or not path.is_file():
            return
        visited.add(path)
        for line in path.read_text(errors='replace').splitlines():
            try:
                fields = shlex.split(line, comments=True)
            except ValueError:
                continue
            if not fields:
                continue
            if '=' in fields[0]:
                key, value = fields[0].split('=', 1)
                fields = [key, value] + fields[1:]
            if fields[0].lower() == 'host':
                for name in fields[1:]:
                    if not any(c in name for c in '*?![]') and valid_target(name) and name not in found:
                        found.append(name)
            elif fields[0].lower() == 'include':
                for pattern in fields[1:]:
                    pattern = os.path.expanduser(os.path.expandvars(pattern))
                    if not os.path.isabs(pattern):
                        pattern = str(home / pattern)
                    for included in sorted(glob.glob(pattern)):
                        visit(included)
    visit(config)
    return found


def valid_target(value):
    return bool(re.fullmatch(r'[A-Za-z0-9_\[\]][A-Za-z0-9_.@:\[\]-]*', value or ''))


def port_number(value):
    number = int(value)
    if not 1 <= number <= 65535:
        raise ValueError('Port must be between 1 and 65535')
    return number


class Terminal:
    def __init__(self):
        try:
            self.fd = os.open('/dev/tty', os.O_RDWR)
        except OSError as error:
            raise SetupError('No interactive terminal. Run ego-bridge setup later, or use --yes --host HOST.') from error

    def close(self):
        os.close(self.fd)

    def write(self, text):
        os.write(self.fd, text.encode())

    def select(self, question, choices, default=0):
        self.write('\n' + question + '\n  ↑/↓ select · Enter confirm · Esc cancel\n')
        old = termios.tcgetattr(self.fd)
        index, rendered = default, False
        visible = min(8, len(choices))
        rows = visible + (1 if len(choices) > visible else 0)
        try:
            tty.setcbreak(self.fd)
            self.write('\x1b[?25l')
            while True:
                if rendered:
                    self.write('\x1b[' + str(rows) + 'A')
                start = min(max(0, index - visible // 2), len(choices) - visible)
                for n in range(start, start + visible):
                    choice = choices[n]
                    self.write('\r\x1b[2K' + ('  › ' if n == index else '    ') + choice + '\n')
                if len(choices) > visible:
                    self.write('\r\x1b[2K    ' + str(index + 1) + '/' + str(len(choices)) + '\n')
                rendered = True
                key = os.read(self.fd, 1)
                if key in (b'\r', b'\n'):
                    return index
                if key == b'\x03':
                    raise KeyboardInterrupt
                if key in (b'q', b'\x04'):
                    return None
                if key == b'\x1b':
                    if not select.select([self.fd], [], [], .12)[0]:
                        return None
                    key += os.read(self.fd, 1)
                    if select.select([self.fd], [], [], .12)[0]:
                        key += os.read(self.fd, 1)
                if key in (b'\x1b[A', b'\x1bOA', b'k'):
                    index = (index - 1) % len(choices)
                elif key in (b'\x1b[B', b'\x1bOB', b'j'):
                    index = (index + 1) % len(choices)
        finally:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, old)
            self.write('\x1b[?25h')

    def ask(self, label, default=''):
        self.write('\n' + label + (' [' + default + ']' if default else '') + ': ')
        answer = bytearray()
        while True:
            byte = os.read(self.fd, 1)
            if not byte:
                raise KeyboardInterrupt
            if byte in (b'\n', b'\r'):
                return answer.decode().strip() or default
            if byte == b'\x03':
                raise KeyboardInterrupt
            answer.extend(byte)

    def confirm(self, question):
        return self.select(question, ['Yes', 'Not now']) == 0


def checked(command, *, input=None, capture=True, timeout=180):
    result = subprocess.run(command, input=input, stdin=subprocess.DEVNULL if input is None else None,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None, timeout=timeout)
    if result.returncode:
        message = (result.stderr or b'').decode(errors='replace').strip()
        raise SetupError(message or 'Command failed: ' + command[0])
    return (result.stdout or b'').decode().strip()


class SSH:
    def __init__(self, target, port):
        if not valid_target(target):
            raise SetupError('Use an SSH alias or user@host, without shell characters.')
        self.directory = tempfile.TemporaryDirectory(prefix='ego-ssh-', dir='/tmp')
        self.options = ['-p', str(port), '-o', 'ConnectTimeout=15', '-o', 'ControlMaster=auto',
                        '-o', 'ControlPersist=90', '-o', 'ControlPath=' + self.directory.name + '/conn']
        self.target = target

    def command(self, script):
        return ['ssh', *self.options, self.target, script]

    def run(self, script, *, capture=True, timeout=180):
        return checked(self.command(script), capture=capture, timeout=timeout)

    def upload(self, remote_dir, files):
        process = subprocess.Popen(self.command('tar -xf - -C ' + shlex.quote(remote_dir)), stdin=subprocess.PIPE)
        try:
            with tarfile.open(fileobj=process.stdin, mode='w|') as archive:
                for file in files:
                    archive.add(file, arcname=file.name, recursive=False)
            process.stdin.close()
            if process.wait(timeout=60):
                raise SetupError('SSH upload failed')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def close(self):
        try:
            subprocess.run(['ssh', *self.options, '-O', 'exit', self.target],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        self.directory.cleanup()


def ssh_address(target, port=None):
    command = ['ssh', '-G'] + (['-p', str(port)] if port else []) + [target]
    config = {}
    for line in checked(command, timeout=15).splitlines():
        fields = line.split(None, 1)
        if len(fields) == 2:
            config.setdefault(fields[0], fields[1])
    return config.get('hostname', target.split('@')[-1]), port_number(config.get('port', '22'))


def release_files(version, directory, base=None):
    if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', version):
        raise SetupError('Invalid Release version')
    base = base or os.environ.get('EGO_BRIDGE_RELEASE_BASE_URL', 'https://github.com/gwen0x4c3/ego-bridge/releases')
    url = base.rstrip('/') + '/download/' + version
    def download(name):
        path = directory / name
        checked(['curl', '-fsSL', '--retry', '3', '--connect-timeout', '15', '--max-time', '180',
                 url + '/' + name, '-o', str(path)], capture=False)
        return path
    manifest = download('SHA256SUMS')
    hashes = {}
    for line in manifest.read_text().splitlines():
        fields = line.split()
        if len(fields) == 2 and re.fullmatch(r'[a-f0-9]{64}', fields[0]):
            hashes[fields[1]] = fields[0]
    names = ['install.sh', 'ego-bridge-' + version + '-linux.tar.gz']
    files = [manifest]
    for name in names:
        if name not in hashes:
            raise SetupError('Release is missing ' + name)
        path = download(name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != hashes[name]:
            raise SetupError('SHA-256 mismatch for ' + name)
        files.append(path)
    return files


def remote_cli(port, *args):
    # Shell quoting is essential: OpenSSH sends remote commands through a login shell.
    resolve = ('if [ -x "$HOME/.local/bin/ego-browser" ]; then cli="$HOME/.local/bin/ego-browser"; '
               'elif [ -x /home/linuxbrew/.linuxbrew/bin/ego-browser ]; then cli=/home/linuxbrew/.linuxbrew/bin/ego-browser; '
               'else cli=$(command -v ego-browser) || exit 127; fi; ')
    return resolve + 'EGO_BRIDGE_PORT=' + str(port) + ' "$cli" ' + ' '.join(shlex.quote(a) for a in args)


def deploy(ssh, version, port, directory):
    if ssh.run('uname -s') != 'Linux':
        raise SetupError('The selected SSH destination is not Linux')
    ssh.run('command -v python3 >/dev/null && command -v openssl >/dev/null && command -v tar >/dev/null')
    print('Downloading Linux runtime on this Mac…', flush=True)
    files = release_files(version, directory)
    remote_dir = ssh.run('mktemp -d /tmp/ego-bridge-setup.XXXXXX')
    if not re.fullmatch(r'/tmp/ego-bridge-setup\.[A-Za-z0-9]+', remote_dir):
        raise SetupError('Unexpected remote staging directory')
    try:
        ssh.upload(remote_dir, files)
        print('Installing Linux runtime over SSH…', flush=True)
        script = 'bash ' + shlex.quote(remote_dir + '/install.sh') + ' --version ' + shlex.quote(version)
        script += ' --from-dir ' + shlex.quote(remote_dir) + ' --non-interactive --port ' + str(port)
        ssh.run(script, capture=False, timeout=180)
    finally:
        ssh.run('rm -rf -- ' + shlex.quote(remote_dir))


def pair(ssh, cli, bridge_host, port):
    try:
        response = json.loads(ssh.run(remote_cli(port, 'pair', '--json')))
    except ValueError as error:
        raise SetupError('Update the Linux runtime first; its CLI must support pair --json.') from error
    code = response.get('code')
    if not response.get('ok') or not isinstance(code, str) or not re.fullmatch(r'[a-z2-9]{4}-[a-z2-9]{4}', code):
        raise SetupError('Linux did not return a valid one-time pairing code')
    # The code is ephemeral stdin only: never put it in argv or print it.
    result = json.loads(checked([*cli, '--json', 'connect', '--host', bridge_host,
                                 '--port', str(port), '--code-stdin'], input=(code + '\n').encode(), timeout=40))
    if not result.get('ok') or result.get('status', {}).get('state') != 'connected':
        raise SetupError(result.get('error') or 'Mac connection is not ready')
    remote = json.loads(ssh.run(remote_cli(port, 'bridge', 'status', '--json')))
    if not remote.get('uplink'):
        raise SetupError('Mac paired, but Linux has not confirmed the connection')


def main(argv=None):
    ap = argparse.ArgumentParser(description='Set up a Linux bridge from this Mac over SSH')
    ap.add_argument('--host', help='Saved SSH alias or user@host')
    ap.add_argument('--ssh-port', type=port_number)
    ap.add_argument('--bridge-host', help='Linux hostname/IP reachable directly from this Mac')
    ap.add_argument('--port', type=port_number, default=3325)
    ap.add_argument('--version')
    ap.add_argument('--yes', action='store_true', help='Install and pair the explicitly specified --host without prompts')
    ap.add_argument('--skip-remote-install', action='store_true')
    ap.add_argument('--cli', type=Path, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    terminal, ssh = None, None
    try:
        version_file = Path(__file__).with_name('VERSION')
        if not version_file.exists():
            version_file = Path(__file__).resolve().parents[1] / 'VERSION'
        version = args.version or 'v' + version_file.read_text().strip()
        cli = [str(args.cli)] if args.cli else [sys.executable, str(Path(__file__).with_name('ego-bridge'))]
        if args.yes and not args.host:
            raise SetupError('--yes requires an explicit --host')
        if not args.yes:
            terminal = Terminal()
            if not args.skip_remote_install and not terminal.confirm('Install the Linux agent on another machine over SSH?'):
                print('Mac installation is ready. Continue later with: ego-bridge setup')
                return 0
        target = args.host
        if not target:
            hosts = ssh_hosts()
            choices = hosts + ['Enter user@host manually', 'Set up later']
            index = terminal.select('Choose a Linux machine', choices)
            if index is None or index == len(choices) - 1:
                return 0
            target = hosts[index] if index < len(hosts) else terminal.ask('SSH destination (user@host)')
        if not valid_target(target):
            raise SetupError('Invalid SSH destination')
        bridge_host, ssh_port = ssh_address(target, args.ssh_port)
        if terminal:
            ssh_port = port_number(terminal.ask('SSH port', str(ssh_port)))
            bridge_host = terminal.ask('Linux address reachable from this Mac', args.bridge_host or bridge_host)
            args.port = port_number(terminal.ask('Bridge port', str(args.port)))
        else:
            bridge_host = args.bridge_host or bridge_host
        if not bridge_host or any(c.isspace() for c in bridge_host):
            raise SetupError('Invalid bridge address')
        if bridge_host.startswith('[') and bridge_host.endswith(']'):
            bridge_host = bridge_host[1:-1]
        print('SSH: ' + target + ' · Bridge: ' + bridge_host + ':' + str(args.port), flush=True)
        ssh = SSH(target, ssh_port)
        with tempfile.TemporaryDirectory(prefix='ego-setup-', dir='/tmp') as directory:
            if not args.skip_remote_install:
                deploy(ssh, version, args.port, Path(directory))
            if terminal and not terminal.confirm('Linux is ready. Pair and connect this Mac now?'):
                print('Installed. Pair later with: ego-bridge setup --skip-remote-install --host ' + target)
                return 0
            print('Pairing and verifying both endpoints…', flush=True)
            pair(ssh, cli, bridge_host, args.port)
        print('Connected. The menu-bar App and CLI are synchronized.')
        return 0
    except (SetupError, OSError, ValueError, subprocess.SubprocessError) as error:
        print('Setup: ' + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nSetup cancelled. Installed files and existing pairing are retained.', file=sys.stderr)
        return 130
    finally:
        if ssh:
            ssh.close()
        if terminal:
            terminal.close()


if __name__ == '__main__':
    sys.exit(main())
