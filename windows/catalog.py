"""Read the local QQ Music download directory without opening encrypted audio."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import os
import re
import stat

COPY_EXTENSIONS = {
    "mflac": {"flac"}, "mgg": {"ogg"}, "qmcflac": {"flac"}, "qmcogg": {"ogg"},
    "qmc0": {"mp3"}, "qmc2": {"ogg", "mp3"}, "qmc3": {"mp3"}, "qmc4": {"mp3"},
    "qmc6": {"mp3", "ogg", "flac", "m4a"},
    "qmc8": {"mp3", "ogg", "flac", "m4a"}, "tkm": {"mp3", "ogg", "flac", "m4a"},
}
SEPARATOR = re.compile(r"\s+[\-–—]\s+|\s*[－]\s*")
UNKNOWN_ARTIST = "未知歌手"


def is_link(info):
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & 0x400)


def scan_songs(root: Path):
    if not root.is_dir():
        raise ValueError("下载目录不存在或无法访问。")

    def fail(error):
        raise error

    result = []
    for directory, folders, names in os.walk(root, onerror=fail):
        parent = Path(directory)
        folders[:] = sorted(
            name for name in folders if not is_link((parent / name).lstat())
        )
        for name in sorted(names):
            source = parent / name
            extension = source.suffix[1:].lower()
            if extension not in COPY_EXTENSIONS:
                continue
            try:
                info = source.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode) or is_link(info) or not info.st_size:
                continue
            relative = source.relative_to(root).as_posix()
            parts = SEPARATOR.split(source.stem, maxsplit=1)
            artist, title = (parts if len(parts) == 2 else (UNKNOWN_ARTIST, parts[0]))
            quality = ("无损" if extension in {"mflac", "qmcflac"} else
                       "有损" if extension in {"mgg", "qmcogg", "qmc0", "qmc3", "qmc4"} else
                       "未知")
            result.append({
                "id": sha256(relative.encode("utf-8", errors="surrogatepass")).hexdigest(),
                "title": title.strip() or source.stem,
                "artist": artist.strip() or UNKNOWN_ARTIST,
                "format": extension,
                "quality": quality,
                "path": relative,
                "size": info.st_size,
                "modified": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
            })
    return result
