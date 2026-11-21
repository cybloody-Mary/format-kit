"""Single-user, loopback-only UI for the portable Windows edition."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import base64
import json
import os
import secrets
import subprocess
import sys
import threading
import webbrowser

import studio

WEB = Path(__file__).resolve().parent / 'web'
STATIC = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/style.css': ('style.css', 'text/css; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
}


def pick_folder(kind):
    if kind not in ('source', 'export'):
        raise studio.StudioError('请选择目录类型。')
    if sys.platform != 'win32':
        return ''
    title = '请选择 QQ 音乐下载目录' if kind == 'source' else '请选择导出目录'
    script = (
        'Add-Type -AssemblyName System.Windows.Forms\n'
        f'$dialog = New-Object System.Windows.Forms.FolderBrowserDialog\n$dialog.Description = "{title}"\n'
        '$dialog.ShowNewFolderButton = $true\n'
        'if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {\n'
        '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n'
        '[Console]::Write($dialog.SelectedPath)\n'
        '}\n'
    )
    shell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run(
        [str(shell), '-NoProfile', '-STA', '-EncodedCommand',
         base64.b64encode(script.encode('utf-16le')).decode('ascii')],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=600, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise studio.StudioError('无法打开文件夹选择器，你可以填入完整路径。')
    return result.stdout.lstrip('\ufeff').strip()


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(('127.0.0.1', 0), Handler)
        self.token = secrets.token_urlsafe(32)
        self.jobs = {}
        self.guard = threading.Lock()

    def active(self):
        return any(job['running'] for job in self.jobs.values())

    def start_job(self, payload):
        settings = studio.local_settings()
        if settings is None:
            raise studio.StudioError('请先选择 QQ 音乐下载目录和导出目录。')
        if not isinstance(payload, dict):
            raise studio.StudioError('无效的转换批次。')
        payload = dict(payload, allow_qq_login=False)
        with self.guard:
            if self.active():
                raise studio.StudioError('已有一个批次正在转换。')
            self.jobs.clear()
            job_id = secrets.token_hex(16)
            self.jobs[job_id] = {'events': [], 'running': True}

        def emit(event):
            with self.guard:
                self.jobs[job_id]['events'].append(event)

        def worker():
            try:
                studio.convert(settings, payload, emit)
            except studio.StudioError as error:
                emit({'event': 'error', 'message': str(error)})
            except Exception:
                emit({'event': 'error', 'message': '无法完成转换。请检查目录权限与磁盘空间。'})
            finally:
                # Conversion keys are never stored in the job history.
                payload.clear()
                with self.guard:
                    self.jobs[job_id]['running'] = False

        threading.Thread(target=worker, daemon=True).start()
        return {'job': job_id}


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, *args):
        pass

    def send_data(self, code, data, content_type):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy',
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; "
                         "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(data)

    def json_response(self, status, value):
        self.send_data(status, json.dumps(value, ensure_ascii=False).encode('utf-8'),
                       'application/json; charset=utf-8')

    def valid_host(self):
        return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

    def authorized(self):
        provided = self.headers.get('Authorization', '')
        return self.valid_host() and secrets.compare_digest(
            provided.encode('utf-8'), ('Bearer ' + self.server.token).encode('ascii'))

    def fail(self, status, message):
        self.json_response(status, {'error': message, 'message': message})

    def do_GET(self):
        address = urlsplit(self.path)
        path = address.path
        if path in STATIC:
            if not self.valid_host():
                return self.fail(403, '请在本机浏览器打开应用。')
            file, mime = STATIC[path]
            return self.send_data(200, (WEB / file).read_bytes(), mime)
        if not self.authorized():
            return self.fail(403, '会话已过期，请重新打开应用。')
        try:
            if path == '/api/inventory':
                return self.json_response(200, studio.inventory(studio.local_settings()))
            if path.startswith('/api/jobs/'):
                job_id = path.rsplit('/', 1)[-1]
                after = max(0, int(parse_qs(address.query).get('after', ['0'])[0]))
                with self.server.guard:
                    job = self.server.jobs.get(job_id)
                    if job is not None:
                        result = {'events': job['events'][after:], 'next': len(job['events']),
                                  'running': job['running']}
                if job is None:
                    return self.fail(404, '找不到这个转换批次。')
                return self.json_response(200, result)
        except studio.StudioError as error:
            return self.fail(400, str(error))
        except (OSError, ValueError):
            return self.fail(400, '无法读取台账或下载目录，请打开目录设置检查。')
        return self.fail(404, '找不到此操作。')

    def read_json(self):
        size = int(self.headers.get('Content-Length', '0'))
        if size < 0 or size > 8_000_000 or 'application/json' not in self.headers.get('Content-Type', ''):
            raise ValueError('Invalid payload')
        data = json.loads(self.rfile.read(size))
        if not isinstance(data, dict):
            raise ValueError('Invalid payload')
        return data

    def do_POST(self):
        if not self.authorized():
            return self.fail(403, '会话已过期，请重新打开应用。')
        try:
            payload = self.read_json()
            path = urlsplit(self.path).path
            if path == '/api/configure':
                with self.server.guard:
                    if self.server.active():
                        return self.fail(409, '请等本批结束后再修改目录。')
                    inventory = studio.config_inventory(payload)
                return self.json_response(200, inventory)
            if path == '/api/pick':
                return self.json_response(200, {'path': pick_folder(payload.get('kind'))})
            if path == '/api/convert':
                return self.json_response(200, self.server.start_job(payload))
            if path == '/api/stop':
                with self.server.guard:
                    if self.server.active():
                        return self.fail(409, '请等转换完成后再退出。')
                self.json_response(200, {'ok': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
        except studio.StudioError as error:
            return self.fail(400, str(error))
        except (OSError, ValueError, subprocess.SubprocessError):
            return self.fail(400, '无法完成操作，请检查目录和输入内容。')
        return self.fail(404, '找不到此操作。')


if __name__ == '__main__':
    with Server() as server:
        url = f'http://127.0.0.1:{server.server_port}/#token={server.token}'
        if '--no-browser' in sys.argv:
            print(url, flush=True)
        else:
            threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
