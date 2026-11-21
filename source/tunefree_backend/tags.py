# -*- coding: utf-8 -*-
"""直接解析 FLAC / OGG 里的标签和时长（不依赖 ffprobe —— 打包版里没有 ffprobe）。

只读解密出来的源文件，给后面的「合并标签 / 联网校验」用。返回统一的小写键：

    title / artist / album / albumartist / date / track / disc / genre / lyrics

OGG 的标签在流级别（ffmpeg 的 -map_metadata 0 只拷容器级别），以前转 MP3 时专辑会丢，
这里统一读出来再显式写入。QQ音乐在 OGG 里塞的 end / endserial / endgran 之类私有字段会被丢掉。
"""
from __future__ import annotations

import os
import struct

# Vorbis comment 键 → 统一键
_KEY_MAP = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "albumartist": "albumartist",
    "album artist": "albumartist",
    "album_artist": "albumartist",
    "date": "date",
    "year": "date",
    "tracknumber": "track",
    "track": "track",
    "discnumber": "disc",
    "disc": "disc",
    "genre": "genre",
    "lyrics": "lyrics",
    "unsyncedlyrics": "lyrics",
    "organization": "publisher",
    "label": "publisher",
    "publisher": "publisher",
    "comment": "comment",
}

_MAX_HEAD = 4 * 1024 * 1024      # 标签都在文件头，读 4 MB 足够（带封面的 FLAC 也够）
_TAIL = 128 * 1024               # OGG 时长看最后一页的 granule


class SourceInfo(object):
    __slots__ = ("tags", "duration", "has_picture")

    def __init__(self, tags=None, duration=None, has_picture=False):
        self.tags = tags or {}
        self.duration = duration
        self.has_picture = has_picture

    def __repr__(self):
        return "SourceInfo(%r, duration=%r, picture=%r)" % (self.tags, self.duration, self.has_picture)


def read_source(path):
    """读 FLAC / OGG 的标签与时长；读不了或不认识的格式返回空的 SourceInfo。"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(_MAX_HEAD)
            size = os.fstat(fh.fileno()).st_size
            tail = b""
            if head[:4] == b"OggS":
                fh.seek(max(0, size - _TAIL))
                tail = fh.read()
    except OSError:
        return SourceInfo()
    try:
        if head[:4] == b"fLaC":
            return _read_flac(head)
        if head[:4] == b"OggS":
            return _read_ogg(head, tail)
    except (ValueError, IndexError, struct.error):
        pass
    return SourceInfo()


# ------------------------------------------------------------------ FLAC ----

def _read_flac(data):
    pos = 4
    tags = {}
    duration = None
    picture = False
    while pos + 4 <= len(data):
        header = data[pos]
        last = bool(header & 0x80)
        kind = header & 0x7F
        length = int.from_bytes(data[pos + 1:pos + 4], "big")
        body = data[pos + 4:pos + 4 + length]
        if kind == 0 and len(body) >= 18:          # STREAMINFO
            rate = (body[10] << 12) | (body[11] << 4) | (body[12] >> 4)
            total = ((body[13] & 0x0F) << 32) | int.from_bytes(body[14:18], "big")
            if rate and total:
                duration = total / float(rate)
        elif kind == 4:                            # VORBIS_COMMENT
            tags = _parse_vorbis_comment(body)
        elif kind == 6:                            # PICTURE
            picture = True
        pos += 4 + length
        if last:
            break
    return SourceInfo(tags, duration, picture)


# ------------------------------------------------------------------- OGG ----

def _ogg_packets(data, limit=3):
    """按 lacing 把前几个包拼出来（注释头常常跨页）。"""
    packets = []
    current = b""
    pos = 0
    while pos + 27 <= len(data) and len(packets) < limit:
        if data[pos:pos + 4] != b"OggS":
            break
        segments = data[pos + 26]
        table = data[pos + 27:pos + 27 + segments]
        offset = pos + 27 + segments
        for lace in table:
            current += data[offset:offset + lace]
            offset += lace
            if lace < 255:
                packets.append(current)
                current = b""
                if len(packets) >= limit:
                    break
        pos = offset
    return packets


def _last_granule(tail):
    index = tail.rfind(b"OggS")
    while index >= 0:
        if index + 14 <= len(tail):
            granule = struct.unpack_from("<q", tail, index + 6)[0]
            if granule > 0:
                return granule
        index = tail.rfind(b"OggS", 0, index)
    return None


def _read_ogg(head, tail):
    packets = _ogg_packets(head)
    if len(packets) < 2:
        return SourceInfo()
    ident, comment = packets[0], packets[1]
    rate = None
    tags = {}
    pre_skip = 0
    if ident[:7] == b"\x01vorbis" and comment[:7] == b"\x03vorbis":
        rate = struct.unpack_from("<I", ident, 12)[0]
        tags = _parse_vorbis_comment(comment[7:])
    elif ident[:8] == b"OpusHead" and comment[:8] == b"OpusTags":
        rate = 48000
        pre_skip = struct.unpack_from("<H", ident, 10)[0]
        tags = _parse_vorbis_comment(comment[8:])
    duration = None
    granule = _last_granule(tail)
    if rate and granule:
        duration = max(0, granule - pre_skip) / float(rate)
    picture = "metadata_block_picture" in tags
    tags.pop("metadata_block_picture", None)
    return SourceInfo(tags, duration, picture)


# ------------------------------------------------------- Vorbis comment ----

def _parse_vorbis_comment(body):
    pos = 0
    vendor_len = struct.unpack_from("<I", body, pos)[0]
    pos += 4 + vendor_len
    count = struct.unpack_from("<I", body, pos)[0]
    pos += 4
    raw = {}
    for _ in range(min(count, 1000)):
        length = struct.unpack_from("<I", body, pos)[0]
        pos += 4
        entry = body[pos:pos + length].decode("utf-8", "replace")
        pos += length
        key, sep, value = entry.partition("=")
        if not sep:
            continue
        key = key.strip().lower()
        value = value.strip()
        if key == "metadata_block_picture":
            raw[key] = ["1"]
            continue
        if key in _KEY_MAP and value:
            raw.setdefault(_KEY_MAP[key], []).append(value)
    tags = {}
    for key, values in raw.items():
        if key == "artist":
            tags[key] = "/".join(dict.fromkeys(values))   # 多个 ARTIST 合并，去重保序
        else:
            tags[key] = values[0]
    if "track" in tags:
        tags["track"] = tags["track"].split("/")[0].strip()
    return tags
