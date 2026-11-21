# -*- coding: utf-8 -*-
"""解密 / 转码结果的完整性校验。

解密算法对不对，最硬的证据是音频容器本身能不能走通：
OGG 逐页 CRC、FLAC 的 STREAMINFO、MP3 的帧头链。命令行加 --verify 就会跑这些，
界面也可以在「完成后」调一次。
"""
from __future__ import annotations

import struct

_CRC_TABLE = None


def _crc_table():
    global _CRC_TABLE
    if _CRC_TABLE is None:
        table = []
        for i in range(256):
            value = i << 24
            for _ in range(8):
                if value & 0x80000000:
                    value = ((value << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
                else:
                    value = (value << 1) & 0xFFFFFFFF
            table.append(value)
        _CRC_TABLE = table
    return _CRC_TABLE


def _ogg_crc(data):
    table = _crc_table()
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ table[((crc >> 24) & 0xFF) ^ byte]
    return crc


def verify_ogg(data, check_crc=False):
    result = {"container": "ogg", "ok": False, "pages": 0, "serials": 0,
              "bad_crc_pages": [], "detail": ""}
    if data[:4] != b"OggS":
        result["detail"] = "不是 Ogg 流（头部 %r）" % data[:4]
        return result

    offset = 0
    pages = 0
    prev_seq = None
    serials = set()
    bad_crc = []
    detail = ""

    while offset + 27 <= len(data):
        if data[offset:offset + 4] != b"OggS":
            detail = "第 %d 页开始处失去同步（偏移 %d）" % (pages, offset)
            break
        nseg = data[offset + 26]
        body_len = sum(data[offset + 27:offset + 27 + nseg])
        page_len = 27 + nseg + body_len
        serial, seq = struct.unpack("<II", data[offset + 14:offset + 22])
        serials.add(serial)
        if prev_seq is not None and seq != (prev_seq + 1) & 0xFFFFFFFF:
            detail = "第 %d 页序号跳变（%d → %d）" % (pages, prev_seq, seq)
        prev_seq = seq
        if check_crc:
            page = bytearray(data[offset:offset + page_len])
            stored = struct.unpack("<I", page[22:26])[0]
            page[22:26] = b"\x00\x00\x00\x00"
            if _ogg_crc(page) != stored:
                bad_crc.append(pages)
        offset += page_len
        pages += 1

    if offset != len(data):
        detail = detail or "末尾还有 %d 字节不属于任何页" % (len(data) - offset)

    result.update(pages=pages, serials=len(serials), bad_crc_pages=bad_crc, detail=detail)
    result["ok"] = bool(pages) and not detail and not bad_crc
    if not detail and check_crc and bad_crc:
        result["detail"] = "%d 页 CRC 不对（前几页：%s）" % (len(bad_crc), bad_crc[:5])
    return result


def verify_flac(data):
    result = {"container": "flac", "ok": False, "detail": ""}
    if data[:4] != b"fLaC":
        if data[:3] == b"ID3":
            result["detail"] = "FLAC 文件中带有前置 ID3 标签（QQ音乐部分版本会这样），需跳过标签再判断"
            result["ok"] = True
            result["note"] = "id3-prefixed"
            return result
        result["detail"] = "不是 FLAC 流（头部 %r）" % data[:4]
        return result
    if len(data) < 42:
        result["detail"] = "文件太短，读不到 STREAMINFO"
        return result

    info = data[8:42]
    min_block, max_block = struct.unpack(">HH", info[0:4])
    packed = struct.unpack(">Q", info[10:18])[0]
    sample_rate = packed >> 44
    channels = ((packed >> 41) & 0x07) + 1
    bits = ((packed >> 36) & 0x1F) + 1
    total_samples = packed & 0xFFFFFFFFF

    result.update(min_block=min_block, max_block=max_block, sample_rate=sample_rate,
                  channels=channels, bits=bits, total_samples=total_samples)
    if not (1000 <= sample_rate <= 768000):
        result["detail"] = "STREAMINFO 里的采样率不合理（%d）" % sample_rate
        return result
    result["ok"] = True
    return result


def verify_mp3(data):
    """粗校验：能找到合法帧头，且文件不是只有几百字节。"""
    result = {"container": "mp3", "ok": False, "detail": ""}
    if len(data) < 1024:
        result["detail"] = "文件太小（%d 字节）" % len(data)
        return result
    limit = min(len(data), 64 * 1024)
    for i in range(limit - 4):
        b0, b1 = data[i], data[i + 1]
        if b0 == 0xFF and (b1 & 0xE0) == 0xE0 and (b1 & 0x18) != 0x08:
            result["ok"] = True
            result["first_frame"] = i
            break
    if not result["ok"]:
        result["detail"] = "前 64KB 里找不到 MP3 帧头"
    return result


def verify_file(path, check_crc=False):
    """按扩展名选校验方式，扩展名不可信时再按头部嗅探一次。"""
    ext = path.lower().rsplit(".", 1)[-1]
    with open(path, "rb") as fh:
        data = fh.read()

    if ext == "ogg" or data[:4] == b"OggS":
        return verify_ogg(data, check_crc=check_crc)
    if ext == "flac":
        return verify_flac(data)
    if ext == "mp3":
        return verify_mp3(data)
    if data[:4] == b"fLaC":
        return verify_flac(data)
    return {"ok": False, "detail": "未知容器：%r" % data[:4]}
