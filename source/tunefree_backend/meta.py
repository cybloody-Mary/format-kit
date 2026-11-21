# -*- coding: utf-8 -*-
"""联网补全标签：歌名、全部歌手、专辑、年份、曲序、流派、唱片公司、封面、歌词。

数据来自 QQ音乐的公开接口，**不需要登录**，只发「这首歌是哪首」这一信息：

1. 文件尾部有 song_id（QTag / musicex 布局都有）→ 直接查歌曲详情；
2. 没有 song_id（V1 / QMC1 老格式）→ 用「歌手 + 歌名」搜一次，取最像的那首。

查到的结果**一定要和文件自己对得上**才会用（歌名、歌手、时长），对不上就当没查到，
退回文件里的标签 + 文件名 —— 宁可少补，也不写错。网络不通时自动降级，不影响转换。
"""
from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
SEARCH_URL = "https://c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg"
COVER_URL = "https://y.gtimg.cn/music/photo_new/T002R{size}x{size}M000{mid}.jpg"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
TIMEOUT = 8

DURATION_TOLERANCE = 6.0      # 秒；同一首歌不同版本的时长差一般远大于这个
# 文件自带 song_id 且时长几乎一样时，歌名、歌手对上一个就够（歌手改过名 / 别名 / 文件标签有错字）
ID_DURATION_TOLERANCE = 2.0


class TrackMeta(object):
    __slots__ = ("song_id", "song_mid", "title", "artists", "album", "album_mid", "date",
                 "track", "disc", "genre", "publisher", "duration", "lyrics", "matched_by")

    def __init__(self, **kwargs):
        for name in self.__slots__:
            setattr(self, name, kwargs.get(name))
        self.artists = list(self.artists or [])

    @property
    def artist(self):
        return "/".join(self.artists)

    @property
    def year(self):
        return (self.date or "")[:4]

    def to_tags(self):
        """转成写文件用的标签字典（键与 tags.py 一致）。"""
        tags = {
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "albumartist": self.artists[0] if self.artists else "",
            "date": self.year,
            "track": str(self.track) if self.track else "",
            "disc": str(self.disc) if self.disc else "",
            "genre": self.genre,
            "publisher": self.publisher,
        }
        return {k: v for k, v in tags.items() if v}

    def __repr__(self):
        return "TrackMeta(%s - %s《%s》%s)" % (self.artist, self.title, self.album, self.year)


class Hints(object):
    """文件自己能提供的线索：尾部的 song_id，源标签，文件名，时长。"""

    __slots__ = ("song_id", "song_mid", "title", "artist", "duration")

    def __init__(self, song_id=None, song_mid=None, title=None, artist=None, duration=None):
        try:
            self.song_id = int(song_id or 0)
        except (TypeError, ValueError):
            self.song_id = 0
        self.song_mid = song_mid or ""
        self.title = (title or "").strip()
        self.artist = (artist or "").strip()
        self.duration = duration


# ------------------------------------------------------------------ 匹配 ----

_BRACKETS = re.compile(r"[\(（\[【<《].*?[\)）\]】>》]")
_SUFFIX = re.compile(r"\s+-\s+.*$")
_ARTIST_SPLIT = re.compile(r"\s*(?:[,，/&、;；+]|\bfeat\.?|\bft\.?|\bwith\b|\bx\b)\s*", re.I)


def _norm(text):
    text = unicodedata.normalize("NFKC", text or "").lower()
    return "".join(ch for ch in text if ch.isalnum())


def _core(text):
    text = unicodedata.normalize("NFKC", text or "")
    text = _SUFFIX.sub("", _BRACKETS.sub("", text))
    return _norm(text)


def title_matches(found, wanted):
    if not found or not wanted:
        return False
    a, b = _norm(found), _norm(wanted)
    if a and a == b:
        return True
    ca, cb = _core(found), _core(wanted)
    return bool(ca) and ca == cb


def split_artists(text):
    return [part for part in (p.strip() for p in _ARTIST_SPLIT.split(text or "")) if part]


_BRACKET_INNER = re.compile(r"[\(（\[【<《](.*?)[\)）\]】>》]")


def _artist_keys(names):
    """一组歌手名能对上的写法：整串、拆开后的每个人、括号外 / 括号里的名字。

    「Robynn & Kendy」是一个组合名，不能只按 & 拆开比；
    「KOKIA (吉田亚纪子)」括号里是另一个名字，两边都算。
    """
    keys = set()
    for name in names:
        for part in [name] + split_artists(name):
            for variant in [part, _BRACKETS.sub("", part)] + _BRACKET_INNER.findall(part):
                key = _norm(variant)
                if key:
                    keys.add(key)
    return keys


def artists_overlap(found, wanted):
    return bool(_artist_keys(found) & _artist_keys([wanted]))


