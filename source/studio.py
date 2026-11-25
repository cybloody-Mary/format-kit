"""Local backend for Format Kit."""

import argparse
from array import array
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, replace
import errno
import shutil
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import ssl
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import urllib.request

BASE = Path(__file__).resolve().parent
import ffmpeg_runtime
import ledger
from tunefree_backend import audio, decrypt, ekey, id3, tags, verify
import library_metadata
import portable_paths
from portable_lock import exclusive

MODES = ('tunefree', 'qm', 'qmc')
CONVERTIBLE = {'pending', 'failed', 'repair', 'original_ready'}


class StudioError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    source: Path
    export: Path
    state: Path
    report: Path
    ffmpeg: Path

    def __post_init__(self):
        for attr in ('source', 'export', 'state', 'report', 'ffmpeg'):
            path = Path(getattr(self, attr)).expanduser()
            normalized = path.parent.resolve() / path.name
            object.__setattr__(self, attr, normalized)


def local_settings():
    paths = portable_paths.configured_paths()
    if paths is None:
        return None
    source, export = paths
    ffmpeg = BASE / ('ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg')
    return Settings(source, export, export / '.format-kit-ledger.json',
                    export / 'Format Kit 转码清单.txt', ffmpeg)


def config_inventory(payload):
    try:
        portable_paths.configure(payload)
    except (ValueError, OSError) as exc:
        if isinstance(exc, ValueError):
            raise StudioError(str(exc)) from None
        raise StudioError('无法读取下载目录或写入导出目录，请核对路径和访问权限。') from None
    return inventory(local_settings())


def scan(settings):
    args = SimpleNamespace(source=settings.source, export=settings.export, state=settings.state,
                           report=settings.report, action='refresh')
    try:
        rows, _ = ledger.refresh(args)
    except PermissionError:
        raise StudioError('请允许应用访问 QQ 音乐的文件，或通过系统设置检查应用数据访问权限。') from None
    except FileNotFoundError:
        raise StudioError('找不到 QQ 音乐下载目录，请先用 QQ 音乐下载歌曲。') from None
    return rows


