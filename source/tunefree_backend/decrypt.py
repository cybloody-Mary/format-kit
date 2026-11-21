# -*- coding: utf-8 -*-
"""文件级解密：认扩展名 → 选密码 → 剥尾部 → 落盘（原子写）。

只处理本机已下载的加密文件，全程离线，不碰网络。
"""
from __future__ import annotations

import os
import tempfile

from . import qmc1, qmc2, trim

# 单文件上限
MAX_FILE_SIZE = 512 * 1024 * 1024

# QMCv2（腾讯新版，带 ekey）
QMC2_EXTS = {
    ".mflac": "flac",
    ".mflac0": "flac",
    ".mflach": "flac",
    ".mgg": "ogg",
    ".mgg0": "ogg",
    ".mgg1": "ogg",
    ".mggl": "ogg",
}

# QMCv1（固定异或表）
PROBE_TAIL = 64 * 1024

QMC1_EXTS = {
    ".qmcflac": "flac",
    ".qmcogg": "ogg",
    ".qmc0": "mp3",
    ".qmc3": "mp3",
    ".qmc2": "ogg",
}

OUTPUT_EXT = {}
OUTPUT_EXT.update(QMC2_EXTS)
OUTPUT_EXT.update(QMC1_EXTS)

SUPPORTED_EXTS = tuple(sorted(OUTPUT_EXT))


class DecryptError(Exception):
    """可预期的失败（不是 bug）。reason 供界面做分类提示。"""

    def __init__(self, message, reason="decrypt"):
        Exception.__init__(self, message)
        self.reason = reason


def output_extension(path):
    return OUTPUT_EXT.get(os.path.splitext(path)[1].lower())


def is_supported(path):
    return output_extension(path) is not None


def sniff_plain_audio(data):
    """看解密结果的头部，返回 'flac' / 'ogg' / 'mp3' / None。"""
    head = data[:4]
    if head == b"fLaC":
        return "flac"
    if head == b"OggS":
        return "ogg"
    if head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    if head[:4] == b"ftyp":
        return "m4a"
    return None


def probe(path):
    """只看尾部，不解密（--info / 界面预览用）。"""
    ext = os.path.splitext(path)[1].lower()
    info = {
        "path": path,
        "size": os.path.getsize(path),
        "ext": ext,
        "kind": "unknown",
        "format": OUTPUT_EXT.get(ext),
        "song_id": "",
        "ekey": None,
    }
    if ext not in QMC1_EXTS:
        # 尾部结构都在最后几百字节里（V1 的 key 最多 1 KB），只读末尾一段，
        # 几百首几 GB 的文件夹载入时不用把整份音频读一遍
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                total = fh.tell()
                skip = max(0, total - PROBE_TAIL)
                fh.seek(skip)
                data = fh.read()
        except OSError as exc:
            raise DecryptError("读不了这个文件：%s" % exc, "io")
        footer = qmc2.detect_footer(data)
        footer.audio_len += skip
        info["kind"] = footer.kind
        info["song_id"] = footer.song_id
        info["mid"] = footer.mid
        info["filename"] = footer.filename
        info["audio_bytes"] = footer.audio_len
        if footer.ekey:
            info["ekey"] = footer.ekey[:24] + "…" if len(footer.ekey) > 24 else footer.ekey
        if footer.kind == "musicex":
            info["ekey_auto"] = _can_auto_ekey()
        if footer.key:
            info["key_bytes"] = len(footer.key)
    else:
        info["kind"] = "qmc1"
    return info


def _can_auto_ekey():
    """musicex 文件能不能自动取密钥（只读本机登录态，不发请求）。"""
    try:
        from . import ekey as ekey_mod

        creds = ekey_mod.get_credentials()
        return {"available": True, "uin": creds.uin, "source": creds.source}
    except Exception:
        return {"available": False}


def _read_file(path):
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise DecryptError("读不了这个文件：%s" % exc, "io")
    if size == 0:
        raise DecryptError("文件是空的（可能还没下载完）", "empty")
    if size > MAX_FILE_SIZE:
        raise DecryptError(
            "文件过大（%d 字节），超出处理范围" % size, "too-large"
        )
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError as exc:
        raise DecryptError("读取失败：%s" % exc, "io")


