#!/usr/bin/env python3
"""Opt-in real ego-browser probe; local echo only, no profile changes."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--space', type=int, required=True, help='Existing Agent-owned validation space')
    ap.add_argument('--output', default='/tmp/ego-extra-headers-results.json')
    args = ap.parse_args()
    records = []

    class Echo(BaseHTTPRequestHandler):
        def do_GET(self):
            headers = {key.lower(): value for key, value in self.headers.items()
                       if key.lower().startswith('x-bridge-')}
            records.append({'path': self.path, 'headers': headers})
            data = json.dumps({'path': self.path, 'headers': headers}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html' if self.path.startswith('/page') else 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            if self.path.startswith('/page'):
                self.wfile.write(b'<title>Extra headers validation</title><p>Local header probe</p>')
            else:
                self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Echo)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = 'http://127.0.0.1:' + str(server.server_port)
    prefix = 'const task=await taskSpace(' + str(args.space) + ');const page=task.page("p1");'
    def run(script):
        result = subprocess.run(['ego-browser', 'nodejs', '-e', prefix + script],
                                capture_output=True, text=True, timeout=25)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return (result.stdout + result.stderr).strip()
    try:
        same_round = run('''
await page.cdp("Network.enable");
await page.cdp("Network.setExtraHTTPHeaders",{headers:{"X-Bridge-Environment":"preview-demo"}});
await page.goto(ORIGIN+"/page-first");
console.log("probe="+JSON.stringify(await page.fetch("/fetch-first")));
await page.reload();
const next=await task.newPage();
await next.goto(ORIGIN+"/page-new");
'''.replace('ORIGIN', json.dumps(origin)))
        next_round = run('''
await page.goto(ORIGIN+"/page-next-round");
console.log("probe="+JSON.stringify(await page.fetch("/fetch-next-round")));
'''.replace('ORIGIN', json.dumps(origin)))
        reapplied = run('''
await page.cdp("Network.enable");
await page.cdp("Network.setExtraHTTPHeaders",{headers:{"X-Bridge-Environment":"preview-demo"}});
await page.goto(ORIGIN+"/page-reapplied");
await page.cdp("Network.setExtraHTTPHeaders",{headers:{}});
await page.goto(ORIGIN+"/page-cleared");
'''.replace('ORIGIN', json.dumps(origin)))
        def got(path):
            matches = [rec for rec in records if rec['path'] == path]
            return bool(matches) and matches[-1]['headers'].get('x-bridge-environment') == 'preview-demo'
        version = subprocess.run(['ego-browser', '--version'], capture_output=True, text=True)
        result = {'cliVersion': (version.stdout + version.stderr).strip(),
                  'sameRoundNavigation': got('/page-first'),
                  'sameRoundFetch': got('/fetch-first'), 'newPageInherited': got('/page-new'),
                  'nextRoundNavigation': got('/page-next-round'),
                  'nextRoundFetch': got('/fetch-next-round'),
                  'reapplied': got('/page-reapplied'), 'cleared': not got('/page-cleared'),
                  'records': records}
        Path(args.output).write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
