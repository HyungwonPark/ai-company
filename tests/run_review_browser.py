"""A temporary read-only route server; no product state or credentials."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
import subprocess
import sys
import threading

site = Path(sys.argv[1]).resolve()
csp = re.search(r'header Content-Security-Policy "([^"]+)"', (site.parent / 'review-routes.caddy').read_text()).group(1)
# Exact deployed service-worker source, only for interception regression.
sw = Path(os.environ['REVIEW_SERVICE_WORKER']).read_bytes() if os.environ.get('REVIEW_SERVICE_WORKER') else None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        target = {'/review': site / 'index.html', '/review/': site / 'index.html', '/review/manual': site / 'manual/index.html', '/review/manual/': site / 'manual/index.html'}.get(path)
        if target:
            body = target.read_bytes()
            status, kind = 200, 'text/html; charset=utf-8'
        elif path == '/sw.js' and sw:
            body, status, kind = sw, 200, 'application/javascript'
        elif path in ('/', '/index.html'):
            body, status, kind = b'<html lang="ko"><body>Existing workspace fixture</body></html>', 200, 'text/html'
        elif path.startswith('/review/'):
            body, status, kind = b'Not found', 404, 'text/plain'
        else:
            body, status, kind = b'/* shell fixture */', 200, 'text/plain'
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Cache-Control', 'no-store')
        if target:
            self.send_header('Content-Security-Policy', csp)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.send_error(405)


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    result = subprocess.run([os.environ.get('NODE_EXECUTABLE', 'node'), 'tests/review_pages.cjs'], env={**os.environ, 'BASE_URL': f'http://127.0.0.1:{server.server_port}'}, timeout=240)
finally:
    server.shutdown()
    server.server_close()
raise SystemExit(result.returncode)
