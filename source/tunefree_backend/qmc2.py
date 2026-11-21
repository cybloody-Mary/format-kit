# -*- coding: utf-8 -*-
"""QMCv2（.mflac / .mgg）：尾部元数据里的 ekey → TC-TEA 派生密钥 → Map/RC4 解密。

全部在本地算完：不需要 QQ音乐客户端、不需要注入进程；
只有 musicex 布局需要联网向官方接口取这首歌的 ekey。

三种尾部布局（都以文件末尾为基准）：

    QTag     [加密音频][ekey,song_id,ver][u32 BE 元数据长度]["QTag"]
    V1       [加密音频][原始 key 字节 K][u32 LE K]
    musicex  [加密音频][176B 元数据][u32 LE 192][u32 LE 1]["musicex\\0"]

ekey 派生：base64 → 可能带 ``QQMusic EncV2,Key:`` 前缀（两层 TC-TEA）→
EncV1 形态（前 8 字节 header + TC-TEA 加密的 body）→ 最终 key。
key 长度 <= 300 走 Map（异或表），> 300 走 RC4 分段流密码。
"""
from __future__ import annotations

import base64
import struct

from . import tea

# EncV2 ekey 的包装标记与两层 TEA 密钥（由编码端写死，必须逐字节一致）
ENCV2_PREFIX = b"QQMusic EncV2,Key:"
ENCV2_STAGE1_KEY = b"386ZJY!@#*$%^&)("
ENCV2_STAGE2_KEY = b"**#!(#$%&^a1cZ,T"

# Map 密码的模数与偏移
MAP_MOD = 0x7FFF
MAP_BIAS = 71214

# RC4 密码的分段
RC4_FIRST = 0x80
RC4_SEGMENT = 0x1400

MAP_KEY_LIMIT = 300

_M32 = 0xFFFFFFFF


class Qmc2Error(Exception):
    """ekey / 尾部结构不对，或解密结果明显不是音频。"""


class MissingEkey(Qmc2Error):
    """musicex 布局需要 ekey，但没拿到（未登录 / 过期 / 接口拒绝）。"""


# ----------------------------------------------------------------- ekey ----

def parse_ekey(ekey):
    """base64 ekey → 实际用于解密的 key 字节串。"""
    if isinstance(ekey, bytes):
        ekey = ekey.decode("ascii", "replace")
    text = ekey.strip().strip("\x00").strip()
    if not text:
        raise Qmc2Error("ekey 是空的")

    try:
        raw = base64.b64decode(text, validate=False)
    except Exception as exc:  # binascii.Error 等
        raise Qmc2Error("ekey 不是合法的 base64：%s" % exc)

    if raw.startswith(ENCV2_PREFIX):
        blob = raw[len(ENCV2_PREFIX):]
        stage1 = tea.decrypt(blob, ENCV2_STAGE1_KEY)
        if stage1 is None:
            raise Qmc2Error("EncV2 ekey 第一层解密失败")
        stage2 = tea.decrypt(stage1, ENCV2_STAGE2_KEY)
        if stage2 is None:
            raise Qmc2Error("EncV2 ekey 第二层解密失败")
        try:
            raw = base64.b64decode(stage2, validate=False)
        except Exception as exc:
            raise Qmc2Error("EncV2 ekey 内层不是合法的 base64：%s" % exc)

    if len(raw) < 8:
        raise Qmc2Error("ekey 解码后不足 8 字节")

    header, body = raw[:8], raw[8:]
    if not body:
        return header

    plain = tea.decrypt(body, tea.derive_tea_key(header))
    if plain is None:
        # 少数 ekey（例如 OpenAPI 直接返回的）本身就是裸 key，原样使用
        return raw
    return header + plain


# ------------------------------------------------------------- Map 密码 ----

def _scramble(value, index):
    rot = (index + 4) & 0x07
    return ((value << rot) & 0xFF) | (value >> rot)


