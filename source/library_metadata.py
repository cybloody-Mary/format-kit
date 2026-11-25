"""Local song metadata and quality labels for the conversion library."""

from contextlib import closing
from pathlib import Path
import re
import sqlite3
import struct
import unicodedata

from tunefree_backend import audio, tags

CATALOG = 'format-kit.sqlite'
ARTIST_SEPARATOR = re.compile(r'[\r\n]+')
FILE_ARTIST_SEPARATOR = re.compile(r'[,，、;；\r\n]+')


def extract_song_id(path, identity):
    """Match the per-song catalog ID to the resource already selected by the ledger."""
    if not identity:
        return None
    try:
        with path.open('rb') as handle:
            size = handle.seek(0, 2)
            if size < 16:
                return None
            handle.seek(-16, 2)
            length, version, marker = struct.unpack('<II8s', handle.read(16))
            if marker != b'musicex\0' or version != 1 or not 156 <= length < size:
                return None
            handle.seek(-length, 2)
            data = handle.read(140)
            mid = data[12:72].decode('utf-16-le').split('\0', 1)[0]
            name = data[72:140].decode('utf-16-le').split('\0', 1)[0]
            if f'{mid}/{name}' == identity:
                return struct.unpack_from('<I', data)[0] or None
    except (OSError, ValueError, struct.error):
        return None
    return None


def catalog_rows(database, ids):
    ids = sorted(set(ids))
    if not ids or not database.is_file():
        return {}
    result = {}
    try:
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=0.15)) as connection:
            connection.execute('PRAGMA query_only = ON')
            for offset in range(0, len(ids), 300):
                block = ids[offset:offset + 300]
                placeholders = ','.join('?' for _ in block)
                for song_id, title, artists in connection.execute(
                    f'SELECT id, name, singer FROM SONGS WHERE id IN ({placeholders})', block
                ):
                    result[int(song_id)] = (title, artists)
    except (sqlite3.Error, ValueError, OSError):
        return {}
    return result


def clean_text(value):
    return ' '.join(value.split()).strip() if isinstance(value, str) else ''


def distinct_names(values):
    seen = set()
    names = []
    for entry in values:
        name = clean_text(entry)
        key = unicodedata.normalize('NFC', name).casefold()
        if key and key not in seen:
            names.append(name)
            seen.add(key)
    return names


def song_names(source, original, catalog_entry):
    title, raw_artists = catalog_entry
    title = clean_text(title)
    artists = distinct_names(ARTIST_SEPARATOR.split(raw_artists)) if isinstance(raw_artists, str) else []

    if original and (not title or not artists):
        source_tags = tags.read_source(str(original)).tags
        title = title or clean_text(source_tags.get('title'))
        raw = source_tags.get('artist', '')
        artists = artists or distinct_names(FILE_ARTIST_SEPARATOR.split(raw))

    if not title or not artists:
        from_name = audio.parse_filename_tags(source.name)
        title = title or clean_text(from_name.get('title')) or source.stem
        artists = artists or distinct_names(FILE_ARTIST_SEPARATOR.split(from_name.get('artist', '')))

    return title, artists


def flac_properties(path):
    with path.open('rb') as handle:
        data = handle.read(42)
        if data.startswith(b'ID3') and len(data) >= 10:
            size = sum((byte & 127) << (7 * (3 - index)) for index, byte in enumerate(data[6:10]))
            handle.seek(10 + size + (10 if data[5] & 16 else 0))
            data = handle.read(42)
    if len(data) == 42 and data.startswith(b'fLaC') and not data[4] & 127 and data[5:8] == b'\0\0\x22':
        fields = struct.unpack_from('>Q', data, 18)[0]
        frequency, depth = fields >> 44, ((fields >> 36) & 31) + 1
        if frequency:
            return f'FLAC · {depth}-bit / {frequency / 1000:g} kHz'
    return None


def ogg_properties(path):
    with path.open('rb') as handle:
        header = handle.read(27)
        if len(header) != 27 or not header.startswith(b'OggS'):
            return None
        laces = handle.read(header[26])
        if not laces or sum(laces) < 8:
            return None
        packet = handle.read(min(sum(laces), 16))
    if packet.startswith(b'\x7fFLAC'):
        return '无损', 'Ogg FLAC'
    if packet.startswith(b'\x01vorbis'):
        if len(packet) == 16:
            rate = struct.unpack_from('<I', packet, 12)[0]
            if rate:
                return '有损', f'Vorbis · {rate / 1000:g} kHz'
        return '有损', 'Vorbis'
    if packet.startswith(b'OpusHead'):
        return '有损', 'Opus'
    return None


def sound_quality(source_format, original, status):
    if status == 'incomplete':
        return '未知', None
    if original:
        try:
            if source_format in ('MFLAC', 'MFLAC0', 'QMCFLAC', 'FLAC'):
                measured = flac_properties(original)
                if measured:
                    return '无损', measured
            elif source_format in ('MGG', 'MGG0', 'MGG1', 'MGGL', 'QMC2', 'QMCOGG', 'OGG'):
                measured = ogg_properties(original)
                if measured:
                    return measured
        except (OSError, ValueError, struct.error):
            pass
    # These labels indicate the expected underlying codec, rather than a
    # streaming-service tier or a guessed sample rate from the file prefix.
    if source_format in ('MFLAC', 'MFLAC0', 'QMCFLAC', 'FLAC'):
        return '无损', 'FLAC · 按源格式'
    if source_format in ('MGG', 'MGG0', 'MGG1', 'MGGL', 'QMC2', 'QMCOGG', 'OGG'):
        return '有损', 'OGG · 按源格式'
    return '未知', None


def enrich(source_root, rows):
    items = [(row, Path(row['source']), extract_song_id(Path(row['source']), row.get('resource')))
             for row in rows]
    matches = catalog_rows(source_root.parent / CATALOG, (song_id for _, _, song_id in items if song_id))
    enriched = []
    for row, source, song_id in items:
        original = Path(row['original']) if row.get('original') and row.get('has_original') else None
        inspectable = original or (source if row.get('resource', '').startswith('audio:') else None)
        name, artists = song_names(source, inspectable, matches.get(song_id, ('', '')))
        source_format = source.suffix[1:].upper()
        quality, detail = sound_quality(source_format, inspectable, row['status'])
        enriched.append(dict(row, title=name, artists=artists, source_format=source_format,
                             quality=quality, quality_detail=detail))
    return enriched
