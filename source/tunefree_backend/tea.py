# -*- coding: utf-8 -*-
"""TC-TEA：腾讯改过的 TEA 分组密码，QQ音乐用它保护 ekey。

和标准 TEA 的区别（照着 tc_tea / jixunmoe 的参考实现搬的）：

* 16 轮，delta 仍是 0x9E3779B9，但 u32 按 **大端** 读写；
* 分组本身是 ECB，外面套了一层链式：``P_i = D(C_i ^ P_{i-1}) ^ C_{i-1}``
  （等价于「前一块明文参与异或」+「再异或一次偏移 8 的原始密文」）；
* 明文带结构：``PadLen(1B) | Padding(0-7B) | Salt(2B) | Body | Zero(7B)``，
  PadLen 只有低 3 bit 有效，末尾 7 字节必须是 0 —— 用来判断解密「是否成功」。

本模块同时提供 encrypt（加密侧），主要给单元测试造样本用。
"""
from __future__ import annotations

import struct

DELTA = 0x9E3779B9
ROUNDS = 16
_M32 = 0xFFFFFFFF

# simple_make_key(106, 8)：100 * |tan(106 + 0.1 * i)| 取整后的 8 个字节。
# 值写死在这里，避免不同平台 math.tan 末位差异带来麻烦。
SIMPLE_KEY_106 = bytes([0x69, 0x56, 0x46, 0x38, 0x2B, 0x20, 0x15, 0x0B])


def _mx(value, total, k1, k2):
    return (
        ((((value << 4) & _M32) + k1) & _M32)
        ^ ((total + value) & _M32)
        ^ (((value >> 5) + k2) & _M32)
    )


def _decrypt_block(block, k):
    y, z = struct.unpack(">II", block)
    total = (DELTA * ROUNDS) & _M32
    for _ in range(ROUNDS):
        z = (z - _mx(y, total, k[2], k[3])) & _M32
        y = (y - _mx(z, total, k[0], k[1])) & _M32
        total = (total - DELTA) & _M32
    return struct.pack(">II", y, z)


def _encrypt_block(block, k):
    y, z = struct.unpack(">II", block)
    total = 0
    for _ in range(ROUNDS):
        total = (total + DELTA) & _M32
        y = (y + _mx(z, total, k[0], k[1])) & _M32
        z = (z + _mx(y, total, k[2], k[3])) & _M32
    return struct.pack(">II", y, z)


def decrypt(data, key):
    """解一段 TC-TEA 密文，结构不符返回 None（调用方据此判断格式）。"""
    if len(key) < 16 or len(data) < 16 or len(data) % 8:
        return None

    k = struct.unpack(">IIII", key[:16])
    n = len(data)
    buf = bytearray(data)

    # 第 0 块直接 ECB
    buf[0:8] = _decrypt_block(bytes(data[0:8]), k)
    # 之后每块先异或前一块明文，再 ECB
    for i in range(8, n, 8):
        for j in range(8):
            buf[i + j] ^= buf[i - 8 + j]
        buf[i:i + 8] = _decrypt_block(bytes(buf[i:i + 8]), k)
    # 再整体异或一次「偏移 8 的原始密文」
    for i in range(8, n):
        buf[i] ^= data[i - 8]

    pad = buf[0] & 0x07
    start = 1 + pad + 2
    end = n - 7
    if end <= start:
        return None
    if any(buf[end:]):
        return None
    return bytes(buf[start:end])


def encrypt(plain, key, salt=b"\x00\x00"):
    """把明文封成 TC-TEA 密文（测试/造样本用）。"""
    if len(key) < 16:
        raise ValueError("key 至少 16 字节")

    body = bytes(plain)
    if not body:
        raise ValueError("明文不能为空：TC-TEA 的填充结构至少要 1 字节")
    pad = (-(len(body) + 10)) % 8
    frame = bytes([pad]) + b"\x00" * pad + bytes(salt) + body + b"\x00" * 7
    if len(frame) % 8:
        raise AssertionError("封装后长度不是 8 的倍数")

    k = struct.unpack(">IIII", key[:16])
    out = []
    prev_cipher = None
    prev_plain = None
    for i in range(0, len(frame), 8):
        p = frame[i:i + 8]
        if i == 0:
            x = p
        else:
            x = bytes(a ^ b for a, b in zip(p, prev_cipher))
        c = _encrypt_block(x, k)
        if i != 0:
            c = bytes(a ^ b for a, b in zip(c, prev_plain))
        out.append(c)
        prev_cipher, prev_plain = c, x
    return b"".join(out)


def derive_tea_key(ekey_header):
    """EncV1 的 TEA 密钥：8 字节 header 与 SIMPLE_KEY_106 交错。"""
    header = bytes(ekey_header)
    if len(header) < 8:
        raise ValueError("header 需要 8 字节")
    tk = bytearray(16)
    for i in range(0, 16, 2):
        tk[i] = SIMPLE_KEY_106[i // 2]
        tk[i + 1] = header[i // 2]
    return bytes(tk)