class MapCipher(object):
    """短 key（<=300B）用的异或密码。

    第 o 个字节的掩码是 ``key[(o*o + 71214) % len(key)]`` 按 ``o`` 旋转后的值；
    o 超过 0x7FFF 之后按 ``o % 0x7FFF`` 循环 —— 注意 0x7FFF 这**一个**偏移
    用的是它自身（不是 0），这是全文件唯一一个例外字节。
    """

    kind = "map"

    def __init__(self, key):
        self.key = bytes(key)
        if not self.key:
            raise Qmc2Error("key 为空")
        self.size = len(self.key)
        self._table = self._build_table()
        self._base = self._table[:MAP_MOD]  # 周期块：索引 0..0x7FFE

    def _build_table(self):
        table = bytearray(0x8000)
        n = self.size
        key = self.key
        for o in range(0x8000):
            idx = (o * o + MAP_BIAS) % n
            table[o] = _scramble(key[idx], idx)
        return bytes(table)

    def mask_at(self, offset):
        if offset > MAP_MOD:
            offset %= MAP_MOD
        return self._table[offset]

    def keystream(self, length, offset=0):
        """生成从 offset 开始、长度 length 的掩码流。"""
        if length <= 0:
            return b""
        base = self._base
        period = MAP_MOD
        out = bytearray()
        pos = offset
        left = length
        # 周期块直接切片拼接，避开逐字节循环
        first_take = min(left, period - (pos % period)) if pos % period else min(left, period)
        while left > 0:
            phase = pos % period
            take = min(left, period - phase)
            out += base[phase:phase + take]
            pos += take
            left -= take
        # 唯一例外：绝对偏移 0x7FFF 用 table[0x7FFF]
        special = MAP_MOD
        if offset <= special < offset + length:
            out[special - offset] = self._table[special]
        return bytes(out)

    def decrypt(self, data, offset=0):
        data = bytes(data)
        n = len(data)
        if not n:
            return b""
        ks = self.keystream(n, offset)
        return (int.from_bytes(data, "big") ^ int.from_bytes(ks, "big")).to_bytes(n, "big")


# ------------------------------------------------------------- RC4 密码 ----

def _hash_base(data):
    """从 key 求出分段用的 hash：非零字节连乘 u32，一旦回绕或变小就停。"""
    value = 1
    for byte in data:
        if byte == 0:
            continue
        nxt = (value * byte) & _M32
        if nxt == 0 or nxt <= value:
            break
        value = nxt
    return value


class Rc4Cipher(object):
    """长 key（>300B）用的变体 RC4：S 盒长度等于 key 长度，按 0x1400 分段。"""

    kind = "rc4"

    def __init__(self, key):
        self.key = bytes(key)
        self.size = n = len(self.key)
        if not n:
            raise Qmc2Error("key 为空")

        if n > 256:
            box = bytearray(range(256))
            box.extend(bytes(i & 0xFF for i in range(n - 256)))
        else:
            box = bytearray(range(n))

        j = 0
        for i in range(n):
            j = (j + box[i] + self.key[i]) % n
            box[i], box[j] = box[j], box[i]
        self._box = box
        self._hash = _hash_base(self.key)
        # 每个分段都从同一个初始 S 盒、j=k=0 开始，只是先丢弃 discard 个字节，
        # 所以所有分段的密钥流都是同一条「基础流」的切片。discard 最大
        # 0x1FF + (RC4_SEGMENT-1)，再取一个分段长度，一次算好就够全文件用。
        self._base = self._stream(0x1FF + RC4_SEGMENT)
        self._head = self._first_keystream()

    def _stream(self, length):
        n = self.size
        box = bytearray(self._box)
        out = bytearray(length)
        j = 0
        k = 0
        for x in range(length):
            j = (j + 1) % n
            k = (box[j] + k) % n
            box[j], box[k] = box[k], box[j]
            out[x] = box[(box[j] + box[k]) % n]
        return bytes(out)

    def _segment_key(self, seg_id, seed):
        if not seed:
            return 0
        return int(self._hash / ((seg_id + 1) * seed) * 100.0)

    def _first_keystream(self):
        """前 RC4_FIRST 字节：每个字节单独从 key 里取掩码。"""
        n = self.size
        key = self.key
        return bytes(key[self._segment_key(o, key[o % n]) % n] for o in range(RC4_FIRST))

    def keystream(self, length, offset=0):
        """从 offset 开始、长度 length 的掩码流（全部是切片拼接，没有逐字节循环）。"""
        parts = []
        cur = offset
        end = offset + length
        if cur < RC4_FIRST and cur < end:
            stop = min(end, RC4_FIRST)
            parts.append(self._head[cur:stop])
            cur = stop
        base = self._base
        key = self.key
        while cur < end:
            seg_id = cur // RC4_SEGMENT
            phase = cur % RC4_SEGMENT
            take = min(end - cur, RC4_SEGMENT - phase)
            discard = (self._segment_key(seg_id, key[seg_id & 0x1FF]) & 0x1FF) + phase
            parts.append(base[discard:discard + take])
            cur += take
        return b"".join(parts)

    def decrypt(self, data, offset=0):
        data = bytes(data)
        n = len(data)
        if not n:
            return b""
        ks = self.keystream(n, offset)
        return (int.from_bytes(data, "big") ^ int.from_bytes(ks, "big")).to_bytes(n, "big")


def make_cipher(key):
    """按 key 长度选密码。"""
    key = bytes(key)
    if len(key) > MAP_KEY_LIMIT:
        return Rc4Cipher(key)
    return MapCipher(key)


# --------------------------------------------------------------- 尾部 ----

