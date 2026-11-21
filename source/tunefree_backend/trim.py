# -*- coding: utf-8 -*-
"""去掉解密后音频末尾多出来的非音频字节。

有的 QQ音乐文件在音频和 musicex 尾部之间还夹着几十个字节（不是音频，作用不明），
解密时它们会跟着音频一起被还原、接在文件最后。播放器大多能忍，但 ffmpeg / 严格的
播放器会报「invalid sync code」，FLAC 的末尾也不再是一个完整的帧。

这里只在**能确认**的情况下裁：

* FLAC：按 STREAMINFO 算出最后一帧的帧号，在文件尾部找到这一帧，
  用帧自带的 CRC-16 找到它真正结束的位置；之后的字节就是多余的。
* OGG：顺着页头走到最后一个完整的页，之后不是 OggS 的字节就是多余的。

确认不了（可变块大小、最后一帧本身就不完整……）就原样不动。
"""
from __future__ import annotations

import struct

_TAIL_WINDOW = 1 << 20          # FLAC 最后一帧不会比这个大
MAX_JUNK = 4096                 # 实际见过的多余字节只有十几到几十个；超过这个数就不是这种情况，不碰


def _crc8(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _crc16_table():
    table = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x8005) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table.append(crc)
    return table


_CRC16 = _crc16_table()


def _utf8_number(data, pos):
    """FLAC 帧头里 UTF-8 风格编码的帧号；返回 (数值, 占用字节数)，非法返回 (None, 0)。"""
    first = data[pos]
    ones = 0
    while ones < 8 and first & (0x80 >> ones):
        ones += 1
    if ones == 0:
        return first, 1
    if ones == 1 or ones > 7:
        return None, 0
    value = first & (0xFF >> (ones + 1))
    for i in range(1, ones):
        if pos + i >= len(data) or data[pos + i] & 0xC0 != 0x80:
            return None, 0
        value = (value << 6) | (data[pos + i] & 0x3F)
    return value, ones


def _flac_header_length(data, pos):
    """校验帧头（含 CRC-8），返回帧头长度（不含 CRC-8 字节）；不像帧头返回 0。"""
    if pos + 6 > len(data) or data[pos] != 0xFF or data[pos + 1] != 0xF8:
        return 0
    block_code = data[pos + 2] >> 4
    rate_code = data[pos + 2] & 0x0F
    if block_code == 0 or rate_code == 0x0F:
        return 0
    _, n = _utf8_number(data, pos + 4)
    if not n:
        return 0
    length = 4 + n
    if block_code == 6:
        length += 1
    elif block_code == 7:
        length += 2
    if rate_code == 12:
        length += 1
    elif rate_code in (13, 14):
        length += 2
    if pos + length >= len(data) or _crc8(data[pos:pos + length]) != data[pos + length]:
        return 0
    return length


def flac_audio_end(data):
    """FLAC 真正的结尾位置；没有多余字节 / 确认不了时返回 None。"""
    if data[:4] != b"fLaC":
        return None
    pos = 4
    streaminfo = None
    while pos + 4 <= len(data):
        header = data[pos]
        length = int.from_bytes(data[pos + 1:pos + 4], "big")
        if header & 0x7F == 0:
            streaminfo = data[pos + 4:pos + 4 + length]
        pos += 4 + length
        if header & 0x80:
            break
    if not streaminfo or len(streaminfo) < 18:
        return None
    min_block, max_block = struct.unpack(">HH", streaminfo[0:4])
    total = ((streaminfo[13] & 0x0F) << 32) | int.from_bytes(streaminfo[14:18], "big")
    if min_block != max_block or not total or not max_block:
        return None                      # 可变块大小：帧号含义不同，不碰
    last_number = (total + max_block - 1) // max_block - 1

    window_start = max(pos, len(data) - _TAIL_WINDOW)
    start = data.rfind(b"\xff\xf8", window_start)
    while start >= window_start:
        if _flac_header_length(data, start):
            number, _ = _utf8_number(data, start + 4)
            if number == last_number:
                break
        start = data.rfind(b"\xff\xf8", window_start, start)
    else:
        return None
    if start < window_start:
        return None

    # 帧（含末尾的 CRC-16）整体算 CRC 结果为 0。
    # 取最后一个为 0 的位置：帧中间偶然算出 0 的位置会被后面真正的结尾覆盖掉。
    size = len(data)
    crc = 0
    zeros = []
    table = _CRC16
    for index in range(start, size):
        crc = ((crc << 8) & 0xFFFF) ^ table[(crc >> 8) ^ data[index]]
        if crc == 0 and index - start >= 8:
            zeros.append(index + 1)
    if not zeros:
        return None                      # 最后一帧本身不完整：不动
    last = zeros[-1]
    # 多余字节以 0 开头（或者全是 0）时，这些 0 不改变 CRC，要退回到 0 开始之前
    zero_set = set(zeros)
    end = last
    while (end - 1) in zero_set and data[end - 1] == 0 and end - 1 - start > 8:
        end -= 1
    if last < size:
        return end if size - end <= MAX_JUNK else None
    # 文件末尾就是 CRC 为 0 的位置：只有末尾真有一串 0（≥4 个）才裁，
    # 避免把恰好以 00 00 结尾的正常 CRC 当成多余字节
    if 4 <= size - end <= MAX_JUNK:
        return end
    return None                          # 本来就没有多余字节


def ogg_audio_end(data):
    """OGG 最后一个完整页的结尾；后面不是 OggS 的字节算多余。"""
    if data[:4] != b"OggS":
        return None
    pos = 0
    size = len(data)
    while pos + 27 <= size and data[pos:pos + 4] == b"OggS":
        segments = data[pos + 26]
        if pos + 27 + segments > size:
            break
        body = sum(data[pos + 27:pos + 27 + segments])
        end = pos + 27 + segments + body
        if end > size:
            break
        pos = end
    if pos == 0 or pos >= size or data[pos:pos + 4] == b"OggS" or size - pos > MAX_JUNK:
        return None
    return pos


def audio_end(data, fmt):
    """按格式找真正的音频结尾；不用裁时返回 None。"""
    try:
        if fmt == "flac":
            return flac_audio_end(data)
        if fmt == "ogg":
            return ogg_audio_end(data)
    except (IndexError, struct.error):
        return None
    return None
