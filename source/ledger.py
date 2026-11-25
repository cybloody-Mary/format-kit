#!/usr/bin/env python3
"""Format Kit 本地转码台账。只读取下载和导出目录，状态保存在程序旁。"""

import argparse
from collections import Counter
from datetime import datetime
from hashlib import sha256
import sys
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import time
import unicodedata

import ffmpeg_runtime
import portable_paths
from portable_lock import exclusive
from tunefree_backend import qmc2

SOURCE = Path.home()
EXPORT = portable_paths.export_suggestion()
HERE = Path(__file__).resolve().parent
ORIGINAL_TYPES = {'.mflac': '.flac', '.mgg': '.ogg', '.flac': '.flac', '.ogg': '.ogg',
                  '.mflac0': '.flac', '.mgg0': '.ogg', '.mgg1': '.ogg', '.mggl': '.ogg',
                  '.qmcflac': '.flac', '.qmcogg': '.ogg', '.qmc2': '.ogg'}
STATUS = {'success': '成功', 'pending': '待转码', 'failed': '失败',
          'repair': '待核验或补齐', 'original_ready': '仅原音', 'incomplete': '待下载完整'}
RESOURCE = re.compile(r'[A-Za-z0-9_-]+\.(?:mflac0?|mgg[01l]?)', re.IGNORECASE)
MEDIA_ID = re.compile(r'[A-Za-z0-9_-]+')


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def canonical(name):
    return unicodedata.normalize('NFC', name).casefold()


def one_line(value):
    return ' '.join(str(value).splitlines())


def signature(path):
    info = path.stat()
    return [info.st_size, info.st_mtime_ns]


def identify_local(path, handle, before, kind, cache, known):
    stamp = [before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns]
    key = str(path)
    if known.get(key, {}).get('stamp') == stamp:
        identity = known[key]['id']
    else:
        digest = sha256()
        handle.seek(0)
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
        identity = kind + ':' + digest.hexdigest()
    current = path.stat()
    if (current.st_size, current.st_mtime_ns, current.st_ino) != (
        before.st_size, before.st_mtime_ns, before.st_ino
    ):
        return None
    if cache is not None:
        cache[key] = {'stamp': stamp, 'id': identity}
    return identity


def read_resource(path, cache=None, known=None):
    """Keep resource identities stable through renames and file duplicates."""
    known = known or {}
    with path.open('rb') as handle:
        before = os.fstat(handle.fileno())
        size = before.st_size
        if size <= 100:
            return None
        head = handle.read(4)
        expected = ORIGINAL_TYPES.get(path.suffix.lower())
        if ((head == b'fLaC' and expected == '.flac') or
                (head == b'OggS' and expected == '.ogg')):
            return identify_local(path, handle, before, 'audio', cache, known)
        handle.seek(-16, os.SEEK_END)
        length, version, marker = struct.unpack('<II8s', handle.read(16))
        if marker != b'musicex\x00':
            handle.seek(-min(size, 65536), os.SEEK_END)
            footer = qmc2.detect_footer(handle.read())
            if footer.kind in ('qtag', 'v1') or path.suffix.lower() in ('.qmcflac', '.qmcogg', '.qmc2'):
                return identify_local(path, handle, before, 'embedded', cache, known)
            return None
        if version != 1 or not 156 <= length < size:
            return None
        handle.seek(-length, os.SEEK_END)
        metadata = handle.read(140)
        current = path.stat()
        if (before.st_ino, size, before.st_mtime_ns) != (
            current.st_ino, current.st_size, current.st_mtime_ns
        ):
            return None
    mid = metadata[12:72].decode('utf-16-le').split('\0', 1)[0]
    name = metadata[72:140].decode('utf-16-le').split('\0', 1)[0]
    if not MEDIA_ID.fullmatch(mid) or not RESOURCE.fullmatch(name):
        return None
    if ORIGINAL_TYPES.get(Path(name).suffix.lower()) != ORIGINAL_TYPES.get(path.suffix.lower()):
        return None
    return mid + '/' + name


def needs_musicex_key(identity):
    return not identity.startswith(('audio:', 'embedded:'))


