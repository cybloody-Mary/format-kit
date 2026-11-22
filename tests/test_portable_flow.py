import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def run(backend, command, payload=None, env=None):
    args = [sys.executable, '-B', str(backend/'studio.py'), command]
    data = subprocess.check_output(args, input=json.dumps(payload) if payload is not None else None,
                                   text=True, env=env, timeout=40)
    return [json.loads(line) for line in data.splitlines()]


def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def qa_backend(backend, work):
    source = work/'下载 文件夹'
    source.mkdir()
    export = work/'导出 文件夹'
    env = dict(os.environ, QQMUSIC_STUDIO_HOME=str(work/'首次启动 用户目录'))
    [startup] = run(backend,'inventory',env=env)
    assert startup['paths']['configured'] is False and not startup['rows']
    assert not export.exists()
    source_file = source/'测试歌手-无损样本.flac'
    ffmpeg = backend / 'ffmpeg'
    subprocess.run([str(ffmpeg), '-hide_banner', '-loglevel', 'error',
                    '-f','lavfi','-i','sine=frequency=440:sample_rate=96000:duration=1',
                    '-sample_fmt','s32','-c:a','flac',str(source_file)],check=True)
    [result] = run(backend,'configure',{'source':str(source),'export':str(export)},env)
    [track] = result['rows']
    assert result['paths']['configured'] and track['status']=='pending'
    assert track['source_format']=='FLAC' and track['quality']=='无损'
    assert not track['needs_login'] and not track['has_original']
    [*_, complete] = run(backend,'convert',{'selected':[track['id']],'mode':'qmc'},env)
    assert complete['success']==1
    restored = export/'原音'/source_file.name
    assert hash_file(source_file)==hash_file(restored)
    [*_, complete] = run(backend,'convert',{'selected':[track['id']],'mode':'tunefree'},env)
    assert complete['success']==1 and (export/'MP3'/source_file.with_suffix('.mp3').name).is_file()
    [*_, complete] = run(backend,'convert',{'selected':[track['id']],'mode':'tunefree'},env)
    assert complete['skipped']==1
    assert (export/'.qqmusic-ledger.json').is_file()
    new_export = work/'另一目录'
    [changed] = run(backend,'configure',{'source':str(source),'export':str(new_export)},env)
    assert changed['summary']['pending']==1
    [switched] = run(backend,'configure',{'source':str(source),'export':str(export)},env)
    assert switched['summary']['success']==1


if __name__ == '__main__':
    target = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='music-portability-') as temp:
        work=Path(temp)
        qa_backend(target, work)
    print('Fresh profiles, user-chosen Unicode paths, offline lossless export, MP3, deduplication and switching outputs passed.')