class Footer(object):
    """文件尾部解析结果。"""

    __slots__ = ("kind", "audio_len", "ekey", "song_id", "mid", "filename", "key")

    def __init__(self, kind, audio_len, ekey=None, song_id="", mid="", filename="", key=None):
        self.kind = kind
        self.audio_len = audio_len
        self.ekey = ekey
        self.song_id = song_id
        self.mid = mid
        self.filename = filename
        self.key = key

    def __repr__(self):
        return "Footer(kind=%r, audio_len=%d, song_id=%r)" % (self.kind, self.audio_len, self.song_id)


def _read_utf16le(data, offset, max_len):
    end = min(offset + max_len, len(data))
    chars = []
    i = offset
    while i + 1 < end:
        code = data[i] | (data[i + 1] << 8)
        if code == 0:
            break
        chars.append(chr(code))
        i += 2
    return "".join(chars)


def detect_footer(data):
    """识别尾部布局；认不出来返回 kind='unknown'（此时多半是 QMC1）。"""
    total = len(data)
    if total < 8:
        return Footer("unknown", total)

    # musicex：末尾 8 字节是 "musicex\\0"
    if total >= 16 and data[total - 8:] == b"musicex\x00":
        footer_size = struct.unpack("<I", data[total - 16:total - 12])[0]
        version = struct.unpack("<I", data[total - 12:total - 8])[0]
        if version == 1 and 16 < footer_size <= total:
            meta_start = total - footer_size
            meta = data[meta_start:total - 16]
            return Footer(
                "musicex",
                meta_start,
                song_id=str(struct.unpack("<I", meta[:4])[0]) if len(meta) >= 4 else "",
                mid=_read_utf16le(meta, 0x0C, 60),
                filename=_read_utf16le(meta, 0x48, 68),
            )

    # QTag：末尾 4 字节是 "QTag"，前面 4 字节是大端元数据长度
    if data[total - 4:] == b"QTag" and total >= 12:
        meta_len = struct.unpack(">I", data[total - 8:total - 4])[0]
        if 0 < meta_len <= total - 8:
            meta = data[total - 8 - meta_len:total - 8]
            head, _, rest = meta.partition(b",")
            song_id = rest.split(b",")[0].decode("ascii", "replace") if rest else ""
            return Footer(
                "qtag",
                total - 8 - meta_len,
                ekey=head.decode("ascii", "replace"),
                song_id=song_id,
            )

    # V1：末尾 4 字节是小端的 key 长度，key 就在它前面
    key_size = struct.unpack("<I", data[total - 4:])[0]
    if 0 < key_size <= 0x400 and key_size + 4 < total:
        return Footer("v1", total - 4 - key_size, key=data[total - 4 - key_size:total - 4])

    return Footer("unknown", total)


def decrypt_qmc2(data, ekey=None, expect_ext=None, auto_ekey=True, authst=None, uin=None):
    """解一整份 QMC2 文件（含尾部剥离）。

    ``auto_ekey=True`` 时，musicex 布局会读本机 QQ音乐的登录态去调 GetEVkey 接口取密钥。
    返回 ``(plain_bytes, footer, cipher)``；结构不对抛 Qmc2Error。
    """
    footer = detect_footer(data)

    if footer.kind == "v1":
        key = footer.key
        if not key:
            raise Qmc2Error("V1 尾部里的 key 长度为 0")
    elif footer.kind == "qtag":
        if not footer.ekey:
            raise Qmc2Error("QTag 尾部里没有 ekey")
        key = parse_ekey(ekey or footer.ekey)
    elif footer.kind == "musicex":
        if not ekey and auto_ekey:
            from . import ekey as ekey_mod

            try:
                ekey = ekey_mod.ekey_for_footer(footer, explicit_authst=authst, explicit_uin=uin)
            except ekey_mod.EkeyError as exc:
                raise MissingEkey(str(exc))
        if not ekey:
            raise MissingEkey(
                "这个文件用的是 musicex 尾部，密钥不写在文件里："
                "要么用自动取密钥（需要 QQ音乐已登录），要么用 --ekey 手动给"
            )
        key = parse_ekey(ekey)
    else:
        raise Qmc2Error("无法识别的文件尾部结构（不是 QTag / V1 / musicex）")

    cipher = make_cipher(key)
    audio = bytes(data[:footer.audio_len])
    plain = cipher.decrypt(audio)

    if expect_ext and not _looks_like(plain, expect_ext):
        raise Qmc2Error(
            "解密结果不是预期的 %s 音频（头部为 %r），密钥可能不对"
            % (expect_ext, plain[:4])
        )
    return plain, footer, cipher


def _looks_like(plain, ext):
    """粗校验：OGG / FLAC / MP3 头部特征。"""
    if len(plain) < 4:
        return False
    if ext == "ogg":
        return plain[:4] == b"OggS"
    if ext == "flac":
        return plain[:4] == b"fLaC" or plain[:4] == b"ID3\x03" or plain[:4] == b"ID3\x04"
    if ext == "mp3":
        return plain[:3] == b"ID3" or (plain[0] == 0xFF and (plain[1] & 0xE0) == 0xE0)
    return True
