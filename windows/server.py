"""Local library and conversion batch manager for QQ Music downloads."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
from urllib.parse import urlsplit
from uuid import uuid4
import webbrowser

from catalog import COPY_EXTENSIONS, is_link, scan_songs


ROOT = Path(__file__).resolve().parent
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/cover.png": ("cover.png", "image/png"),
}
MP3_QUALITIES = {"0", "2", "5", "7"}
DEFAULTS = {"input_dir": "", "output_dir": "", "format": "copy", "bitrate": "2"}
ACTIVE = {"preparing", "running"}
MAX_BATCH_FILES = 500


def now():
    return datetime.now(timezone.utc).isoformat()


def private_directory():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "qqmusic-decoder"
    return Path.home() / ".local" / "share" / "qqmusic-decoder"


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def get_song_file(root, relative):
    source = root
    for part in Path(relative).parts:
        source /= part
        if is_link(source.lstat()):
            raise ValueError("跳过符号链接或重解析点。")
    return source


def start_visible(path):
    if os.name == "nt":
        os.startfile(path)
    else:
        command = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([command, str(path)], start_new_session=True)


def choose_path(field):
    """Windows native pickers also work with the embedded Python distribution."""
    if field not in ("input_dir", "output_dir"):
        raise ValueError("未知的路径类型。")
    if os.name == "nt":
        script = (ROOT / "choose-path.ps1").read_text(encoding="utf-8")
        command = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        process = subprocess.run([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", command,
        ], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=180)
        if process.returncode:
            raise ValueError("无法打开文件选择器，请在设置中粘贴路径。")
        return process.stdout.decode("utf-8").strip() or None
    if sys.platform == "darwin":
        process = subprocess.run(["osascript", "-e", "POSIX path of (choose folder)"],
                                 capture_output=True, timeout=180)
        return process.stdout.decode("utf-8").strip() or None
    return None


def file_ready(path, extension):
    try:
        info = path.lstat()
        if is_link(info) or not stat.S_ISREG(info.st_mode) or info.st_size < 16:
            return False
        with path.open("rb") as stream:
            head = stream.read(16)
    except OSError:
        return False
    if extension == "flac":
        return head.startswith(b"fLaC")
    if extension == "ogg":
        return head.startswith(b"OggS")
    if extension == "m4a":
        return head[4:8] == b"ftyp"
    return head.startswith(b"ID3") or (head[0] == 255 and head[1] & 224 == 224)


def import_audio(source, output_dir, original, extension):
    """Publish every track atomically and preserve existing exports."""
    stem = Path(original).stem.rstrip(" .") or "track"
    temporary = output_dir / (".qqdec-" + uuid4().hex + ".tmp")
    try:
        shutil.copyfile(source, temporary)
        for i in range(1, 10001):
            suffix = "" if i == 1 else f" ({i})"
            target = output_dir / f"{stem}{suffix}.{extension}"
            if os.name == "nt":
                try:
                    temporary.rename(target)
                except FileExistsError:
                    continue
            else:
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    continue
            return target
        raise OSError("同名导出文件过多。")
    finally:
        temporary.unlink(missing_ok=True)


class Library:
    def __init__(self, directory, engine, ffmpeg):
        self.directory = directory
        self.engine = engine
        self.ffmpeg = ffmpeg
        self.process = None
        self.cancel_requested = False
        self.closing = False
        self.staging = directory / "batches"
        self.staging.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.file = directory / "state.json"
        if self.file.exists():
            with self.file.open(encoding="utf-8") as stream:
                self.data = json.load(stream)
        else:
            self.data = {"settings": {}, "seen": {}, "history": []}
        self.settings = {key: self.data["settings"].get(key, value) for key, value in DEFAULTS.items()}
        self.data["settings"] = self.settings
        self.songs = []
        self.scan_error = ""
        interrupted = False
        for batch in self.data["history"]:
            if batch["status"] in ACTIVE | {"waiting"}:
                self._finish(batch, "应用退出时此批次尚未完成，可重新选择未完成的歌曲。")
                interrupted = True
        if interrupted:
            self.save()
        self.refresh()

    def save(self):
        write_json(self.file, self.data)

    def refresh(self):
        with self.lock:
            setting = self.settings["input_dir"]
            self.scan_error = ""
            self.songs = []
            if not setting:
                return self.snapshot()
            try:
                root = Path(setting).resolve()
                songs = scan_songs(root)
            except (OSError, ValueError) as error:
                self.scan_error = str(error)
                return self.snapshot()
            key = os.path.normcase(str(root))
            previous = self.data["seen"].get(key)
            current = {song["id"]: f'{song["size"]}:{song["modified"]}' for song in songs}
            new = set(previous["new"]) if previous else set()
            if previous is not None:
                new.update(sid for sid, stamp in current.items() if previous["files"].get(sid) != stamp)
            new.intersection_update(current)
            self.data["seen"][key] = {"files": current, "new": sorted(new)}
            if previous != self.data["seen"][key]:
                self.save()
            for song in songs:
                song["is_new"] = song["id"] in new
            self.songs = songs
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            last_results = {}
            for batch in self.data["history"]:
                if batch.get("source_dir") == self.settings["input_dir"]:
                    for item in batch["items"]:
                        last_results.setdefault(item["id"], item["status"])
            songs = [dict(song, status=last_results.get(song["id"], "pending")) for song in self.songs]
            return deepcopy({
                "settings": self.settings, "songs": songs, "history": self.data["history"],
                "scan_error": self.scan_error,
            })

    def update(self, changes):
        clean = {}
        for field, value in changes.items():
            if field not in DEFAULTS or not isinstance(value, str):
                raise ValueError("设置值无效。")
            value = value.strip().strip('"')
            if field.endswith("_dir") and value:
                path = Path(value).expanduser().resolve()
                if not path.is_dir():
                    raise ValueError("请选择一个已存在的目录。")
                value = str(path)
            if field == "format" and value not in ("copy", "mp3"):
                raise ValueError("未知的输出格式。")
            if field == "bitrate" and value not in MP3_QUALITIES:
                raise ValueError("未知的 MP3 音质。")
            clean[field] = value

        with self.lock:
            current = {**self.settings, **clean}
            input_dir, output_dir = current["input_dir"], current["output_dir"]
            if input_dir and output_dir and Path(input_dir) == Path(output_dir):
                raise ValueError("下载目录和导出目录需要分别选择。")
            rescan = self.settings["input_dir"] != current["input_dir"]
            self.settings.update(clean)
            self.save()
            return self.refresh() if rescan else self.snapshot()

    def batch(self, batch_id):
        for result in self.data["history"]:
            if result["id"] == batch_id:
                return result
        raise ValueError("没有找到这个批次。")

    def active(self):
        return next((batch for batch in self.data["history"] if batch["status"] in ACTIVE), None)

    def create(self, song_ids, output_format=None, bitrate=None):
        if (
            not isinstance(song_ids, list) or not song_ids or len(song_ids) > MAX_BATCH_FILES
            or any(not isinstance(song, str) for song in song_ids)
        ):
            raise ValueError(f"请选择 1 至 {MAX_BATCH_FILES} 首歌曲。")
        with self.lock:
            if self.closing:
                raise ValueError("应用正在退出。")
            if self.active():
                raise ValueError("请先完成当前批次。")
            if not self.engine.is_file() or not self.ffmpeg.is_file():
                raise ValueError("转换组件缺失。请重新解压完整应用包后打开启动器。")
            settings = self.settings.copy()
            if not all(settings[key] for key in ("input_dir", "output_dir")):
                raise ValueError("请先选择下载目录和导出目录。")
            if output_format is not None:
                if output_format not in ("copy", "mp3"):
                    raise ValueError("未知的输出格式。")
                settings["format"] = output_format
            if bitrate is not None:
                if not isinstance(bitrate, str) or bitrate not in MP3_QUALITIES:
                    raise ValueError("未知的 MP3 音质。")
                settings["bitrate"] = bitrate
            ids = set(song_ids)
            songs = [song for song in self.songs if song["id"] in ids]
            if len(songs) != len(ids):
                raise ValueError("歌曲列表已更新，请刷新后重新选择。")
            batch_id = uuid4().hex[:12]
            batch = {
                "id": batch_id, "created_at": now(), "finished_at": "", "status": "preparing",
                "format": settings["format"], "bitrate": settings["bitrate"], "total": len(songs),
                "succeeded": 0, "failed": 0, "cancelled": 0, "cancelling": False,
                "percent": 0, "current_title": "", "error": "", "source_dir": settings["input_dir"],
                "output_dir": settings["output_dir"],
                "items": [{**song, "source": song["path"], "output": "", "status": "pending", "error": ""}
                          for song in songs],
            }
            self.cancel_requested = False
            self.settings.update(format=settings["format"], bitrate=settings["bitrate"])
            self.data["history"].insert(0, batch)
            self.save()
            threading.Thread(target=self._convert, args=(batch,), daemon=True).start()
            return self.snapshot()

    @staticmethod
    def _count(batch):
        batch["succeeded"] = sum(item["status"] == "complete" for item in batch["items"])
        batch["failed"] = sum(item["status"] == "failed" for item in batch["items"])
        batch["cancelled"] = sum(item["status"] == "cancelled" for item in batch["items"])

    def _finish(self, batch, missing="", cancelled=False):
        for item in batch["items"]:
            if item["status"] == "pending":
                item.update(status="cancelled" if cancelled else "failed",
                            error=missing or ("转换已取消。" if cancelled else "引擎没有返回转换结果。"))
        self._count(batch)
        if cancelled and batch["cancelled"]:
            status = "cancelled"
        elif batch["succeeded"] == batch["total"]:
            status = "complete"
        else:
            status = "partial" if batch["succeeded"] else "failed"
        batch.update(status=status, finished_at=now(), current_title="", cancelling=False, error=missing)

    @staticmethod
    def _request_cancel(process):
        try:
            process.stdin.write('{"cancel":true}\n')
            process.stdin.flush()
        except OSError:
            pass

    def cancel(self, batch_id):
        with self.lock:
            batch = self.active()
            if not batch or batch["id"] != batch_id:
                return self.snapshot()
            if not self.cancel_requested:
                self.cancel_requested = True
                batch["cancelling"] = True
                if self.process:
                    self._request_cancel(self.process)
            return self.snapshot()

    @staticmethod
    def _error_message(error):
        if not isinstance(error, dict):
            return "转换失败。"
        parts = [error.get("userMessage"), error.get("suggestion")]
        return " ".join(part for part in parts if isinstance(part, str)) or "转换失败。"

    def _register_output(self, batch, data, sources, scratch):
        item = sources.get(os.path.normcase(data.get("input", "")))
        if not item or item["status"] != "pending":
            return
        status = data.get("status")
        error = data.get("error") or {}
        if status != "ok":
            cancelled = error.get("code") == "ERR_CANCELLED" or status == "cancelled"
            item.update(status="cancelled" if cancelled else "failed", error=self._error_message(error))
        else:
            try:
                produced = Path(data.get("output", ""))
                output = produced.resolve(strict=True)
                if output.parent != scratch or output != produced or is_link(produced.lstat()):
                    raise ValueError("转换输出路径异常。")
                extension = output.suffix[1:].lower()
                expected = {"mp3"} if batch["format"] == "mp3" else COPY_EXTENSIONS[item["format"]]
                if extension not in expected:
                    raise ValueError(f"引擎返回了 {extension.upper()}，与本批次要求的格式不符。")
                if not file_ready(output, extension):
                    raise ValueError("输出文件的音频格式无效。")
                published = import_audio(output, Path(batch["output_dir"]), item["source"], extension)
                item.update(status="complete", output=str(published), error="")
            except (OSError, ValueError) as error:
                item.update(status="failed", error=str(error))
        self._count(batch)
        self.save()

    def _convert(self, batch):
        scratch = self.staging / batch["id"]
        process = None
        message = ""
        cancelled = False
        try:
            scratch.mkdir()
            root = Path(batch["source_dir"])
            output_dir = Path(batch["output_dir"])
            if not output_dir.is_dir():
                raise ValueError("导出目录已不可用。")
            sources = {}
            for item in batch["items"]:
                with self.lock:
                    if self.cancel_requested:
                        break
                try:
                    source = get_song_file(root, item["source"])
                    info = source.stat()
                    modified = datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()
                    if not stat.S_ISREG(info.st_mode) or info.st_size != item["size"] or modified != item["modified"]:
                        raise ValueError("下载文件已变化，请刷新后重试。")
                    sources[os.path.normcase(str(source))] = item
                except (OSError, ValueError) as error:
                    with self.lock:
                        item.update(status="failed", error=str(error))
            with self.lock:
                cancelled = self.cancel_requested
                self._count(batch)
                if cancelled or not sources:
                    return
                batch["status"] = "running"
                self.save()
            tracks = list(sources.values())
            request = {
                "paths": [str(root / item["source"]) for item in tracks],
                "outputDir": str(scratch), "outputFormat": batch["format"],
                "mp3Quality": int(batch["bitrate"]), "concurrency": 2,
            }
            process = subprocess.Popen(
                [str(self.engine), "--ffmpeg", str(self.ffmpeg)], cwd=ROOT,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            process.stdin.write(json.dumps(request, ensure_ascii=True) + "\n")
            process.stdin.flush()
            with self.lock:
                self.process = process
                if self.cancel_requested:
                    self._request_cancel(process)
            terminal = False
            for line in process.stdout:
                event = json.loads(line)
                kind, data = event["type"], event["data"]
                with self.lock:
                    if kind == "progress":
                        current = data.get("current", 0)
                        if isinstance(current, int) and 0 < current <= len(tracks):
                            batch["current_title"] = tracks[current - 1]["title"]
                        percent = data.get("percent")
                        if isinstance(percent, int) and 0 <= percent <= 100:
                            batch["percent"] = max(batch["percent"], percent)
                    elif kind == "file":
                        self._register_output(batch, data, sources, scratch)
                    elif kind == "complete":
                        for entry in data.get("results", []):
                            self._register_output(batch, entry, sources, scratch)
                        cancelled = bool(data.get("cancelled"))
                        terminal = True
                    elif kind == "error":
                        message = self._error_message(data)
                        terminal = True
            exit_code = process.wait(timeout=5)
            if not terminal or (exit_code and not message):
                message = "转换引擎异常退出，请重试未处理的歌曲。"
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            message = "无法完成转换，请检查下载和导出目录，或重新启动应用后重试。"
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdin.close()
                process.stdout.close()
            with self.lock:
                self.process = None
                self._finish(batch, message, cancelled=cancelled or self.cancel_requested)
                self.save()
            shutil.rmtree(scratch, ignore_errors=True)

    def monitor(self, stopped):
        while not stopped.wait(12):
            try:
                self.refresh()
            except (OSError, ValueError) as error:
                print(f"Background refresh: {error}", file=sys.stderr)

    def open_folder(self, batch_id, kind):
        with self.lock:
            if kind not in ("input_dir", "output_dir"):
                raise ValueError("未知的目录类型。")
            value = self.batch(batch_id)["output_dir"] if batch_id and kind == "output_dir" else self.settings[kind]
            if not value or not Path(value).is_dir():
                raise ValueError("目录尚未设置或当前不可用。")
            start_visible(Path(value))
            return {"ok": True}


class LocalHandler(BaseHTTPRequestHandler):
    def log_message(self, template, *args):
        if self.server.verbose:
            super().log_message(template, *args)

    def reply(self, status, data, kind="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(data)

    def json(self, status, obj):
        self.reply(status, json.dumps(obj, ensure_ascii=True).encode("utf-8"))

    def valid(self, api):
        host = self.headers.get("Host")
        origin = self.headers.get("Origin")
        if host != self.server.authority or origin not in (None, f"http://{host}"):
            return False
        if api and self.headers.get("X-QQMusic-Request") != "1":
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self.valid(path.startswith("/api/")):
            self.json(403, {"error": "仅允许当前本地页面发起请求。"})
        elif path == "/api/state":
            self.json(200, self.server.library.snapshot())
        elif path in ASSETS:
            asset, kind = ASSETS[path]
            self.reply(200, (ROOT / "web" / asset).read_bytes(), kind)
        else:
            self.json(404, {"error": "页面不存在。"})

    def do_POST(self):
        if not self.valid(True):
            self.json(403, {"error": "仅允许当前本地页面发起请求。"})
            return
        try:
            if self.headers.get_content_type() != "application/json":
                raise ValueError("需要 JSON 请求。")
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 2_000_000:
                raise ValueError("请求大小超出范围。")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("请求格式无效。")
            result = self.route(urlsplit(self.path).path, body)
            if result is None:
                self.json(404, {"error": "操作不存在。"})
            else:
                self.json(200, result)
        except (ValueError, OSError, UnicodeError, subprocess.SubprocessError) as error:
            self.json(400, {"error": str(error)})

    def route(self, path, body):
        app = self.server.library
        if app.closing:
            raise ValueError("应用正在退出。")
        batch_id = body.get("batch_id")
        operations = {
            "/api/settings": lambda: app.update(body),
            "/api/scan": app.refresh,
            "/api/pick": lambda: {"path": choose_path(body.get("field"))},
            "/api/batches": lambda: app.create(body.get("song_ids"), body.get("format"), body.get("bitrate")),
            "/api/batches/cancel": lambda: app.cancel(batch_id),
            "/api/exit": self.exit_app,
            "/api/folder/open": lambda: app.open_folder(batch_id, body.get("kind")),
        }
        operation = operations.get(path)
        return operation() if operation else None

    def exit_app(self):
        with self.server.library.lock:
            if self.server.library.active():
                raise ValueError("转换任务仍在运行，请先完成或取消。")
            self.server.library.closing = True
        threading.Thread(target=self.server.shutdown, daemon=True).start()
        return {"ok": True}


@contextmanager
def exclusive(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "running.lock").open("a+b") as stream:
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            from msvcrt import locking, LK_NBLCK
            locking(stream.fileno(), LK_NBLCK, 1)
        else:
            from fcntl import flock, LOCK_EX, LOCK_NB
            flock(stream, LOCK_EX | LOCK_NB)
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=private_directory())
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--engine", type=Path, default=ROOT / "engine" / "qqmusic-decoder-engine.exe")
    parser.add_argument("--ffmpeg", type=Path, default=ROOT / "engine" / "ffmpeg.exe")
    options = parser.parse_args()
    data = options.data_dir.resolve()
    bookmark = data / "running-url"
    try:
        with exclusive(data):
            app = Library(data, options.engine.resolve(), options.ffmpeg.resolve())
            with ThreadingHTTPServer(("127.0.0.1", options.port), LocalHandler) as server:
                server.authority = f"127.0.0.1:{server.server_port}"
                server.library = app
                server.verbose = options.verbose
                url = f"http://{server.authority}/"
                bookmark.write_text(url, encoding="ascii")
                stopped = threading.Event()
                threading.Thread(target=app.monitor, args=(stopped,), daemon=True).start()
                print(f"QQ Music Decoder: {url}", flush=True)
                try:
                    if not options.no_browser and not webbrowser.open(url):
                        raise ValueError("Could not open the default browser.")
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
                finally:
                    stopped.set()
                    bookmark.unlink(missing_ok=True)
    except (BlockingIOError, PermissionError) as error:
        if bookmark.is_file():
            url = bookmark.read_text(encoding="ascii")
            if url.startswith("http://127.0.0.1:"):
                print(f"QQ Music Decoder: {url}", flush=True)
                if not options.no_browser and not webbrowser.open(url):
                    raise ValueError("Could not open the default browser.")
                return
        raise error


if __name__ == "__main__":
    main()
