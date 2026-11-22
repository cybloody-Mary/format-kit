"""Fetch and verify the pinned public runtimes used by the portable release."""

import argparse
import hashlib
from pathlib import Path
import sys
import tempfile
from urllib.request import urlopen

DOWNLOADS = Path(__file__).resolve().parent.parent / 'downloads'
ASTRAL = 'https://github.com/astral-sh/python-build-standalone/releases/download/20261110/'
PYTHON = 'https://www.python.org/ftp/python/3.13.17/'
PYPI = 'https://files.pythonhosted.org/packages/'
ARTIFACTS = {
    'python-mac-arm64.tar.gz': (
        ASTRAL + 'cpython-3.14.10%2B20261110-aarch64-apple-darwin-install_only_stripped.tar.gz',
        'f15ec3d15460dd66c2eb88639b152cd9040e7d1ece11e38bf89e8f53f0171e02',
    ),
    'python-mac-x64.tar.gz': (
        ASTRAL + 'cpython-3.14.10%2B20261110-x86_64-apple-darwin-install_only_stripped.tar.gz',
        '85f8e54798e8e6aa58d2253f013d815c5616343cc0d15cbea882fca78bdfeae2',
    ),
    'python-win-x64.zip': (
        PYTHON + 'python-3.13.17-embed-amd64.zip',
        '9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38',
    ),
    'kugo-v0.6.1.tar.gz': (
        'https://codeload.github.com/skxxxkx666/Kugo-Music-Converter/tar.gz/refs/tags/v0.6.1',
        'df0110f027ec749440d43dfa5a77d19cfef10aed04acf86d8934d476e1ee4bb1',
    ),
    'ffmpeg-mac-arm64.whl': (
        PYPI + '2b/47/9f9b654f853598bae0850fa22bba168103e45d31b5a2a893ed474f28d54a/'
        'imageio_ffmpeg-0.8.1-py3-none-macosx_12_0_arm64.whl',
        '5636d691e36e5274b13fe38182b1f627b2e35dcecc63944012c2470f9484fc45',
    ),
    'ffmpeg-mac-x64.whl': (
        PYPI + 'da/24/2c3a7c69453bfb8374ddbbb5007346bad4a4247bc86882f3521cd615f929/'
        'imageio_ffmpeg-0.8.1-py3-none-macosx_10_13_x86_64.whl',
        '9538a10e598257263b5de1a1c1b86e7603417bc219245b1e279b452e5e48cc20',
    ),
    'ffmpeg-win-x64.whl': (
        PYPI + '82/0c/a1d49f3dd2688bd750b4986f8e271ad11f8e729a896070ea25f587b44da8/'
        'imageio_ffmpeg-0.8.1-py3-none-win_amd64.whl',
        'f27ca4cb85e2d4946deedbce1a81245dfd942a2bcf94b53676fb3956aa95bf1b',
    ),
}
TARGETS = {
    'macos': ('python-mac-arm64.tar.gz', 'python-mac-x64.tar.gz',
              'ffmpeg-mac-arm64.whl', 'ffmpeg-mac-x64.whl'),
    'windows-x64': ('python-win-x64.zip', 'ffmpeg-win-x64.whl', 'kugo-v0.6.1.tar.gz'),
}


def verify_file(path, digest):
    sha = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1 << 20), b''):
            sha.update(block)
    if sha.hexdigest() != digest:
        raise ValueError(f'SHA-256 does not match the published value for {path.name}')


def pinned_file(filename):
    path = DOWNLOADS / filename
    verify_file(path, ARTIFACTS[filename][1])
    return path


def fetch(filename):
    address, digest = ARTIFACTS[filename]
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=DOWNLOADS, prefix='.fetch-', delete=False) as writer:
        temporary = Path(writer.name)
        try:
            with urlopen(address, timeout=60) as response:
                while block := response.read(1 << 20):
                    writer.write(block)
            writer.close()
            verify_file(temporary, digest)
            temporary.replace(DOWNLOADS / filename)
        finally:
            writer.close()
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=(*TARGETS, 'all'))
    parser.add_argument('--verify-only', action='store_true', help='check cached files without using the network')
    args = parser.parse_args()
    names = tuple(ARTIFACTS) if args.target == 'all' else TARGETS[args.target]
    for filename in names:
        try:
            pinned_file(filename)
        except (OSError, ValueError) as error:
            if args.verify_only:
                print(f'{filename}: {error}', file=sys.stderr)
                return 1
            print(f'Downloading {filename}', flush=True)
            try:
                fetch(filename)
            except (OSError, ValueError) as error:
                print(f'{filename}: {error}', file=sys.stderr)
                return 1
        print(f'Verified {filename}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
