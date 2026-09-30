"""Exercise the generated Caddy route in a network-isolated disposable container."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

candidate = Path(sys.argv[1]).resolve()
name = 'review-route-test-' + uuid.uuid4().hex[:10]
image = os.environ.get('CADDY_TEST_IMAGE', 'caddy:2.10.2-alpine')
checks = []


def request(method, path):
    query = f'{method} {path} HTTP/1.1\\r\\nHost: 127.0.0.1:8080\\r\\nConnection: close\\r\\nContent-Length: 0\\r\\n\\r\\n'
    result = subprocess.run(['docker', 'exec', name, 'sh', '-c', "printf '" + query + "' | nc -w 2 127.0.0.1 8080"], capture_output=True, check=True)
    header, body = result.stdout.split(b'\r\n\r\n', 1)
    status = int(header.split()[1])
    headers = dict(line.decode().split(': ', 1) for line in header.split(b'\r\n')[1:] if b': ' in line)
    return status, headers, body


with tempfile.TemporaryDirectory(prefix='review-caddy-') as temp:
    config = Path(temp) / 'Caddyfile'
    config.write_text('{\n admin off\n auto_https off\n}\nhttp://127.0.0.1:8080 {\n' + (candidate / 'review-routes.caddy').read_text() + '\nhandle {\n respond "existing-route-fixture" 202\n}\n}\n')
    try:
        subprocess.run(['docker', 'run', '-d', '--name', name, '--network', 'none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges:true', '--tmpfs', '/tmp:rw,exec,mode=1777', '--tmpfs', '/config', '--tmpfs', '/data', '-v', str(candidate / 'site') + ':/data/review-documents/current:ro', '-v', str(config) + ':/etc/caddy/Caddyfile:ro', image, 'sh', '-c', 'cp /usr/bin/caddy /tmp/caddy && exec /tmp/caddy run --config /etc/caddy/Caddyfile'], check=True, stdout=subprocess.DEVNULL)
        for attempt in range(30):
            try:
                request('GET', '/review')
                break
            except Exception:
                if attempt == 29:
                    raise
                time.sleep(.1)
        for path, document in [('/review', 'index.html'), ('/review/', 'index.html'), ('/review/manual', 'manual/index.html'), ('/review/manual/', 'manual/index.html')]:
            status, headers, body = request('GET', path)
            assert status == 200, (path, status)
            assert body == (candidate / 'site' / document).read_bytes(), path
            assert headers.get('Cache-Control') == 'no-store'
            assert "default-src 'none'" in headers.get('Content-Security-Policy', '')
            assert 'sha256-' in headers['Content-Security-Policy']
            status, _, body = request('HEAD', path)
            assert status == 200 and not body
            checks.append({'path': path, 'get': 200, 'head': 200, 'content_sha256': hashlib.sha256((candidate / 'site' / document).read_bytes()).hexdigest()})
        for path in ['/review/not-published', '/review/manifest.json', '/review/.git/config', '/review/manual/index.html']:
            status, _, _ = request('GET', path)
            assert status == 404, (path, status)
            checks.append({'path': path, 'status': status})
        for method in ['POST', 'PUT', 'DELETE']:
            status, _, _ = request(method, '/review')
            assert status == 405
            checks.append({'method': method, 'status': status})
        for path in ['/', '/api/session', '/.well-known/assetlinks.json', '/reviewer']:
            status, _, body = request('GET', path)
            assert status == 202 and b'existing-route-fixture' in body
            checks.append({'existing_path': path, 'retained': True})
        print(json.dumps({'status': 'PASS', 'network': 'none', 'read_only': True, 'checks': checks}, ensure_ascii=False))
    except Exception:
        print(subprocess.run(['docker', 'logs', name], capture_output=True, text=True).stderr, file=sys.stderr)
        raise
    finally:
        subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
