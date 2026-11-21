"""Resolve FFmpeg and install the pinned upstream executable on first conversion."""

from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
import platform
import shutil
import ssl
import stat
import sys
import sysconfig
import tempfile
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, HTTPSHandler
from zipfile import BadZipFile, ZipFile

from fetch_dependencies import ARTIFACTS, verify_file
from portable_lock import exclusive
from portable_paths import data_directory


class FFmpegError(Exception):
    """An FFmpeg dependency could not be installed safely."""


@dataclass(frozen=True)
class _Wheel:
    asset: str
    executable: str
    size: int
    sha256: str


# Executable hashes and sizes were computed from independently pinned wheels.
_WHEELS = {
    ('darwin', 'arm64'): _Wheel('ffmpeg-mac-arm64.whl', 'ffmpeg-macos-aarch64-v9.0', 52070376,
                               '591260c945d0eef150e3bf82b0ef988bd36a9cecc18ff05d6679617159f0a95e'),
    ('darwin', 'x86_64'): _Wheel('ffmpeg-mac-x64.whl', 'ffmpeg-macos-x86_64-v9.0.2', 80827688,
                                'a45b462cf91ed89148ae218c4577e30896485d7a6792c3673bcf5f823fa01b63'),
    ('win32', 'amd64'): _Wheel('ffmpeg-win-x64.whl', 'ffmpeg-win-x86_64-v9.0.2.exe', 105423872,
                             '3256173f3f8bffd7df12227c68adf68025edb1832273a9530688a7bb1ed8edec'),
    ('win32', 'arm64'): _Wheel('ffmpeg-win-arm64.whl', 'ffmpeg-win-aarch64-v9.0.1.exe', 78507008,
                             'a169b9d26b2380be66211022525c9ac16affc923ba05b561024e57c5ed4281f9'),
}
_MAX_WHEEL_SIZE = 64 << 20
_VERIFIED_CACHE = {}


def _host_wheel():
    arch = (sysconfig.get_platform().removeprefix('win-') if sys.platform == 'win32'
            else platform.machine()).lower()
    wheel = _WHEELS.get((sys.platform, arch))
    if wheel is None:
        raise FFmpegError('当前系统或处理器架构没有对应的 FFmpeg 下载。')
    return wheel


def _cached_path(wheel):
    filename = 'ffmpeg.exe' if wheel.executable.endswith('.exe') else 'ffmpeg'
    return data_directory() / 'runtime' / wheel.sha256 / filename


def _stamp(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Expected a regular executable')
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _ready(path, wheel):
    try:
        stamp = _stamp(path)
        if stamp[3] != wheel.size or (sys.platform != 'win32' and not stamp[2] & stat.S_IXUSR):
            return False
        if _VERIFIED_CACHE.get(path) != stamp:
            verify_file(path, wheel.sha256)
            if _stamp(path) != stamp:
                return False
            _VERIFIED_CACHE[path] = stamp
        return True
    except (OSError, ValueError):
        return False


def resolve_ffmpeg(bundled: Path) -> Path:
    """Find a bundled or cached executable without making a network request."""
    if not bundled.is_file():
        try:
            wheel = _host_wheel()
        except FFmpegError:
            return bundled
        cached = _cached_path(wheel)
        if _ready(cached, wheel):
            return cached
    return bundled


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _download_wheel(url, target):
    address = urlsplit(url)
    if address.scheme != 'https' or address.netloc != 'files.pythonhosted.org':
        raise FFmpegError('FFmpeg 下载地址不符合安全要求。')
    system_certs = Path('/etc/ssl/cert.pem')
    context = ssl.create_default_context(cafile=str(system_certs)
                                         if sys.platform == 'darwin' and system_certs.is_file() else None)
    opener = build_opener(_NoRedirect(), HTTPSHandler(context=context))
    deadline = time.monotonic() + 180
    downloaded = 0
    with opener.open(url, timeout=30) as response, target.open('wb') as output:
        for block in iter(lambda: response.read(1 << 20), b''):
            downloaded += len(block)
            if downloaded > _MAX_WHEEL_SIZE or time.monotonic() >= deadline:
                raise FFmpegError('FFmpeg 下载超出限制，请检查网络后重试。')
            output.write(block)


def _download_verified(wheel, target):
    url, sha256 = ARTIFACTS[wheel.asset]
    for _ in range(2):
        try:
            _download_wheel(url, target)
            verify_file(target, sha256)
        except (URLError, HTTPException, ConnectionError, TimeoutError, ValueError):
            continue
        return
    raise FFmpegError('首次转换需要从 PyPI 获取 FFmpeg。下载或校验失败，请检查网络后重试。')


def _install(wheel, cached):
    with tempfile.TemporaryDirectory(prefix='.ffmpeg-', dir=cached.parent) as temporary:
        folder = Path(temporary)
        archive_path = folder / 'ffmpeg.whl'
        _download_verified(wheel, archive_path)
        pending = folder / cached.name
        with ZipFile(archive_path) as archive:
            member = archive.getinfo(f'imageio_ffmpeg/binaries/{wheel.executable}')
            if member.file_size != wheel.size:
                raise ValueError('Unexpected executable size')
            with archive.open(member) as source, pending.open('wb') as target:
                shutil.copyfileobj(source, target)
        verify_file(pending, wheel.sha256)
        pending.chmod(0o700)
        pending.replace(cached)
        _VERIFIED_CACHE[cached] = _stamp(cached)


def ensure_ffmpeg(bundled: Path) -> Path:
    """Find FFmpeg, downloading a verified upstream wheel on first use if needed."""
    if bundled.is_file():
        return bundled
    wheel = _host_wheel()
    cached = _cached_path(wheel)
    if _ready(cached, wheel):
        return cached
    try:
        with exclusive(cached.with_suffix('.lock')):
            if not _ready(cached, wheel):
                _install(wheel, cached)
    except BlockingIOError:
        raise FFmpegError('另一个窗口正在安装 FFmpeg，请稍后再试。') from None
    except (BadZipFile, ValueError, KeyError):
        raise FFmpegError('FFmpeg 文件内容未通过校验，请重试。') from None
    except OSError:
        raise FFmpegError('无法下载或写入 FFmpeg，请检查网络、磁盘空间和目录权限后重试。') from None
    return cached
