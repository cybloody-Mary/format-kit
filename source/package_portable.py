"""Build portable release archives from source and checksum-verified runtimes."""

import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

from fetch_dependencies import ARTIFACTS, TARGETS, pinned_file
from package_macos_icon import build_macos_icon

SOURCE = Path(__file__).resolve().parent
ROOT = SOURCE.parent
WINDOWS = ROOT / 'windows'
DIST = ROOT / 'dist'
BUILD = ROOT / 'build'
PROGRAM = ('studio.py', 'ledger.py', 'portable_paths.py', 'portable_lock.py', 'library_metadata.py',
           'ffmpeg_runtime.py', 'fetch_dependencies.py')
APP = 'Format Kit'
APP_COVER = SOURCE / 'web' / 'app-icon.png'
VERSION = '2.0.4'
ONLINE_NOTE = '首次转换时应用会从 PyPI 下载并校验转码组件，请保持网络连接。\n'


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def copy_program(destination):
    destination.mkdir(parents=True, exist_ok=True)
    for name in PROGRAM:
        shutil.copy2(SOURCE / name, destination / name)
    for document in ('LICENSE', 'NOTICE'):
        shutil.copy2(ROOT / document, destination / document)
    shutil.copytree(SOURCE / 'tunefree_backend', destination / 'tunefree_backend',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(SOURCE / 'Notices', destination / 'Notices')
    (destination / 'Notices' / 'Components.txt').write_text(
        'TuneFree for Mac 3.2.2 — MIT\nhttps://github.com/Dora-z/TuneFree_Mac/tree/v3.2.2\n\n'
        'CPython 3.14.10 — PSF License\nhttps://www.python.org/downloads/release/python-31410/\n'
        'macOS runtime build (recipe and archives)\n'
        'https://github.com/astral-sh/python-build-standalone/tree/20261110\n\n'
        'FFmpeg, executable from imageio-ffmpeg 0.8.1; separate process.\n'
        'https://pypi.org/project/imageio-ffmpeg/0.8.1/\n'
        'https://github.com/imageio/imageio-ffmpeg/tree/v0.8.1/\n'
        'FFmpeg upstream source and license information\n'
        'https://ffmpeg.org/releases/\nhttps://ffmpeg.org/legal.html\n'
        'LAME upstream source\nhttps://sourceforge.net/projects/lame/files/lame/3.100/\n',
        encoding='utf-8')
    (destination / 'Notices' / 'Artifact-SHA256.txt').write_text(
        ''.join(f'{ARTIFACTS[name][1]}  {name}\n' for name in TARGETS['macos']), encoding='utf-8')


def extract_ffmpeg(artifact, destination):
    with zipfile.ZipFile(artifact) as archive:
        [binary] = [entry for entry in archive.namelist()
                    if entry.startswith('imageio_ffmpeg/binaries/ffmpeg-')]
        with archive.open(binary) as source, destination.open('wb') as output:
            shutil.copyfileobj(source, output)
    destination.chmod(0o755)


def archive_tree(folder, target):
    with tempfile.TemporaryDirectory(prefix='.archive-', dir=target.parent) as name:
        pending = Path(name) / target.name
        # Preserve executable permissions and symbolic links on macOS.
        if folder.suffix == '.app':
            run('/usr/bin/ditto', '-c', '-k', '--norsrc', '--noextattr', '--keepParent', folder, pending)
        else:
            with zipfile.ZipFile(pending, 'w', zipfile.ZIP_DEFLATED, compresslevel=5) as package:
                for file in sorted(folder.rglob('*')):
                    if file.is_file():
                        package.write(file, file.relative_to(folder.parent))
        with zipfile.ZipFile(pending) as package:
            if (error := package.testzip()):
                raise ValueError(f'ZIP check failed for {error}')
        pending.replace(target)
    print(target, flush=True)


def build_windows(staging, dependencies, go, clang):
    folder = staging / 'FormatKit'
    folder.mkdir()
    for name in ('catalog.py', 'server.py', '使用说明.md', 'choose-path.ps1',
                 'Start Format Kit.cmd'):
        path = WINDOWS / name
        if path.suffix in {'.cmd', '.ps1'}:
            (folder / name).write_bytes(path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
        else:
            shutil.copy2(path, folder / name)
    for name in ('web', 'licenses'):
        shutil.copytree(WINDOWS / name, folder / name)
    for name in ('LICENSE', 'NOTICE'):
        shutil.copy2(ROOT / name, folder / name)

    runtime = folder / 'runtime'
    runtime.mkdir()
    with zipfile.ZipFile(dependencies['python-win-x64.zip']) as archive:
        archive.extractall(runtime)
    (runtime / 'python313._pth').write_bytes(b'python313.zip\r\n.\r\n..\r\n')

    engine = folder / 'engine'
    engine.mkdir()
    extract_ffmpeg(dependencies['ffmpeg-win-x64.whl'], engine / 'ffmpeg.exe')

    sources = folder / 'source'
    sources.mkdir()
    with tarfile.open(dependencies['kugo-v0.6.1.tar.gz']) as archive:
        archive.extractall(sources, filter='data')
    kugo = sources / 'kugo-engine'
    (sources / 'Kugo-Music-Converter-0.6.1').rename(kugo)
    shutil.copy2(WINDOWS / 'engine' / 'protocol_output.go',
                 kugo / 'backend' / 'internal' / 'logger' / 'protocol_output.go')
    command = kugo / 'backend' / 'cmd' / 'format-kit-engine'
    command.mkdir()
    shutil.copy2(WINDOWS / 'engine' / 'main.go', command / 'main.go')
    launcher = sources / 'launcher'
    shutil.copytree(WINDOWS / 'launcher', launcher)
    shutil.copy2(WINDOWS / 'BUILD.md', sources / '编译说明.md')
    icon = launcher / 'icon_windows_amd64.syso'
    run(sys.executable, launcher / 'icon_resource.py', folder / 'web' / 'cover.png',
        icon, '--clang', clang)

    env = dict(os.environ, GOOS='windows', GOARCH='amd64', CGO_ENABLED='0')
    options = [str(go), 'build', '-trimpath', '-buildvcs=false']
    subprocess.run(options + ['-ldflags=-s -w', '-o', engine / 'format-kit-engine.exe',
                              './cmd/format-kit-engine'],
                   cwd=kugo / 'backend', env=env, check=True)
    subprocess.run(options + ['-ldflags=-s -w -H=windowsgui', '-o',
                              folder / 'Start Format Kit.exe', '.'],
                   cwd=launcher, env=env, check=True)
    icon.unlink()
    archive_tree(folder, DIST / 'FormatKit-Windows-x64.zip')


def active_macos_sdk():
    command = ['/usr/bin/xcrun', '--sdk', 'macosx', '--show-sdk-path']
    return Path(subprocess.check_output(command, text=True).strip())


def build_mac(identity, sdk, staging, dependencies):
    app = staging / (APP + '.app')
    contents = app / 'Contents'
    resources = contents / 'Resources'
    macos = contents / 'MacOS'
    macos.mkdir(parents=True)
    copy_program(resources)
    if sdk is None:
        sdk = active_macos_sdk()
    if not sdk.is_dir():
        raise ValueError(f'macOS SDK folder not found: {sdk}')
    print(f'Building macOS with SDK {sdk}', flush=True)
    shutil.copy2(APP_COVER, resources / 'AppCover.png')
    build_macos_icon(APP_COVER, resources / 'AppIcon.icns', sdk=sdk)
    swift_binaries = []
    ffmpeg_binaries = []
    for arch, suffix in (('arm64', 'arm64'), ('x86_64', 'x64')):
        swift = staging / f'FormatKit-{arch}'
        run('/usr/bin/xcrun', 'swiftc', '-sdk', sdk, '-parse-as-library',
            '-target', f'{arch}-apple-macos14.0', '-module-cache-path', BUILD / f'swift-cache-{arch}',
            '-O', SOURCE / 'FormatKit.swift', '-o', swift)
        swift_binaries.append(swift)
        extract = resources / 'runtime' / arch
        extract.mkdir(parents=True)
        run('/usr/bin/tar', '-xzf', dependencies[f'python-mac-{suffix}.tar.gz'],
            '--strip-components', '1', '-C', extract)
        if not (extract / 'bin/python3').is_file():
            raise ValueError(f'Missing {arch} bundled Python')
        if artifact := dependencies.get(f'ffmpeg-mac-{suffix}.whl'):
            ffmpeg = staging / f'ffmpeg-{arch}'
            extract_ffmpeg(artifact, ffmpeg)
            ffmpeg_binaries.append(ffmpeg)
    bundled_ffmpeg = resources / 'ffmpeg'
    if ffmpeg_binaries:
        run('/usr/bin/lipo', '-create', *ffmpeg_binaries, '-output', bundled_ffmpeg)
        bundled_ffmpeg.chmod(0o755)
    executable = macos / 'FormatKit'
    run('/usr/bin/lipo', '-create', *swift_binaries, '-output', executable)
    executable.chmod(0o755)
    info = {
        'CFBundleIdentifier': 'io.github.cybloody-mary.format-kit',
        'CFBundleName': APP, 'CFBundleDisplayName': APP, 'CFBundleExecutable': 'FormatKit',
        'CFBundleIconFile': 'AppIcon.icns',
        'CFBundleInfoDictionaryVersion': '6.0', 'CFBundleShortVersionString': VERSION,
        'CFBundleVersion': '6', 'CFBundleDevelopmentRegion': 'zh-Hans', 'CFBundlePackageType': 'APPL',
        'LSMinimumSystemVersion': '14.0', 'NSHighResolutionCapable': True,
    }
    with (contents / 'Info.plist').open('wb') as handle:
        plistlib.dump(info, handle)
    (resources / '使用说明.txt').write_text(
        f'{APP} {VERSION} · macOS 14+\n\n'
        '解压后打开应用。首次运行请确认下载目录和导出目录。\n'
        '「目录设置」可随时切换，台账随导出目录保存。\n'
        + (ONLINE_NOTE if not ffmpeg_binaries else '')
        + '\n应用许可见 LICENSE，第三方组件版本及许可见 Notices。\n',
        encoding='utf-8')
    run('/usr/bin/xattr', '-cr', app)
    sign_flags = [] if identity == '-' else ['--options', 'runtime', '--timestamp']
    nested = [bundled_ffmpeg] if ffmpeg_binaries else []
    for file in sorted((resources / 'runtime').rglob('*')):
        if file.is_symlink() or not file.is_file():
            continue
        with file.open('rb') as handle:
            prefix = handle.read(4)
        if prefix in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe'):
            nested.append(file)
    run('/usr/bin/codesign', '--force', '--sign', identity, *sign_flags, *nested)
    run('/usr/bin/codesign', '--force', '--sign', identity, *sign_flags, app)
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', app)
    archive_tree(app, DIST / 'FormatKit-macOS-universal.zip')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=TARGETS)
    parser.add_argument('--sdk', type=Path, help='macOS SDK path (defaults to the active Xcode SDK)')
    parser.add_argument('--sign', default='-', help='macOS signing identity (defaults to ad-hoc signing)')
    parser.add_argument('--go', type=Path, help='Go executable for compiling the Windows engine')
    parser.add_argument('--clang', type=Path, help='Clang executable for compiling the Windows icon')
    parser.add_argument('--public', action='store_true',
                        help='have the macOS release download FFmpeg at first conversion')
    args = parser.parse_args()
    if args.target == 'macos' and sys.platform != 'darwin':
        parser.error('The macOS target requires macOS and an Apple Swift 6 toolchain')
    go = str(args.go.resolve()) if args.go else shutil.which('go')
    clang = str(args.clang.resolve()) if args.clang else shutil.which('clang')
    if args.target == 'windows-x64' and not (go and clang):
        parser.error('The Windows x64 bundle requires Go and Clang.')
    try:
        dependencies = {name: pinned_file(name) for name in TARGETS[args.target]
                        if args.target != 'macos' or not args.public or not name.startswith('ffmpeg-')}
    except (OSError, ValueError) as error:
        parser.error(f'{error}. Run python3 source/fetch_dependencies.py {args.target}.')
    DIST.mkdir(exist_ok=True)
    BUILD.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f'{args.target}-', dir=BUILD) as name:
        staging = Path(name)
        if args.target == 'macos':
            build_mac(args.sign, args.sdk, staging, dependencies)
        else:
            build_windows(staging, dependencies, go, clang)


if __name__ == '__main__':
    main()