def plausible(meta, hints):
    """查到的结果和文件对得上吗。"""
    if meta is None or not meta.title:
        return False
    if hints.duration and meta.duration and abs(hints.duration - meta.duration) > DURATION_TOLERANCE:
        return False
    if hints.title:
        title_ok = title_matches(meta.title, hints.title)
        artist_hit = bool(hints.artist) and artists_overlap(meta.artists, hints.artist)
        if title_ok and (artist_hit or not hints.artist):
            return True
        # 文件尾部的 song_id 是 QQ音乐下载时写进去的，就是这首歌本身；
        # 时长几乎一样时，歌名或歌手有一个对上就信它（另一个多半是别名、改名或标签错字）
        same_length = bool(hints.duration and meta.duration
                           and abs(hints.duration - meta.duration) <= ID_DURATION_TOLERANCE)
        return meta.matched_by == "song_id" and same_length and (title_ok or artist_hit)
    # 文件里一点歌名线索都没有：只信 song_id，且时长必须对得上
    return bool(meta.matched_by == "song_id" and hints.duration and meta.duration)


# ------------------------------------------------------------------ 网络 ----

class Client(object):
    """一次任务共用一个；网络连续失败 3 次就停用，后面的歌不再等超时。"""

    def __init__(self, cache_dir=None, timeout=TIMEOUT, log=None):
        self.timeout = timeout
        self.cache_dir = cache_dir or default_cache_dir()
        self.log = log or (lambda level, message: None)
        self._failures = 0
        self._disabled = False
        self._lock = threading.Lock()
        self._covers = {}

    @property
    def available(self):
        return not self._disabled

    def _note_failure(self, exc):
        with self._lock:
            self._failures += 1
            if self._failures >= 3 and not self._disabled:
                self._disabled = True
                self.log("warning", "联网补全标签暂停：连续 3 次连不上 QQ音乐（%s），本批剩下的歌只用本地标签" % exc)

    def _note_success(self):
        with self._lock:
            self._failures = 0

    def _open(self, request):
        if self._disabled:
            return None
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                data = resp.read()
            self._note_success()
            return data
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._note_failure(exc)
            return None

    def _api(self, body):
        request = urllib.request.Request(
            API_URL,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"User-Agent": USER_AGENT, "Referer": "https://y.qq.com/",
                     "Content-Type": "application/json"},
        )
        raw = self._open(request)
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return None

    # ------------------------------------------------------------ 查询 ----
    def fetch(self, song_id, song_mid="", with_lyrics=True):
        """按 song_id 查详情（顺带歌词），一次请求。"""
        body = {
            "comm": {"ct": 24, "cv": 0},
            "detail": {"module": "music.pf_song_detail_svr", "method": "get_song_detail_yqq",
                       "param": {"song_id": int(song_id)}},
        }
        if with_lyrics:
            body["lyric"] = {"module": "music.musichallSong.PlayLyricInfo", "method": "GetPlayLyricInfo",
                             "param": {"songMID": song_mid or "", "songID": int(song_id)}}
        result = self._api(body)
        if not result:
            return None
        meta = parse_detail(((result.get("detail") or {}).get("data")) or {})
        if meta is None:
            return None
        if with_lyrics:
            meta.lyrics = parse_lyric(((result.get("lyric") or {}).get("data")) or {})
            if not meta.lyrics and not song_mid and meta.song_mid:
                # 歌词接口要 mid；文件里没有时用详情里拿到的再问一次
                again = self._api({"comm": {"ct": 24, "cv": 0},
                                   "lyric": {"module": "music.musichallSong.PlayLyricInfo",
                                             "method": "GetPlayLyricInfo",
                                             "param": {"songMID": meta.song_mid, "songID": int(song_id)}}})
                if again:
                    meta.lyrics = parse_lyric(((again.get("lyric") or {}).get("data")) or {})
        return meta

    def search(self, artist, title, limit=5):
        """smartbox 搜索（公开、免登录），返回 [(song_id, mid, name, singer)]。"""
        query = ("%s %s" % (artist, title)).strip()
        url = SEARCH_URL + "?" + urllib.parse.urlencode({"format": "json", "key": query})
        raw = self._open(urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                              "Referer": "https://y.qq.com/"}))
        if not raw:
            return []
        try:
            items = ((json.loads(raw.decode("utf-8")).get("data") or {}).get("song") or {}).get("itemlist") or []
        except ValueError:
            return []
        out = []
        for item in items[:limit]:
            try:
                out.append((int(item.get("id") or 0), item.get("mid") or "",
                            item.get("name") or "", item.get("singer") or ""))
            except (TypeError, ValueError):
                continue
        return [row for row in out if row[0]]

    def resolve(self, hints):
        """按线索找到这首歌并校验；找不到 / 对不上返回 None。"""
        if self._disabled:
            return None
        if hints.song_id > 0:
            meta = self.fetch(hints.song_id, hints.song_mid)
            if meta is not None:
                meta.matched_by = "song_id"
                if plausible(meta, hints):
                    return meta
        if hints.title and hints.artist:
            for song_id, mid, name, singer in self.search(hints.artist, hints.title):
                if song_id == hints.song_id:
                    continue
                if not title_matches(name, hints.title):
                    continue
                if not artists_overlap(split_artists(singer), hints.artist):
                    continue
                meta = self.fetch(song_id, mid)
                if meta is not None:
                    meta.matched_by = "search"
                    if plausible(meta, hints):
                        return meta
        return None

    # ------------------------------------------------------------ 封面 ----
    def preview(self, song_id, song_mid="", allow_network=True):
        """载入文件夹时给封面墙用：按文件自带 song_id 查歌名 / 歌手 / 专辑 / 封面。

        不查歌词，结果按 song_id 缓存在本机，同一个文件夹第二次打开不再联网。
        返回 dict（含本地封面路径）或 None。
        """
        try:
            song_id = int(song_id or 0)
        except (TypeError, ValueError):
            return None
        if song_id <= 0 or self._disabled:
            return None
        cache = os.path.join(self.cache_dir, "songs", "%d.json" % song_id)
        row = None
        try:
            with open(cache, "r", encoding="utf-8") as fh:
                row = json.load(fh)
        except (OSError, ValueError):
            row = None
        if row is None and not allow_network:
            return None
        if row is None:
            meta = self.fetch(song_id, song_mid, with_lyrics=False)
            if meta is None:
                return None
            row = {"title": meta.title or "", "artist": meta.artist, "album": meta.album or "",
                   "album_mid": meta.album_mid or "", "year": meta.year, "duration": meta.duration}
            try:
                os.makedirs(os.path.dirname(cache), exist_ok=True)
                tmp = cache + ".part%d" % threading.get_ident()
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(row, fh, ensure_ascii=False)
                os.replace(tmp, cache)
            except OSError:
                pass
        out = dict(row)
        out["cover"] = self.cover(row.get("album_mid"), allow_network=allow_network) or ""
        return out

    def cover(self, album_mid, allow_network=True):
        """下载专辑封面（800×800，退而求其次 500），缓存到本机；返回本地路径或 None。"""
        if not album_mid:
            return None
        with self._lock:
            if album_mid in self._covers:
                return self._covers[album_mid]
        path = os.path.join(self.cache_dir, "%s.jpg" % album_mid)
        if os.path.isfile(path) and os.path.getsize(path) > 1024:
            with self._lock:
                self._covers[album_mid] = path
            return path
        if not allow_network:
            return None
        data = None
        for size in (800, 500, 300):
            raw = self._open(urllib.request.Request(COVER_URL.format(size=size, mid=album_mid),
                                                    headers={"User-Agent": USER_AGENT}))
            if raw and raw[:3] == b"\xff\xd8\xff" and len(raw) > 1024:
                data = raw
                break
        if not data:
            with self._lock:
                self._covers[album_mid] = None
            return None
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp = path + ".part%d" % threading.get_ident()
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except OSError:
            path = None
        with self._lock:
            self._covers[album_mid] = path
        return path


