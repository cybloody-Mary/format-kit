"""Build the Windows launcher icon object using Pillow and Clang."""

import argparse
from io import BytesIO
from pathlib import Path
import struct
import subprocess
from tempfile import TemporaryDirectory

from PIL import Image

SIZES = (16, 24, 32, 48, 64, 96, 128)


def directory(label, entries):
    lines = [f"{label}:", ".long 0,0", f".short 0,0,0,{len(entries)}"]
    for identifier, target, is_directory in entries:
        flag = " + 0x80000000" if is_directory else ""
        lines.extend((f".long {identifier}", f".long {target} - resource_base{flag}"))
    return lines


def build(source, target, clang):
    with Image.open(source) as cover:
        cover = cover.convert("RGBA")
        images = []
        for size in SIZES:
            data = BytesIO()
            cover.resize((size, size), Image.Resampling.LANCZOS).save(data, format="PNG")
            images.append(data.getvalue())
    icons = list(enumerate(zip(SIZES, images), 1))
    group = struct.pack("<HHH", 0, 1, len(icons))
    for identifier, (size, image_data) in icons:
        group += struct.pack("<BBBBHHIH", size, size, 0, 0, 1, 32, len(image_data), identifier)

    resources = [(f"icon_{identifier}", data) for identifier, (_, data) in icons]
    resources.append(("group_1", group))
    assembly = ['.section .rsrc$01,"dr"']
    assembly += directory("resource_base", [(3, "icons", True), (14, "groups", True)])
    assembly += directory("icons", [(identifier, f"icon_{identifier}_lang", True)
                                    for identifier, _ in icons])
    assembly += directory("groups", [(1, "group_1_lang", True)])
    for name, _ in resources:
        assembly += directory(name + "_lang", [(0, name + "_entry", False)])
    for name, data in resources:
        assembly += [f"{name}_entry:", f".rva {name}_data", f".long {len(data)},0,0"]
    assembly.append('.section .rsrc$02,"dr"')
    with TemporaryDirectory() as directory_path:
        temp = Path(directory_path)
        for name, data in resources:
            (temp / name).write_bytes(data)
            assembly += [".p2align 2", f"{name}_data:", f'.incbin "{name}"']
        (temp / "icon.s").write_text("\n".join(assembly) + "\n", encoding="ascii")
        subprocess.run([clang, "-target", "x86_64-pc-windows-msvc", "-c", "icon.s", "-o", str(target)],
                       cwd=temp, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cover", type=Path)
    parser.add_argument("object", type=Path)
    parser.add_argument("--clang", default="clang")
    args = parser.parse_args()
    build(args.cover, args.object.resolve(), args.clang)