def decrypt_bytes(path, ekey=None, auto_ekey=True, authst=None, uin=None):
    """返回 (明文, 信息字典)。信息里有 kind / cipher / song_id 等。"""
    ext = os.path.splitext(path)[1].lower()
    target = OUTPUT_EXT.get(ext)
    if not target:
        raise DecryptError("不认识的扩展名：%s" % (ext or "(无)"), "unsupported")

    data = _read_file(path)
    info = {"ext": ext, "format": target, "encrypted_bytes": len(data)}

    if ext in QMC1_EXTS:
        cipher = qmc1.Qmc1Cipher()
        plain = cipher.decrypt(data)
        info["kind"] = "qmc1"
        info["cipher"] = cipher.kind
        info["audio_bytes"] = len(plain)
    else:
        try:
            plain, footer, cipher = qmc2.decrypt_qmc2(
                data, ekey=ekey, expect_ext=target,
                auto_ekey=auto_ekey, authst=authst, uin=uin,
            )
        except qmc2.MissingEkey as exc:
            raise DecryptError(str(exc), "ekey")
        except qmc2.Qmc2Error as exc:
            raise DecryptError(str(exc), "qmc2")
        info["kind"] = footer.kind
        info["cipher"] = cipher.kind
        info["song_id"] = footer.song_id
        info["mid"] = footer.mid
        info["audio_bytes"] = footer.audio_len
        info["key_bytes"] = len(getattr(cipher, "key", b""))

    sniffed = sniff_plain_audio(plain)
    info["sniffed"] = sniffed
    if sniffed is None:
        raise DecryptError(
            "解密结果的头部不是音频（%r），密钥可能不对或文件已损坏" % plain[:4], "bad-output"
        )
    # 部分 QQ音乐文件在音频和尾部之间夹着几十字节非音频数据，解密后会接在音频末尾；
    # 能确认最后一帧 / 最后一页完整结束的位置时，把多余的裁掉
    end = trim.audio_end(plain, sniffed)
    if end is not None and 0 < end < len(plain):
        info["trimmed_bytes"] = len(plain) - end
        plain = plain[:end]
    return plain, info


def decrypt_file(src, dst, ekey=None, overwrite=True, auto_ekey=True, authst=None, uin=None):
    """解密一个文件并写盘。返回信息字典。写盘用「临时文件 + rename」保证不留下半个文件。"""
    plain, info = decrypt_bytes(src, ekey=ekey, auto_ekey=auto_ekey, authst=authst, uin=uin)

    if not overwrite and os.path.exists(dst):
        info["written"] = False
        return info

    parent = os.path.dirname(os.path.abspath(dst)) or "."
    os.makedirs(parent, exist_ok=True)

    fd, tmp = tempfile.mkstemp(prefix=".tunefree-", suffix=".part", dir=parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(plain)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dst)
        # mkstemp 建的是 0600，落到用户目录里会变成「只有本人能读」；
        # 按常规文件权限改回来（尊重 umask），否则拷到手机/别的账户会读不到
        try:
            mask = os.umask(0)
            os.umask(mask)
            os.chmod(dst, 0o666 & ~mask)
        except OSError:
            pass
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise DecryptError("写入失败：%s" % exc, "write")

    info["written"] = True
    info["output"] = dst
    info["output_bytes"] = len(plain)
    return info


def inspect_dir(root):
    """扫描目录并给出每个文件的可展示信息（界面的文件列表用）。

    返回 ``[{"path", "name", "size", "kind", "format", "song_id", ...}]``，
    单个文件读不出来时带 ``"error"`` 字段，不抛异常。
    """
    rows = []
    for path in scan(root):
        try:
            info = probe(path)
        except DecryptError as exc:
            info = {"path": path, "name": os.path.basename(path),
                    "size": os.path.getsize(path) if os.path.exists(path) else 0,
                    "error": str(exc)}
        else:
            info["name"] = os.path.basename(path)
            info["relpath"] = os.path.relpath(path, root)
        rows.append(info)
    return rows


def scan(root):
    """递归列出受支持的加密文件（按路径排序）。"""
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.startswith("."):
                continue
            if is_supported(name):
                found.append(os.path.join(dirpath, name))
    found.sort()
    return found
