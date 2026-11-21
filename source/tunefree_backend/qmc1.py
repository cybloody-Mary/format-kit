# -*- coding: utf-8 -*-
"""QMCv1（.qmcflac / .qmcogg / .qmc0 / .qmc3）：固定异或表，不需要 ekey。

老版 QQ音乐留下的格式。掩码由一个 8x7 的常量表按「之字形」走出来，
每走 0x8000 个状态额外跳一格（原实现是递归调用自己），所以不能简单按
0x80 取周期复制 —— 这里老老实实按原算法生成，实测约 8.5 MB/s，
一分钟以内的曲子够用。
"""
from __future__ import annotations

# 8 x 7 的掩码表，逐行照抄 C++ 参考实现的 seedMap
TABLE = (
    (0x4A, 0xD6, 0xCA, 0x90, 0x67, 0xF7, 0x52),
    (0x5E, 0x95, 0x23, 0x9F, 0x13, 0x11, 0x7E),
    (0x47, 0x74, 0x3D, 0x90, 0xAA, 0x3F, 0x51),
    (0xC6, 0x09, 0xD5, 0x9F, 0xFA, 0x66, 0xF9),
    (0xF3, 0xD6, 0xA1, 0x90, 0xA0, 0xF7, 0xF0),
    (0x1D, 0x95, 0xDE, 0x9F, 0x84, 0x11, 0xF4),
    (0x0E, 0x74, 0xBB, 0x90, 0xBC, 0x3F, 0x92),
    (0x00, 0x09, 0x5B, 0x9F, 0x62, 0x66, 0xA1),
)

_CACHE_LEN = 0
_CACHE = b""


def mask_stream(length):
    """生成前 length 个掩码字节（带进程内缓存，多文件复用）。"""
    global _CACHE_LEN, _CACHE
    if length <= _CACHE_LEN:
        return _CACHE[:length]

    out = bytearray(length)
    x, y, dx, index = -1, 8, 1, -1
    i = 0
    table = TABLE
    while i < length:
        index += 1
        if x < 0:
            dx = 1
            y = (8 - y) % 8
            mask = 0xC3
        elif x > 6:
            dx = -1
            y = 7 - y
            mask = 0xD8
        else:
            mask = table[y][x]
        x += dx
        # 与原实现的递归一致：这个状态不产出字节，直接跳到下一个
        if index == 0x8000 or (index > 0x8000 and (index + 1) % 0x8000 == 0):
            continue
        out[i] = mask
        i += 1

    _CACHE = bytes(out)
    _CACHE_LEN = length
    return _CACHE


class Qmc1Cipher(object):
    kind = "qmc1"

    def decrypt(self, data, offset=0):
        data = bytes(data)
        n = len(data)
        if not n:
            return b""
        stream = mask_stream(offset + n)[offset:]
        return (int.from_bytes(data, "big") ^ int.from_bytes(stream, "big")).to_bytes(n, "big")
