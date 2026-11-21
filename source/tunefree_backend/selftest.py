# -*- coding: utf-8 -*-
"""自检：不依赖任何真实样本，也能证明这套实现是对的。

用到的期望值来自两处独立参考实现的公开测试向量（Rust 版 tc_tea / qmc-decoder），
外加「自己加密 → 自己解密 → 比对」的端到端闭环。

命令行：``python main.py --selftest``
"""
from __future__ import annotations

import base64
import struct

from . import audio, decrypt, ekey, qmc1, qmc2, tea


def _v_tea():
    """官方测试向量：这个 ekey 必须派生出 'This is a test key for test purpose :D'。"""
    ekey = "VGhpcyBpcyBHFWEh4cjZ1Vi7rJ56XeoPlqGM1sxBGPg7mt89umKclFBr9iqfmFdS"
    got = qmc2.parse_ekey(ekey)
    want = b"This is a test key for test purpose :D"
    return got == want, "ekey 派生 → %r" % got


def _v_tea_roundtrip():
    key = tea.derive_tea_key(b"12345678")
    payloads = (b"x", b"hello tc-tea world", b"\x00" * 32, b"a" * 7, b"b" * 8)
    for payload in payloads:
        enc = tea.encrypt(payload, key)
        dec = tea.decrypt(enc, key)
        if dec != payload:
            return False, "round-trip 失败：%r" % payload
    if len(tea.encrypt(b"hello tc-tea world", key)) % 8:
        return False, "密文长度不是 8 的倍数"
    return True, "TC-TEA 加解密闭环 %d 组通过" % len(payloads)


def _v_map():
    key = bytes(range(0x41, 0x51))
    cipher = qmc2.MapCipher(key)
    got = list(cipher.decrypt(bytes(16), 0))
    want = [0x3F, 0x8A, 0xC1, 0x49, 0x3F, 0x49, 0xC1, 0x8A,
            0x3F, 0x8A, 0xC1, 0x49, 0x3F, 0x49, 0xC1, 0x8A]
    if got != want:
        return False, "偏移 0 处不符：%s" % got
    got2 = list(cipher.decrypt(bytes(16), 0x7FFF - 8))
    want2 = [0x8A, 0x3F, 0x8A, 0xC1, 0x49, 0x3F, 0x49, 0xC1,
             0x8A, 0x8A, 0xC1, 0x49, 0x3F, 0x49, 0xC1, 0x8A]
    if got2 != want2:
        return False, "0x7FFF 边界处不符：%s" % got2
    return True, "Map 密码两组向量（含 0x7FFF 边界）通过"


def _v_map_roundtrip():
    """大文件长距离异或：验证掩码流拼接与偏移一致。"""
    key = bytes(range(1, 41)) * 3
    cipher = qmc2.MapCipher(key)
    payload = bytes((i * 37 + 11) & 0xFF for i in range(200000))
    # 用密码本身当加密（异或对称），分段解一次比一次解到底
    whole = cipher.decrypt(payload)
    chunked = b"".join(cipher.decrypt(payload[i:i + 7000], offset=i)
                       for i in range(0, len(payload), 7000))
    if whole != chunked:
        return False, "分段解密与整段解密结果不一致"
    if cipher.decrypt(whole) != payload:
        return False, "两次异或没有还原原文"
    return True, "200KB 分段/整段解密一致"


def _v_rc4():
    key = bytes(range(255))
    cipher = qmc2.Rc4Cipher(key)
    got = list(cipher.decrypt(bytes(16), 0))
    want = [0, 50, 16, 8, 5, 3, 2, 1, 1, 1, 0, 0, 0, 0, 0, 0]
    if got != want:
        return False, "首段不符：%s" % got
    h1 = qmc2._hash_base([1, 99])
    h2 = qmc2._hash_base(bytes([0xFF] * 16))
    if h1 != 1 or h2 != 0xFC05FC01:
        return False, "hash base 不符：%x %x" % (h1, h2)
    # 长文件分段一致性
    payload = bytes((i * 13 + 7) & 0xFF for i in range(300000))
    whole = cipher.decrypt(payload)
    piece = b"".join(cipher.decrypt(payload[i:i + 4096], offset=i)
                     for i in range(0, len(payload), 4096))
    if whole != piece:
        return False, "RC4 分段解密不一致"
    if cipher.decrypt(whole) != payload:
        return False, "RC4 两次解密没有还原"
    return True, "RC4 首段向量 + hash base + 300KB 分段一致"