def inspect_source(folder, cache=None, known=None):
    groups = {}
    incomplete = []
    for path in sorted(folder.iterdir(), key=lambda p: canonical(p.name)):
        if path.suffix.lower() not in ORIGINAL_TYPES or path.is_symlink() or not path.is_file():
            continue
        try:
            resource = read_resource(path, cache, known)
        except (OSError, UnicodeError, struct.error, ValueError):
            resource = None
        if resource:
            groups.setdefault(resource, []).append(path)
        else:
            incomplete.append(path)
    return groups, incomplete


def index_audio(folder, extensions):
    files = {}
    if folder.is_dir():
        for path in folder.iterdir():
            if path.suffix.lower() in extensions and not path.is_symlink() and path.is_file():
                key = canonical(path.name)
                files[key] = path if key not in files else None
    return files


def probe_mp3(path):
    ffmpeg = ffmpeg_runtime.resolve_ffmpeg(HERE / ('ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg'))
    if ffmpeg.is_file():
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
        probe = subprocess.run(
            [str(ffmpeg), '-hide_banner', '-xerror', '-nostdin', '-i', str(path),
             '-map', '0:a:0', '-t', '0.05', '-f', 'null', '-'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20, **options,
        )
        return probe.returncode == 0 and bool(re.search(r'Audio:\s*mp3[ ,]', probe.stderr))
    if sys.platform != 'darwin':
        return False
    info = subprocess.run(['/usr/bin/afinfo', str(path)], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=15)
    duration = re.search(r'duration:\s*(\d+\.\d+) sec', info.stdout)
    return bool(info.returncode == 0 and '.mp3' in info.stdout
                and duration and float(duration[1]) > 0)


def probe_original(path):
    with path.open('rb') as handle:
        header = handle.read(10)
        if path.suffix.lower() == '.ogg':
            return header.startswith(b'OggS')
        if header.startswith(b'ID3') and len(header) == 10:
            size = sum((byte & 127) << (7 * (3 - i)) for i, byte in enumerate(header[6:10]))
            handle.seek(10 + size + (10 if header[5] & 16 else 0))
            header = handle.read(10)
        return header.startswith(b'fLaC')


def verify_pair(mp3, original):
    if not mp3 or not original:
        return None
    try:
        before = [signature(mp3), signature(original)]
        if min(item[0] for item in before) > 100 and probe_mp3(mp3) and probe_original(original):
            if before == [signature(mp3), signature(original)]:
                return before
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def fresh_state():
    return {'version': 1, 'tracks': {}, 'events': [], 'unassigned_exports': {}}


def verify_export_shape(mp3, original):
    if mp3 is None or original is None:
        return False
    try:
        return signature(mp3)[0] > 100 and signature(original)[0] > 100
    except OSError:
        return False


def registered_files_match(mp3, original, stamps):
    if not mp3 or not original:
        return False
    try:
        return [signature(mp3), signature(original)] == stamps
    except OSError:
        return False


def original_available(path, stamp):
    try:
        return bool(path and signature(path) == stamp)
    except OSError:
        return False


def save_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                     prefix='.format-kit-', delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def assign_names(path, out, reserved):
    stem = path.stem
    ext = ORIGINAL_TYPES[path.suffix.lower()]
    number = 1
    while canonical(stem + '.mp3') in reserved or canonical(stem + ext) in reserved:
        suffix = f' [{ext[1:].upper()}]' if number == 1 else f' [{ext[1:].upper()} {number}]'
        # Leave room for the suffix even when the input name nearly fills the filesystem limit.
        base = path.stem.encode('utf-8')[:225].decode('utf-8', 'ignore')
        stem = base + suffix
        number += 1
    reserved.update((canonical(stem + '.mp3'), canonical(stem + ext)))
    return str(out / 'MP3' / (stem + '.mp3')), str(out / '原音' / (stem + ext))


def collect(state, groups, incomplete, out, first_scan):
    mp3_files = index_audio(out / 'MP3', {'.mp3'})
    originals = index_audio(out / '原音', {'.flac', '.ogg'})
    tracks = state['tracks']
    claimed = {canonical(track['mp3']): key for key, track in tracks.items() if 'mp3' in track}
    reserved = set(mp3_files) | set(originals) | set(claimed)
    reserved.update(canonical(track['original']) for track in tracks.values() if 'original' in track)
    rows = []
    source_stems = Counter(canonical(path.stem) for copies in groups.values() for path in copies)
    for key, copies in groups.items():
        track = tracks.setdefault(key, {})
        path = next((p for p in copies if p.name == track.get('source')), copies[0])
        if 'mp3' not in track and len(copies) > 1:
            path = next((p for p in copies if canonical(p.stem + '.mp3') in mp3_files and
                         canonical(p.stem + ORIGINAL_TYPES[p.suffix.lower()]) in originals), path)
        track['source'] = path.name
        pending_reason = ''
        saved_original = track.get('original_only', {})
        ready_original = originals.get(canonical(saved_original.get('name', '')))
        if not original_available(ready_original, saved_original.get('signature')):
            ready_original = None
        mp3_key = canonical(path.stem + '.mp3')
        original_ext = ORIGINAL_TYPES[path.suffix.lower()]
        possible = mp3_files.get(mp3_key)
        matching_original = originals.get(canonical(path.stem + original_ext))
        if ('mp3' not in track and mp3_key not in claimed and
                mp3_key not in state['unassigned_exports'] and
                source_stems[canonical(path.stem)] == 1 and
                verify_export_shape(possible, matching_original)):
            # Only infer pairs with unique titles and matching source quality. A pair
            # older than a new download needs an explicit confirmation after setup.
            try:
                exported_after_source = min(signature(possible)[1], signature(matching_original)[1]) >= signature(path)[1]
            except OSError:
                exported_after_source = False
            if first_scan or exported_after_source:
                stamps = verify_pair(possible, matching_original)
                if stamps:
                    track.update(mp3=possible.name, original=matching_original.name,
                                 signatures=stamps, verified_at=now())
                    track.pop('failure', None)
                    track.pop('original_only', None)
                    claimed[mp3_key] = key

        mp3 = mp3_files.get(canonical(track['mp3'])) if 'mp3' in track else None
        original = originals.get(canonical(track['original'])) if 'original' in track else None
        if 'mp3' in track:
            if original_available(original, track['signatures'][1]):
                ready_original = original
            status = 'repair'
            pending_reason = '已登记的导出缺失，或文件自核验后被更改'
            if registered_files_match(mp3, original, track['signatures']):
                status = 'success'
                pending_reason = ''
            elif track.get('failure'):
                status = 'failed'
                pending_reason = track['failure']['reason']
            output = (str(out / 'MP3' / track['mp3']), str(out / '原音' / track['original']))
        elif ready_original:
            status = 'failed' if track.get('failure') else 'original_ready'
            pending_reason = track.get('failure', {}).get('reason', '原音已保存，可以直接补转 MP3')
            suggested_mp3 = out / 'MP3' / ready_original.with_suffix('.mp3').name
            if canonical(suggested_mp3.name) in reserved:
                suggested_mp3, _ = assign_names(path, out, reserved)
            else:
                reserved.add(canonical(suggested_mp3.name))
            output = (str(suggested_mp3), str(ready_original))
        elif track.get('failure'):
            status = 'failed'
            pending_reason = track['failure']['reason']
            output = assign_names(path, out, reserved)
        else:
            status = 'pending'
            if possible:
                other_ext = '.ogg' if original_ext == '.flac' else '.flac'
                if originals.get(canonical(path.stem + other_ext)):
                    pending_reason = '已有同名歌曲的另一音质版本'
                else:
                    pending_reason = '已有同名导出，需分别核对'
            output = assign_names(path, out, reserved)

        rows.append({'resource': key, 'source': str(path), 'status': status,
                     'copies': [str(p) for p in copies if p != path], 'detail': pending_reason,
                     'mp3': output[0], 'original': output[1],
                     'has_original': ready_original is not None,
                     'needs_login': ready_original is None and needs_musicex_key(key)})

    for key, mp3 in mp3_files.items():
        if key in claimed or key in state['unassigned_exports'] or not mp3:
            continue
        for extension in ('.flac', '.ogg'):
            original = originals.get(canonical(mp3.stem + extension))
            stamps = verify_pair(mp3, original)
            if stamps:
                state['unassigned_exports'][key] = {
                    'mp3': mp3.name, 'original': original.name, 'signatures': stamps}
                break

    rows.extend({'source': str(path), 'status': 'incomplete', 'copies': [],
                 'detail': '未读到完整且匹配的 musicex 文件尾'} for path in incomplete)
    return rows


def record_result(args, state, groups, out):
    key = next((key for key, paths in groups.items()
                if any(p.name == args.file or str(p) == args.file for p in paths)), None)
    if key is None:
        raise ValueError('找不到这首已下载完整的歌曲，请填写源文件名或完整路径')
    path = next(p for p in groups[key] if p.name == args.file or str(p) == args.file)
    track = state['tracks'].setdefault(key, {'source': path.name})
    event = {'time': now(), 'file': path.name, 'status': 'failed' if args.action == 'failure' else 'success'}
    if args.action == 'failure':
        reason = one_line(args.reason).strip()
        if not reason:
            raise ValueError('请填写失败原因')
        track['failure'] = {'time': event['time'], 'reason': reason}
        event['detail'] = reason
    else:
        mp3 = args.mp3.expanduser().resolve(strict=True)
        original = args.original.expanduser().resolve(strict=True)
        if (mp3.parent != (out / 'MP3').resolve() or mp3.suffix.lower() != '.mp3'
                or original.parent != (out / '原音').resolve()
                or original.suffix.lower() != ORIGINAL_TYPES[path.suffix.lower()]):
            raise ValueError('请提供歌曲对应的 MP3 和原音子目录中的已导出文件')
        if any(other != key and canonical(value.get('mp3', '')) == canonical(mp3.name)
               for other, value in state['tracks'].items()):
            raise ValueError('此 MP3 已登记给另一音质或歌曲，请另外命名')
        if any(other != key and canonical(value.get('original', '')) == canonical(original.name)
               for other, value in state['tracks'].items()):
            raise ValueError('此原音已登记给另一资源，请另外命名')
        stamps = verify_pair(mp3, original)
        if not stamps:
            raise ValueError('MP3 或原音未通过格式校验')
        track.update(mp3=mp3.name, original=original.name, signatures=stamps, verified_at=now())
        track.pop('failure', None)
        track.pop('original_only', None)
        state['unassigned_exports'].pop(canonical(mp3.name), None)
        event['detail'] = mp3.name
    state['events'].append(event)


def render_report(rows, state, src, out):
    counts = Counter(row['status'] for row in rows)
    duplicates = sum(len(row['copies']) for row in rows)
    legacy = []
    current_resources = {row.get('resource') for row in rows}
    past = [track for key, track in state['tracks'].items()
            if key not in current_resources and 'mp3' in track]
    for exported in list(state['unassigned_exports'].values()) + past:
        if registered_files_match(out / 'MP3' / exported['mp3'],
                                  out / '原音' / exported['original'], exported['signatures']):
            legacy.append(exported)

    lines = ['Format Kit 转码台账', f'刷新时间　{now()}', '',
             f'源目录　{src}', f'导出目录　{out}', '',
             f'当前源文件 {len(rows) + duplicates} 个，重复副本 {duplicates} 个。',
             '　'.join(f'{label} {counts[status]}' for status, label in STATUS.items()),
             f'已核验的历史导出（当前来源版本不同或未匹配） {len(legacy)} 首。', '']
    for status in ('pending', 'original_ready', 'failed', 'repair', 'incomplete', 'success'):
        lines.append(f'【{STATUS[status]} · {counts[status]} 首】')
        for row in sorted(rows, key=lambda v: canonical(Path(v['source']).name)):
            if row['status'] != status:
                continue
            text = '  ' + one_line(Path(row['source']).name)
            if row['detail']:
                text += '　｜　' + one_line(row['detail'])
            lines.append(text)
            for copy in row['copies']:
                lines.append('    同一音质的重复副本　' + one_line(Path(copy).name))
        lines.append('')
    if legacy:
        lines.append('【历史导出】')
        lines.extend('  ' + one_line(item['original']) for item in sorted(legacy, key=lambda v: v['original']))
        lines.append('')
    if state['events']:
        lines.append('【最近 20 条转码记录】')
        for event in state['events'][-20:][::-1]:
            lines.append(f"  {event['time']}　{STATUS[event['status']]}　"
                         f"{one_line(event['file'])}　｜　{one_line(event['detail'])}")
        lines.append('')
    lines.extend(['【使用方法】', '在 Format Kit 中刷新歌曲库，或运行「python3 ledger.py watch」自动同步。',
                  '单独扫描文件无法判断转码器报错。转码流程可用 failure 命令记录失败，'
                  '用 success 命令核验及登记导出。',
                  '运行「python3 ledger.py --help」查看队列、登记和停止命令。', ''])
    return '\n'.join(lines), counts, len(legacy)


def refresh(args):
    src = args.source.expanduser().resolve(strict=True)
    out = args.export.expanduser().resolve()
    path = args.state.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive(path.with_suffix('.lock')):
        first_scan = not path.exists()
        state = fresh_state() if first_scan else json.loads(path.read_text(encoding='utf-8'))
        if state.get('version') != 1:
            raise ValueError('无法读取台账版本')
        identity_cache = {}
        groups, incomplete = inspect_source(src, identity_cache, state.get('inputs'))
        state['inputs'] = identity_cache
        if args.action in ('success', 'failure'):
            record_result(args, state, groups, out)
        rows = collect(state, groups, incomplete, out, first_scan)
        report, counts, legacy = render_report(rows, state, src, out)
        save_text(path, json.dumps(state, ensure_ascii=False, indent=2))
        save_text(args.report.expanduser().absolute(), report)
    summary = '  '.join(f'{STATUS[key]} {counts[key]}' for key in STATUS) + f' | 历史导出 {legacy}'
    return rows, summary


def folder_snapshot(args):
    out = args.export.expanduser().resolve()
    folders = [(args.source.expanduser(), ORIGINAL_TYPES), (out / 'MP3', ('.mp3',)),
               (out / '原音', ('.flac', '.ogg'))]
    entries = []
    for directory, suffixes in folders:
        if not directory.exists():
            entries.append((str(directory), None))
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() in suffixes and path.is_file() and not path.is_symlink():
                try:
                    entries.append((str(path), signature(path)))
                except FileNotFoundError:
                    continue
    return entries


def watch(args):
    state = args.state.expanduser().resolve()
    state.parent.mkdir(parents=True, exist_ok=True)
    stop = state.with_suffix('.watch.stop')
    if sys.platform != 'darwin':
        raise ValueError('请在 Windows 界面中查看自动刷新。')
    import fcntl
    with state.with_suffix('.watch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('自动同步已经在运行。', flush=True)
            return
        stop.unlink(missing_ok=True)
        previous = None
        last_error = None
        print(f'自动同步已启动，每 {args.interval:g} 秒检查文件变化。', flush=True)
        while not stop.exists():
            try:
                current = folder_snapshot(args)
                if current != previous:
                    _, summary = refresh(args)
                    print(f'{now()}  {summary}', flush=True)
                    previous = current
                last_error = None
            except (OSError, ValueError) as exc:
                if str(exc) != last_error:
                    print(f'等待目录恢复访问权限：{exc}', flush=True)
                    last_error = str(exc)
            time.sleep(args.interval)
        stop.unlink(missing_ok=True)
        print('自动同步已停止。', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--export', type=Path, default=EXPORT)
    parser.add_argument('--state', type=Path, default=HERE / '.format-kit-ledger.json')
    parser.add_argument('--report', type=Path, default=HERE / 'Format Kit 转码清单.txt')
    actions = parser.add_subparsers(dest='action')
    actions.add_parser('refresh', help='刷新当前状态和文本清单')
    watcher = actions.add_parser('watch', help='在源文件或导出文件变化后自动刷新台账')
    watcher.add_argument('--interval', type=float, default=5, help='检查间隔，默认 5 秒')
    actions.add_parser('stop', help='停止自动同步')
    queue = actions.add_parser('queue', help='列出各首待处理歌曲和建议的输出路径')
    queue.add_argument('--retry-failed', action='store_true', help='把已记录的失败也加入队列')
    failure = actions.add_parser('failure', help='记录一次实际发生的转码失败')
    failure.add_argument('file')
    failure.add_argument('reason')
    success = actions.add_parser('success', help='核验并记录已完成的导出')
    success.add_argument('file')
    success.add_argument('--mp3', type=Path, required=True)
    success.add_argument('--original', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == 'watch':
            if not 0.2 <= args.interval <= 60:
                raise ValueError('检查间隔需在 0.2 到 60 秒之间')
            watch(args)
            return
        if args.action == 'stop':
            args.state.expanduser().resolve().with_suffix('.watch.stop').touch()
            print('已请求停止自动同步。')
            return
        rows, summary = refresh(args)
        if args.action == 'queue':
            statuses = {'pending', 'repair', 'original_ready'} | ({'failed'} if args.retry_failed else set())
            print(json.dumps([row for row in rows if row['status'] in statuses],
                             ensure_ascii=False, indent=2))
        else:
            print(summary)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'台账更新失败：{exc}\n')


if __name__ == '__main__':
    main()