def default_cache_dir():
    base = os.path.expanduser("~/Library/Caches/TuneFree/covers")
    try:
        os.makedirs(base, exist_ok=True)
        return base
    except OSError:
        return os.path.join(tempfile.gettempdir(), "tunefree-covers")


# ------------------------------------------------------------------ 解析 ----

def parse_detail(data):
    info = data.get("track_info") or {}
    if not info or not (info.get("name") or info.get("title")):
        return None
    album = info.get("album") or {}
    extras = {}
    for item in (data.get("info") or {}).values() if isinstance(data.get("info"), dict) else []:
        values = [c.get("value") for c in (item.get("content") or []) if c.get("value")]
        if values:
            extras[item.get("title") or ""] = values[0]
    date = info.get("time_public") or album.get("time_public") or extras.get("发行时间") or ""
    index_cd = info.get("index_cd")
    return TrackMeta(
        song_id=info.get("id"),
        song_mid=info.get("mid") or "",
        title=info.get("name") or info.get("title"),
        artists=[s.get("name") for s in (info.get("singer") or []) if s.get("name")],
        album=album.get("name") or album.get("title") or "",
        album_mid=album.get("mid") or "",
        date=date if re.match(r"^\d{4}", date or "") else "",
        track=info.get("index_album") or None,
        disc=(int(index_cd) + 1) if isinstance(index_cd, int) and index_cd > 0 else None,
        genre=extras.get("歌曲流派") or "",
        publisher=extras.get("唱片公司") or "",
        duration=float(info.get("interval")) if info.get("interval") else None,
    )


def parse_lyric(data):
    text = data.get("lyric") or ""
    if not text:
        return ""
    try:
        decoded = base64.b64decode(text).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        decoded = text
    decoded = decoded.replace("\r\n", "\n").strip()
    # 纯音乐 / 暂无歌词的占位不要
    plain = lrc_to_text(decoded)
    if not plain or "纯音乐" in plain and len(plain) < 40 or "暂无歌词" in plain:
        return ""
    return decoded


_LRC_TIME = re.compile(r"\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\]")
_LRC_META = re.compile(r"^\[(?:ti|ar|al|by|offset|re|ve|length|au):.*\]$", re.I)


def lrc_to_text(lrc):
    """LRC → 纯文本（给 MP3 的 USLT：Apple 音乐等播放器会把时间戳原样显示出来）。"""
    lines = []
    for line in (lrc or "").splitlines():
        line = line.strip()
        if not line or _LRC_META.match(line):
            continue
        text = _LRC_TIME.sub("", line).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)
