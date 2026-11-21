"""Cross-compile the portable Windows entry point and its Explorer icon."""

from pathlib import Path
import shutil
import struct
import subprocess

SOURCE = Path(__file__).resolve().parent
TARGETS = {'x64': 'x86_64-windows-gnu', 'arm64': 'aarch64-windows-gnu'}
ICON_HEADER = struct.Struct('<HHH')
ICON_ENTRY = struct.Struct('<BBBBHHII')
GROUP_ENTRY = struct.Struct('<BBBBHHIH')


def resource_record(kind, index, data):
    # Win32 .res uses numeric identifiers preceded by 0xFFFF and DWORD padding.
    header = struct.pack('<IIHHHHIHHII', len(data), 32, 0xFFFF, kind, 0xFFFF, index,
                         0, 0, 0, 0, 0)
    return header + data + bytes(-len(data) % 4)


def make_icon_resource(icon, destination):
    data = icon.read_bytes()
    reserved, image_type, count = ICON_HEADER.unpack_from(data)
    if (reserved, image_type) != (0, 1) or count == 0:
        raise ValueError(f'Invalid ICO: {icon}')

    # Replace the file offset in each ICO entry with the matching RT_ICON ID.
    entries = bytearray(data[:ICON_HEADER.size])
    with destination.open('wb') as resource:
        resource.write(resource_record(0, 0, b''))
        for index in range(1, count + 1):
            entry = ICON_ENTRY.unpack_from(data, ICON_HEADER.size + (index - 1) * ICON_ENTRY.size)
            size, offset = entry[-2:]
            image = data[offset:offset + size]
            if not size or len(image) != size:
                raise ValueError(f'Truncated ICO frame: {icon}')
            resource.write(resource_record(3, index, image))
            entries.extend(GROUP_ENTRY.pack(*entry[:-1], index))
        resource.write(resource_record(14, 1, entries))


def build_windows_launcher(arch, destination, staging, zig=None):
    compiler = zig or shutil.which('zig')
    if compiler is None:
        raise ValueError('Zig is required to build Start.exe. Install Zig 0.15.2 or use --zig.')
    resource = staging / 'app-icon.res'
    make_icon_resource(SOURCE / 'windows_app_icon.ico', resource)
    subprocess.run(
        [str(Path(compiler).resolve()), 'cc', '-target', TARGETS[arch], '-Oz',
         '-Wl,--subsystem,windows', str(SOURCE / 'windows_launcher.c'), str(resource),
         '-lkernel32', '-luser32', '-o', str(destination)],
        check=True,
    )