def inventory(settings):
    if settings is None:
        return {'export_dir': '', 'report': '', 'paths': portable_paths.info(), 'rows': [],
                'summary': dict.fromkeys((*ledger.STATUS, 'duplicates', 'historical'), 0)}
    rows = scan(settings)
    counts = Counter(row['status'] for row in rows)
    summary = {key: counts[key] for key in ledger.STATUS}
    summary['duplicates'] = sum(len(row['copies']) for row in rows)
    summary['historical'] = max(0, len(ledger.index_audio(settings.export / 'MP3', {'.mp3'}))
                                - counts['success'] - counts['repair'])
    return {
        'export_dir': str(settings.export), 'report': str(settings.report), 'summary': summary,
        'paths': {**portable_paths.info(), 'source': str(settings.source),
                  'export': str(settings.export), 'configured': True},
        'rows': [dict(row, id=row.get('resource', row['source']),
                      name=Path(row['source']).name, needs_login=row.get('needs_login', True))
                 for row in library_metadata.enrich(settings.source, rows)],
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def read_credentials():
    # Read the installed QQ Music client's own preference file only after the
    # user confirmed the selected batch. Tokens stay inside this process.
    if sys.platform != 'darwin':
        raise StudioError('Windows 上请为这首新版加密文件填入对应文件密钥。')
    credentials = next((value for path in (ekey.CONTAINER_PREFS, ekey.PREFS_PATH)
                        if (value := ekey.read_credentials_from_plist(path)) is not None), None)
    if credentials is None:
        raise StudioError('读取不到 QQ 音乐的登录状态。请确认 QQ 音乐已经登录并允许访问其 App 数据。')
    return credentials


def restore_audio(source, song_key=None, *, allow_key_fetch=False):
    with source.open('rb') as handle:
        magic = handle.read(4)
    if magic in (b'fLaC', b'OggS'):
        return source.read_bytes()
    try:
        plain, _ = decrypt.decrypt_bytes(str(source), ekey=song_key, auto_ekey=allow_key_fetch)
    except decrypt.DecryptError as exc:
        if exc.reason == 'ekey':
            raise StudioError('QQ 音乐未提供解码密钥，请检查登录状态或稍后重试。') from None
        raise StudioError('无法恢复音频，请检查源文件是否完整。') from None
    return plain


def ffmpeg_run(ffmpeg, *arguments, progress=False):
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
    return subprocess.run(
        [str(ffmpeg), '-hide_banner', '-nostdin', '-v', 'error',
         *(['-progress', 'pipe:1'] if progress else []), *map(str, arguments)],
        check=True, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=150, **options,
    ).stdout


def decoded_duration(ffmpeg, path):
    output = ffmpeg_run(ffmpeg, '-xerror', '-err_detect', 'explode', '-i', path,
                        '-map', '0:a:0', '-f', 'null', '-', progress=True)
    durations = re.findall(r'^out_time_us=(\d+)$', output, re.M)
    if not durations or not int(durations[-1]):
        raise StudioError('音频解码未得到有效时长。')
    return int(durations[-1]) / 1_000_000


def streaminfo(path):
    with path.open('rb') as handle:
        header = handle.read(10)
        offset = 0
        if header.startswith(b'ID3') and len(header) == 10:
            offset = 10 + (10 if header[5] & 16 else 0)
            offset += sum((value & 127) << (7 * (3 - index))
                          for index, value in enumerate(header[6:10]))
        handle.seek(offset)
        info = handle.read(42)
    if not (len(info) == 42 and info[:4] == b'fLaC' and not info[4] & 127
            and info[5:8] == b'\0\0\x22'):
        raise StudioError('FLAC 音频头未通过校验。')
    fields = struct.unpack_from('>Q', info, 18)[0]
    rate, depth, samples = fields >> 44, ((fields >> 36) & 31) + 1, fields & 0xFFFFFFFFF
    if not rate:
        raise StudioError('FLAC 没有有效采样率。')
    return depth, info[26:42].hex(), samples / rate


def unusual_depth_checksum(ffmpeg, path, depth):
    # FFmpeg stores an audio sample in the high bits of a 32-bit PCM word.
    # FLAC's checksum uses the original sample, sign-extended to whole bytes.
    temporary = path.with_name('.checksum-pcm')
    try:
        ffmpeg_run(ffmpeg, '-y', '-xerror', '-err_detect', 'explode', '-i', path,
                   '-map', '0:a:0', '-c:a', 'pcm_s32le', '-f', 's32le', temporary)
        digest = hashlib.md5()
        width = (depth + 7) // 8
        with temporary.open('rb') as handle:
            for block in iter(lambda: handle.read(1 << 18), b''):
                words = array('i')
                words.frombytes(block)
                if sys.byteorder != 'little':
                    words.byteswap()
                digest.update(b''.join((word >> (32 - depth)).to_bytes(width, 'little', signed=True)
                                       for word in words))
        return digest.hexdigest()
    finally:
        temporary.unlink(missing_ok=True)


def validate_original(ffmpeg, path):
    if path.suffix == '.ogg':
        if not verify.verify_file(str(path), check_crc=True)['ok']:
            raise StudioError('OGG 分页校验失败，源文件可能不完整。')
        return decoded_duration(ffmpeg, path)
    depth, expected, duration = streaminfo(path)
    if not int(expected, 16) or not duration:
        return decoded_duration(ffmpeg, path)
    if depth in (8, 16, 24, 32):
        pcm = f'pcm_s{depth}' + ('le' if depth > 8 else '')
        result = ffmpeg_run(ffmpeg, '-xerror', '-err_detect', 'explode', '-i', path,
                            '-map', '0:a:0', '-c:a', pcm, '-f', 'hash', '-hash', 'MD5', '-')
        actual = result.strip().removeprefix('MD5=')
    else:
        actual = unusual_depth_checksum(ffmpeg, path, depth)
    if actual != expected:
        raise StudioError('FLAC 解码后的采样与源文件校验值不一致。')
    return duration


def restore_and_validate(ffmpeg, source, original, song_key=None, *, allow_key_fetch=False):
    normal = restore_audio(source, song_key, allow_key_fetch=allow_key_fetch)
    original.write_bytes(normal)
    try:
        return validate_original(ffmpeg, original)
    except (StudioError, OSError, subprocess.SubprocessError) as first:
        if original.suffix != '.flac':
            raise StudioError('恢复后的 OGG 未通过音频校验。') from first
        # Some downloads have trailing bytes. Try the full restored FLAC and
        # accept only a boundary whose decoded PCM matches the stored checksum.
        old_trim = decrypt.trim.audio_end
        try:
            decrypt.trim.audio_end = lambda *_: None
            raw = restore_audio(source, song_key, allow_key_fetch=allow_key_fetch)
        finally:
            decrypt.trim.audio_end = old_trim
        extra = len(raw) - len(normal)
        if 0 < extra <= 64 and raw.startswith(normal):
            for trim_count in range(extra):
                original.write_bytes(raw[:len(raw) - trim_count])
                try:
                    return validate_original(ffmpeg, original)
                except (StudioError, OSError, subprocess.SubprocessError):
                    continue
        raise StudioError('恢复后的 FLAC 未通过原音校验。') from first


def encode_mp3(ffmpeg, source, output, mode, duration, display_name):
    info = tags.read_source(source)
    metadata = audio.merge_tags(info.tags, display_name)
    bitrate = ('-b:a', '320k') if mode == 'tunefree' else ('-q:a', '2')
    tags_args = audio._metadata_args(metadata, audio._ID3_KEYS)
    arguments = ['-y', '-xerror', '-i', source, '-map', '0:a:0']
    options = ['-c:a', 'libmp3lame', *bitrate, '-map_metadata', '-1', '-id3v2_version', '3',
               *tags_args, output]
    try:
        ffmpeg_run(ffmpeg, *arguments, '-map', '0:v?', '-c:v', 'copy', *options)
    except subprocess.CalledProcessError:
        ffmpeg_run(ffmpeg, *arguments, *options)
    if metadata.get('lyrics'):
        id3.add_lyrics(str(output), metadata['lyrics'])
    if abs(decoded_duration(ffmpeg, output) - duration) > 0.15:
        raise StudioError('MP3 与原音的解码时长不一致。')
    if not ledger.probe_mp3(output):
        raise StudioError('生成的 MP3 文件无法通过格式检查。')


@contextmanager
def locked_state(settings):
    settings.state.parent.mkdir(parents=True, exist_ok=True)
    with exclusive(settings.state.with_suffix('.lock')):
        state = (json.loads(settings.state.read_text(encoding='utf-8'))
                 if settings.state.exists() else ledger.fresh_state())
        yield state


def save_state(settings, state):
    ledger.save_text(settings.state, json.dumps(state, ensure_ascii=False, indent=2))


def make_output_dir(path, expected):
    if path.parent != expected or expected.is_symlink():
        raise StudioError('导出目录发生变化，请重新打开平台。')
    expected.mkdir(parents=True, exist_ok=True)
    if expected.is_symlink() or path.is_symlink():
        raise StudioError('导出路径包含符号链接，请核对音乐目录。')


def known_output(track, mp3, original):
    original_only = track.get('original_only', {})
    if original_only.get('name') == original.name:
        origin_signature = original_only['signature']
    elif track.get('original') == original.name:
        origin_signature = track['signatures'][1]
    else:
        origin_signature = None
    mp3_signature = track['signatures'][0] if track.get('mp3') == mp3.name else None
    return mp3_signature, origin_signature


def register_failure(settings, row, message):
    with locked_state(settings) as state:
        name = Path(row['source']).name
        track = state['tracks'].setdefault(row['resource'], {'source': name})
        timestamp = ledger.now()
        track['failure'] = {'time': timestamp, 'reason': message}
        state['events'].append({'time': timestamp, 'file': name, 'status': 'failed', 'detail': message})
        save_state(settings, state)


def install_exclusive(candidate, destination):
    try:
        os.link(candidate, destination)
        return
    except OSError as exc:
        supported_fallbacks = {errno.EXDEV, errno.ENOTSUP, errno.ENOSYS, errno.EPERM}
        if exc.errno not in supported_fallbacks and getattr(exc, 'winerror', None) not in (1, 50):
            raise
    with candidate.open('rb') as original, destination.open('xb') as output:
        try:
            shutil.copyfileobj(original, output, length=1 << 20)
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            output.close()
            destination.unlink(missing_ok=True)
            raise
    try:
        shutil.copystat(candidate, destination)
    except OSError:
        destination.unlink(missing_ok=True)
        raise


def publish(settings, row, mode, staged_original, staged_mp3):
    original = Path(row['original'])
    mp3 = Path(row['mp3'])
    make_output_dir(original, settings.export / '原音')
    if staged_mp3 is not None:
        make_output_dir(mp3, settings.export / 'MP3')
    with locked_state(settings) as state:
        key = row['resource']
        previous = dict(state['tracks'].get(key, {}))
        expected_mp3, expected_original = known_output(previous, mp3, original)
        recorded = dict(previous, source=Path(row['source']).name)
        timestamp = ledger.now()
        moves = []
        signatures = []
        files = [(original, staged_original, expected_original)]
        if staged_mp3 is not None:
            files.append((mp3, staged_mp3, expected_mp3))
        for dest, candidate, expected in files:
            if dest.exists() or dest.is_symlink():
                if not dest.is_file() or dest.is_symlink() or not ledger.original_available(dest, expected):
                    raise StudioError('导出目录出现同名或已修改的文件，请核对后再重试。')
                signatures.append(expected)
            elif candidate:
                moves.append((candidate, dest))
                signatures.append(ledger.signature(candidate))
            else:
                raise StudioError('登记的原音已移动，请刷新清单再重试。')
        if mode == 'qmc':
            recorded.update(original_only={'name': original.name, 'signature': signatures[0]},
                            verified_at=timestamp)
        else:
            recorded.update(mp3=mp3.name, original=original.name,
                            signatures=signatures[::-1], verified_at=timestamp)
            recorded.pop('original_only', None)
        recorded.pop('failure', None)
        # Record the expected fingerprints first. A process interruption leaves
        # the ledger able to identify any completed companion without clobbering it.
        state['tracks'][key] = recorded
        save_state(settings, state)
        made = []
        event_count = len(state['events'])
        try:
            for candidate, dest in moves:
                install_exclusive(candidate, dest)
                made.append((dest, ledger.signature(dest)))
            if mode == 'qmc':
                recorded['original_only']['signature'] = ledger.signature(original)
            else:
                recorded['signatures'] = [ledger.signature(mp3), ledger.signature(original)]
            state['events'].append({
                'time': timestamp, 'file': recorded['source'],
                'status': 'original_ready' if mode == 'qmc' else 'success',
                'detail': original.name if mode == 'qmc' else mp3.name,
            })
            save_state(settings, state)
        except (OSError, ValueError):
            for dest, stamp in reversed(made):
                if dest.exists() and ledger.signature(dest) == stamp:
                    dest.unlink()
            state['tracks'][key] = previous
            del state['events'][event_count:]
            save_state(settings, state)
            raise


def check_source(path, resource):
    try:
        if path.is_symlink() or ledger.read_resource(path) != resource:
            raise StudioError('文件在确认后发生变化，请刷新后重新选择。')
        return ledger.signature(path)
    except (OSError, UnicodeError, struct.error):
        raise StudioError('源文件正在更新，请刷新后重新选择。') from None


def process_track(settings, row, mode, song_key=None, *, allow_key_fetch=False):
    source = Path(row['source'])
    start = check_source(source, row['resource'])
    if settings.export.is_symlink():
        raise StudioError('导出目录是符号链接，请核对音乐目录。')
    settings.export.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.format-kit-convert-', dir=settings.export) as folder:
        temporary_dir = Path(folder)
        if not row.get('has_original'):
            staged_original = temporary_dir / (source.stem + ledger.ORIGINAL_TYPES[source.suffix.lower()])
            duration = restore_and_validate(settings.ffmpeg, source, staged_original, song_key,
                                            allow_key_fetch=allow_key_fetch)
            original_to_encode = staged_original
        else:
            staged_original = None
            original_to_encode = Path(row['original'])
            duration = validate_original(settings.ffmpeg, original_to_encode)
        staged_mp3 = None
        if mode != 'qmc':
            staged_mp3 = temporary_dir / (source.stem + '.mp3')
            encode_mp3(settings.ffmpeg, original_to_encode, staged_mp3, mode, duration, source.name)
        if check_source(source, row['resource']) != start:
            raise StudioError('源文件在处理期间发生变化，请重新选择。')
        publish(settings, row, mode, staged_original, staged_mp3)


def emit_event(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


@contextmanager
def batch_lock(settings):
    with exclusive(settings.state.with_suffix('.convert.lock'), blocking=False):
        yield


def convert(settings, payload, emit=emit_event):
    if settings is None:
        raise StudioError('请先设置下载和导出目录。')
    if not isinstance(payload, dict):
        raise StudioError('无效的批次。')
    mode = payload.get('mode', 'tunefree')
    selected = payload.get('selected')
    if mode not in MODES or not isinstance(selected, list) or not selected or not all(
            isinstance(item, str) for item in selected):
        raise StudioError('请先选择待转码歌曲和有效的工具预设。')
    selected = list(dict.fromkeys(selected))
    keys = payload.get('keys', {})
    if not isinstance(keys, dict) or any(not isinstance(value, str) for value in keys.values()):
        raise StudioError('文件密钥格式无法读取。')
    keys = {item: keys[item].strip() for item in selected if item in keys and keys[item].strip()}
    if any(not re.fullmatch(r'[A-Za-z0-9+/=_-]{16,4096}', value) for value in keys.values()):
        raise StudioError('文件密钥格式无效，请核对后重试。')
    try:
        with batch_lock(settings):
            _process_batch(settings, selected, keys, mode, payload.get('allow_qq_login') is True, emit)
    except BlockingIOError:
        raise StudioError('另一个批次正在处理，请稍后重试。') from None


def _batch_tracks(settings, selected, keys, mode):
    rows = {row['resource']: row for row in scan(settings) if 'resource' in row}
    requested = {
        key for key in selected if key in rows and rows[key]['status'] in CONVERTIBLE
        and (mode != 'qmc' or not rows[key].get('has_original'))
    }
    missing_keys = {key for key in requested if rows[key]['needs_login'] and not keys.get(key)}
    return rows, requested, missing_keys


def _process_batch(settings, selected, keys, mode, authorized, emit):
    rows, requested, missing_keys = _batch_tracks(settings, selected, keys, mode)
    if missing_keys and sys.platform == 'darwin' and not authorized:
        raise StudioError('还未授权本批需要密钥的歌曲使用 QQ 音乐登录状态。')
    runnable = requested if sys.platform == 'darwin' else requested - missing_keys
    if runnable:
        try:
            first_use = not ffmpeg_runtime.resolve_ffmpeg(settings.ffmpeg).is_file()
            if first_use:
                emit({'event': 'progress', 'done': 0, 'total': len(selected),
                      'message': '正在下载并校验转码组件，请保持网络连接。'})
            settings = replace(settings, ffmpeg=ffmpeg_runtime.ensure_ffmpeg(settings.ffmpeg))
        except ffmpeg_runtime.FFmpegError as exc:
            raise StudioError(str(exc)) from None
        if first_use:
            rows, requested, refreshed_keys = _batch_tracks(settings, selected, keys, mode)
            if sys.platform == 'darwin' and refreshed_keys - missing_keys:
                raise StudioError('准备转码组件时，本批歌曲的状态发生变化，请刷新后重新确认。')
            missing_keys = refreshed_keys
    old_credentials = ekey.get_credentials
    old_opener = urllib.request._opener
    counts = Counter()
    try:
        if missing_keys and sys.platform == 'darwin':
            credentials = read_credentials()
            ekey.get_credentials = lambda *args, **kwargs: credentials
            urllib.request.install_opener(urllib.request.build_opener(
                urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(
                    context=ssl.create_default_context(cafile='/etc/ssl/cert.pem'
                                                       if Path('/etc/ssl/cert.pem').is_file() else None))))
        for idx, key in enumerate(selected, 1):
            row = rows.get(key)
            if key not in requested:
                status, message = 'skipped', '歌曲已完成，或源文件已变化'
            elif key in missing_keys and sys.platform != 'darwin':
                status, message = 'skipped', '这个加密文件需要对应的单曲密钥'
            else:
                try:
                    process_track(settings, row, mode, keys.get(key),
                                  allow_key_fetch=authorized and key in missing_keys)
                    status = 'original_ready' if mode == 'qmc' else 'success'
                    message = '原音已通过校验' if mode == 'qmc' else '原音和 MP3 已通过校验'
                except (StudioError, OSError, ValueError, subprocess.SubprocessError) as exc:
                    status = 'failed'
                    message = str(exc) if isinstance(exc, StudioError) else '处理或写入失败，请检查源文件和磁盘权限。'
                    register_failure(settings, row, message)
            name = Path(row['source']).name if row else '已移除的歌曲'
            counts[status] += 1
            emit({'event': 'progress', 'id': key, 'status': status, 'message': f'{name} · {message}',
                  'done': idx, 'total': len(selected)})
    finally:
        ekey.get_credentials = old_credentials
        urllib.request.install_opener(old_opener)
        scan(settings)
    succeeded = counts['success'] + counts['original_ready']
    emit({'event': 'complete', 'done': len(selected), 'total': len(selected), 'success': succeeded,
          'failed': counts['failed'], 'skipped': counts['skipped'],
          'message': f'完成 {succeeded} 首，失败 {counts["failed"]} 首，跳过 {counts["skipped"]} 首。'})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('inventory', 'configure', 'convert'))
    args = parser.parse_args()
    try:
        settings = local_settings()
        if args.action == 'inventory':
            emit_event(inventory(settings))
        elif args.action == 'configure':
            emit_event(config_inventory(json.load(sys.stdin)))
        else:
            convert(settings, json.load(sys.stdin))
        return 0
    except StudioError as exc:
        emit_event({'event': 'error', 'message': str(exc)})
    except (OSError, ValueError, subprocess.SubprocessError):
        emit_event({'event': 'error', 'message': '无法读取台账或输出目录，请检查磁盘空间和文件访问权限。'})
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
