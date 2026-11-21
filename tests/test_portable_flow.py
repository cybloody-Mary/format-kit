import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


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
    ffmpeg = backend / ('ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg')
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
    return source


def qa_http(backend, source, work):
    secret_home = work/'浏览器首次启动'
    env = dict(os.environ, QQMUSIC_STUDIO_HOME=str(secret_home))
    process = subprocess.Popen([sys.executable,'-B',str(backend/'win_server.py'),'--no-browser'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,text=True,env=env)
    try:
        url, token = process.stdout.readline().strip().split('#token=',1)
        host = url.rstrip('/')
        with urllib.request.urlopen(host + '/app-icon.png', timeout=10) as response:
            assert response.headers.get_content_type() == 'image/png'
            assert response.read() == (backend / 'web' / 'app-icon.png').read_bytes()
        def api(path, payload=None, auth=True):
            headers = {'Authorization':'Bearer '+token} if auth else {}
            if payload is not None:
                headers['Content-Type']='application/json'
            request = urllib.request.Request(host+path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.load(response)
        try:
            api('/api/inventory',auth=False)
            raise AssertionError('An unauthenticated browser reached local inventory')
        except urllib.error.HTTPError as error:
            assert error.code==403
        assert not api('/api/inventory')['paths']['configured']
        dest = work/'本地浏览器导出'
        inventory = api('/api/configure',{'source':str(source),'export':str(dest)})
        [track] = inventory['rows']
        running = api('/api/convert',{'mode':'qm','selected':[track['id']]})
        deadline=time.monotonic()+15
        while True:
            job=api('/api/jobs/'+running['job'])
            if not job['running']:
                break
            assert time.monotonic()<deadline
            time.sleep(0.05)
        assert job['events'][-1]['success']==1, job
        assert any((dest/'MP3').glob('*.mp3'))
        assert api('/api/stop',{})['ok']
        assert process.wait(timeout=10)==0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    target = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='music-portability-') as temp:
        work=Path(temp)
        source=qa_backend(target,work)
        if (target/'win_server.py').exists():
            qa_http(target,source,work)
    print('Fresh profiles, user-chosen Unicode paths, offline lossless export, MP3, deduplication and switching outputs passed.')
