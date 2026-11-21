"""Resolve music and application data folders on the current computer."""

from pathlib import Path
import json
import os
import sys
import tempfile

APP = 'QQMusicStudio'
MAC_CONTAINER = (
    Path('Library/Containers/com.tencent.QQMusicMac/Data/Library/Application Support/QQMusicMac')
)


def data_directory():
    override = os.environ.get('QQMUSIC_STUDIO_HOME')
    if override:
        return Path(override).expanduser().resolve()
    home = Path.home()
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA') or home / 'AppData' / 'Local') / APP
    return home / 'Library' / 'Application Support' / APP


def user_folder(name):
    if sys.platform != 'win32':
        return Path.home() / name
    # The registry contains the user's redirected Music/Documents paths.
    # Access only these two well-known non-secret shell folder values.
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
            path, _ = winreg.QueryValueEx(key, 'My Music' if name == 'Music' else 'Personal')
            return Path(os.path.expandvars(path))
    except OSError:
        return Path.home() / name


def source_suggestion():
    home = Path.home()
    music = user_folder('Music')
    if sys.platform == 'win32':
        candidates = (music / 'QQMusic' / 'VipSongsDownload', music / 'QQMusic',
                      user_folder('Documents') / 'QQMusic' / 'VipSongsDownload',
                      user_folder('Documents') / 'QQMusic', music / 'QQ音乐')
    else:
        support = home / 'Library' / 'Application Support' / 'QQMusicMac'
        candidates = (home / MAC_CONTAINER / 'iQmc', support / 'iQmc',
                      music / 'QQMusic' / 'VipSongsDownload', music / 'QQMusic', music / 'QQ音乐')
    existing = [path for path in candidates if path.is_dir()]
    for path in existing:
        try:
            if any(file.is_file() and file.suffix.lower() in ('.mflac', '.mgg', '.flac', '.ogg')
                   for file in path.iterdir()):
                return str(path)
        except OSError:
            continue
    return str(existing[0]) if existing else ''


def export_suggestion():
    return user_folder('Music') / 'QQ音乐转换'


def read_config():
    path = data_directory() / 'paths.json'
    if not path.exists():
        return {}
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def configured_paths():
    saved = read_config()
    paths = [saved.get(key) for key in ('source', 'export')]
    if all(isinstance(value, str) and value and Path(value).is_absolute() for value in paths):
        source, export = map(Path, paths)
        if source.is_dir() and export.is_dir():
            return source, export
    return None


def info():
    saved = read_config()
    return {
        'source': saved.get('source', ''),
        'export': saved.get('export', ''),
        'configured': configured_paths() is not None,
        'suggested_source': saved.get('source') or source_suggestion(),
        'suggested_export': str(export_suggestion()),
        'platform': 'windows' if sys.platform == 'win32' else 'macos',
    }


def configure(payload):
    if not isinstance(payload, dict):
        raise ValueError('请选择输入和导出目录。')
    values = [payload.get(key) for key in ('source', 'export')]
    if any(not isinstance(value, str) or not value.strip() or '\x00' in value for value in values):
        raise ValueError('请填写下载目录和导出目录。')
    source, export = (Path(value.strip()).expanduser() for value in values)
    if not source.is_absolute() or not export.is_absolute():
        raise ValueError('请输入完整目录路径。')
    if not source.is_dir():
        raise ValueError('下载目录不存在，或该路径不是文件夹。')
    source = source.resolve()
    export.mkdir(parents=True, exist_ok=True)
    export = export.resolve()
    if export == source:
        raise ValueError('请选择一个独立于下载目录的导出位置。')
    with tempfile.NamedTemporaryFile(prefix='.write-test-', dir=export):
        pass
    home = data_directory()
    home.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=home, delete=False) as handle:
        scratch = Path(handle.name)
        json.dump({'source': str(source), 'export': str(export)}, handle, ensure_ascii=False, indent=2)
    try:
        scratch.replace(home / 'paths.json')
    finally:
        scratch.unlink(missing_ok=True)
    return source, export