def _v_qmc1():
    head = qmc1.mask_stream(9)
    want = bytes([0xC3, 0x4A, 0xD6, 0xCA, 0x90, 0x67, 0xF7, 0x52, 0xD8])
    if head != want:
        return False, "掩码流头部不符：%s" % head.hex()
    payload = bytes((i * 7 + 3) & 0xFF for i in range(200000))
    cipher = qmc1.Qmc1Cipher()
    enc = cipher.decrypt(payload)
    if cipher.decrypt(enc) != payload:
        return False, "QMC1 往返失败"
    return True, "QMC1 掩码流 + 200KB 往返通过"


def _build_qtag_file(audio_bytes, ekey_text, song_id="123456"):
    meta = ("%s,%s,2" % (ekey_text, song_id)).encode("ascii")
    return audio_bytes + meta + struct.pack(">I", len(meta)) + b"QTag"


def _make_ekey(key, encv2=False):
    """把裸 key 封装成 QQ音乐文件里那种 ekey 字符串（EncV1 / EncV2 两种都行）。"""
    header, body = key[:8], key[8:]
    encv1 = header + tea.encrypt(body, tea.derive_tea_key(header)) if body else key
    if not encv2:
        return base64.b64encode(encv1).decode("ascii")
    stage2 = base64.b64encode(encv1)
    stage1 = tea.encrypt(stage2, qmc2.ENCV2_STAGE2_KEY)
    wrapped = tea.encrypt(stage1, qmc2.ENCV2_STAGE1_KEY)
    return base64.b64encode(qmc2.ENCV2_PREFIX + wrapped).decode("ascii")


def _v_end_to_end_qtag():
    payload = b"OggS" + bytes((i * 31 + 5) & 0xFF for i in range(150000))
    key = bytes((i * 17 + 3) & 0xFF for i in range(64))
    cipher = qmc2.MapCipher(key)
    encrypted = cipher.decrypt(payload)          # 异或对称，当作加密
    ekey = _make_ekey(key)
    blob = _build_qtag_file(encrypted, ekey)

    plain, footer, used = qmc2.decrypt_qmc2(blob, expect_ext="ogg")
    if footer.kind != "qtag" or footer.audio_len != len(encrypted):
        return False, "尾部解析不对：%r" % (footer,)
    if plain != payload:
        return False, "解密出的音频与原文件不一致"
    if used.kind != "map":
        return False, "密码选择不对：%s" % used.kind
    return True, "QTag 布局端到端（EncV1 ekey，%d 字节）" % len(plain)


def _v_end_to_end_encv2_longkey():
    payload = b"fLaC" + bytes((i * 91 + 13) & 0xFF for i in range(120000))
    key = bytes((i * 29 + 7) & 0xFF for i in range(320))   # >300 → RC4
    cipher = qmc2.Rc4Cipher(key)
    encrypted = cipher.decrypt(payload)
    ekey = _make_ekey(key, encv2=True)
    blob = _build_qtag_file(encrypted, ekey, song_id="987654")

    plain, footer, used = qmc2.decrypt_qmc2(blob, expect_ext="flac")
    if plain != payload:
        return False, "RC4 + EncV2 解密结果不一致"
    if used.kind != "rc4":
        return False, "密码选择不对：%s" % used.kind
    return True, "QTag + EncV2 双层 ekey + RC4 端到端（320 字节 key）"


