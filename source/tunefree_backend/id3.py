# -*- coding: utf-8 -*-
"""往 ffmpeg 写好的 ID3v2.3 标签里补一个 USLT（歌词）帧。

ffmpeg 会把 lyrics 写成 TXXX 自定义帧，Apple 音乐、车机等都不认；标准的是 USLT。
这里只做「追加一帧」：读原标签、把新帧放在最前面、重写标签长度，音频数据原样拷贝。
"""
from __future__ import annotations

import os
import struct


class ID3Error(Exception):
    pass


def _syncsafe_decode(raw):
    return (raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]


def _syncsafe_encode(value):
    return bytes([(value >> 21) & 0x7F, (value >> 14) & 0x7F, (value >> 7) & 0x7F, value & 0x7F])


def _text16(text):
    return text.encode("utf-16")          # 带 BOM


def uslt_frame(lyrics, lang="chi", description=""):
    body = b"\x01" + lang.encode("ascii")[:3].ljust(3, b"X") + _text16(description) + b"\x00\x00" + _text16(lyrics)
    return b"USLT" + struct.pack(">I", len(body)) + b"\x00\x00" + body


def add_lyrics(path, lyrics, lang="chi"):
    """给 MP3 加 USLT 歌词帧（要求文件开头已有 ID3v2.3 标签，ffmpeg -id3v2_version 3 会写）。"""
    if not lyrics:
        return False
    with open(path, "rb") as fh:
        header = fh.read(10)
        if len(header) < 10 or header[:3] != b"ID3":
            raise ID3Error("没有 ID3v2 标签")
        if header[3] != 3:
            raise ID3Error("只处理 ID3v2.3（实际 v2.%d）" % header[3])
        flags = header[5]
        if flags & 0x80 or flags & 0x40:
            raise ID3Error("不处理带 unsync / 扩展头的标签")
        size = _syncsafe_decode(header[6:10])
        body = fh.read(size)
        audio = fh.read()
    frames = _drop_frames(body, {b"USLT"})
    new_body = uslt_frame(lyrics, lang) + frames
    tmp = path + ".id3tmp"
    with open(tmp, "wb") as out:
        out.write(b"ID3\x03\x00\x00" + _syncsafe_encode(len(new_body)))
        out.write(new_body)
        out.write(audio)
    os.replace(tmp, path)
    return True


def _drop_frames(body, ids):
    """去掉指定 id 的帧（重复跑时不叠加），保留其余帧和末尾的 padding。"""
    out = bytearray()
    pos = 0
    while pos + 10 <= len(body):
        frame_id = body[pos:pos + 4]
        if frame_id == b"\x00\x00\x00\x00":
            out += body[pos:]          # padding
            return bytes(out)
        length = struct.unpack(">I", body[pos + 4:pos + 8])[0]
        end = pos + 10 + length
        if end > len(body):
            out += body[pos:]
            return bytes(out)
        if frame_id not in ids:
            out += body[pos:end]
        pos = end
    out += body[pos:]
    return bytes(out)


def read_frames(path):
    """测试 / 排查用：列出 ID3v2.3 帧 id。"""
    with open(path, "rb") as fh:
        header = fh.read(10)
        if header[:3] != b"ID3":
            return []
        body = fh.read(_syncsafe_decode(header[6:10]))
    ids = []
    pos = 0
    while pos + 10 <= len(body) and body[pos:pos + 4] != b"\x00\x00\x00\x00":
        length = struct.unpack(">I", body[pos + 4:pos + 8])[0]
        ids.append(body[pos:pos + 4].decode("latin-1"))
        pos += 10 + length
    return ids


def read_uslt(path):
    """测试用：读回第一个 USLT 帧的歌词文本。"""
    with open(path, "rb") as fh:
        header = fh.read(10)
        body = fh.read(_syncsafe_decode(header[6:10]))
    pos = 0
    while pos + 10 <= len(body):
        frame_id = body[pos:pos + 4]
        length = struct.unpack(">I", body[pos + 4:pos + 8])[0]
        if frame_id == b"USLT":
            data = body[pos + 10:pos + 10 + length]
            rest = data[4:]
            # 跳过描述（UTF-16，以 00 00 结尾，按 2 字节对齐找）
            i = 0
            while i + 1 < len(rest) and rest[i:i + 2] != b"\x00\x00":
                i += 2
            return rest[i + 2:].decode("utf-16")
        if frame_id == b"\x00\x00\x00\x00":
            break
        pos += 10 + length
    return None