def _v_end_to_end_v1():
    payload = b"OggS" + bytes((i * 53 + 9) & 0xFF for i in range(50000))
    key = bytes((i * 11 + 1) & 0xFF for i in range(32))
    encrypted = qmc2.MapCipher(key).decrypt(payload)
    blob = encrypted + key + struct.pack("<I", len(key))

    footer = qmc2.detect_footer(blob)
    if footer.kind != "v1":
        return False, "V1 尾部没识别出来：%s" % footer.kind
    plain, _, _ = qmc2.decrypt_qmc2(blob)
    if plain != payload:
        return False, "V1 解密结果不一致"
    return True, "V1 布局（裸 key 尾部）端到端"


def _v_musicex_needs_ekey():
    payload = b"OggS" + bytes(2000)
    meta = bytes(176)
    footer_size = 176 + 16
    blob = payload + meta + struct.pack("<II", footer_size, 1) + b"musicex\x00"
    footer = qmc2.detect_footer(blob)
    if footer.kind != "musicex":
        return False, "musicex 尾部没识别出来：%s" % footer.kind
    try:
        qmc2.decrypt_qmc2(blob, auto_ekey=False)
    except qmc2.Qmc2Error as exc:
        if "ekey" not in str(exc):
            return False, "报错信息没提示 ekey：%s" % exc
        return True, "musicex 无 ekey 时给出明确提示"
    return False, "musicex 缺 ekey 却没报错"


def _v_ext_map():
    if decrypt.output_extension("a.MFLAC") != "flac":
        return False, ".MFLAC（大写）没识别"
    if decrypt.output_extension("b.mgg") != "ogg":
        return False, ".mgg 没识别"
    if decrypt.output_extension("c.qmcflac") != "flac":
        return False, ".qmcflac 没识别"
    if decrypt.output_extension("d.txt") is not None:
        return False, ".txt 不该被当成加密文件"
    return True, "扩展名映射（含大小写）正确"


def _v_ekey_credentials():
    """musicex 文件要现取密钥：本机能不能读到 QQ音乐登录态。

    没登录不算失败（仍可用 --ekey 手动给），这里只报状态。
    """
    try:
        creds = ekey.get_credentials()
    except ekey.EkeyError:
        return True, "未读到登录态（musicex 文件需要 --ekey 手动给）"
    return True, "uin %s，来源 %s" % (creds.uin or "?", creds.source)


def _v_ffmpeg():
    path = audio.find_ffmpeg()
    if not path:
        return False, "没找到 ffmpeg（brew install ffmpeg）"
    return True, "ffmpeg：%s" % path


CHECKS = [
    ("TC-TEA 官方向量", _v_tea),
    ("TC-TEA 加解密闭环", _v_tea_roundtrip),
    ("Map 密码向量", _v_map),
    ("Map 密码分段一致", _v_map_roundtrip),
    ("RC4 密码向量", _v_rc4),
    ("QMC1 掩码流", _v_qmc1),
    ("端到端：QTag（EncV1）", _v_end_to_end_qtag),
    ("端到端：QTag（EncV2+RC4）", _v_end_to_end_encv2_longkey),
    ("端到端：V1 裸 key", _v_end_to_end_v1),
    ("尾部：musicex 缺 ekey 提示", _v_musicex_needs_ekey),
    ("扩展名映射", _v_ext_map),
    ("QQ音乐登录态（musicex 取密钥）", _v_ekey_credentials),
    ("ffmpeg 可用", _v_ffmpeg),
]


def run_selftest(verbose=True):
    """跑全部自检，返回 (全部通过, [(名称, 通过, 说明)])。"""
    results = []
    for name, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as exc:  # 自检本身出错也算失败
            ok, detail = False, "异常：%r" % exc
        results.append((name, ok, detail))
        if verbose:
            print("%s %-28s %s" % ("✓" if ok else "✗", name, detail))
    return all(item[1] for item in results), results
