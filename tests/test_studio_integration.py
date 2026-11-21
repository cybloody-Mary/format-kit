"""Exercise the studio against disposable audio files with no local login or network."""

import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

import studio
from studio import restore_audio as real_restore_audio


WORK = Path(__file__).resolve().parents[1]
FFMPEG = Path(os.environ.get('QQMUSIC_TEST_FFMPEG', WORK / 'ffmpeg')).resolve()


def write_musicex(source, song, media_id, resource_name):
    footer = bytearray(176)
    for offset, value in [(12, media_id), (72, resource_name)]:
        encoded = value.encode('utf-16-le')
        footer[offset:offset + len(encoded)] = encoded
    path = source / song
    path.write_bytes(b'Offline integration test body' * 100 + footer +
                     struct.pack('<II8s', 192, 1, b'musicex\x00'))
    return path


def ffmpeg(*args):
    subprocess.run([str(FFMPEG), '-hide_banner', '-v', 'error', '-nostdin',
                    *map(str, args)], check=True, capture_output=True, timeout=30)


class StudioIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='qqmusic-studio-fixtures-')
        cls.addClassCleanup(temporary.cleanup)
        root = Path(temporary.name)
        cls.original = root / 'sample.flac'
        cls.mp3 = root / 'sample.mp3'
        ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100:duration=1',
               '-c:a', 'flac', cls.original)
        ffmpeg('-i', cls.original, '-c:a', 'libmp3lame', '-b:a', '320k', cls.mp3)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='qqmusic-studio-integration-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / 'iQmc'
        self.source.mkdir()
        self.export = self.root / '导出'
        self.mp3_dir = self.export / 'MP3'
        self.original_dir = self.export / '原音'
        self.mp3_dir.mkdir(parents=True)
        self.original_dir.mkdir()
        self.state = self.root / '台账.json'
        self.report = self.root / '清单.txt'
        self.settings = studio.Settings(self.source, self.export, self.state, self.report, FFMPEG)
        offline_guards = [
            patch.object(socket, 'socket', side_effect=AssertionError('Unexpected network access')),
            patch.object(studio, 'read_credentials', side_effect=AssertionError('Unexpected credentials access')),
            patch.object(studio, 'restore_audio', side_effect=AssertionError('Unexpected encrypted audio access')),
        ]
        for guard in offline_guards:
            guard.start()
            self.addCleanup(guard.stop)

    def status_for(self, name):
        rows = studio.inventory(self.settings)['rows']
        return next(row for row in rows if Path(row['source']).name == name)

    def request_for(self, row, authorized, mode='tunefree'):
        return {'allow_qq_login': authorized, 'selected': [row['id']], 'mode': mode}

    def test_inventory_keeps_one_row_for_each_resource(self):
        write_musicex(self.source, '已有.mflac', 'old123', 'F000Old.mflac')
        shutil.copyfile(self.mp3, self.mp3_dir / '已有.mp3')
        shutil.copyfile(self.original, self.original_dir / '已有.flac')
        write_musicex(self.source, '新歌.mflac', 'new123', 'F000New.mflac')
        write_musicex(self.source, '新歌副本.mflac', 'new123', 'F000New.mflac')
        (self.source / '下载未完成.mflac').write_bytes(b'partial')

        inventory = studio.inventory(self.settings)
        rows = inventory['rows']
        self.assertCountEqual((row['status'] for row in rows), ['success', 'pending', 'incomplete'])
        pending = next(row for row in rows if row['status'] == 'pending')
        self.assertEqual(len(pending['copies']), 1)
        self.assertEqual(inventory['summary']['duplicates'], 1)
        self.assertEqual(Path(pending['source']).parent, self.source.resolve())

    def test_missing_authorization_does_not_read_credentials(self):
        write_musicex(self.source, '未授权.mflac', 'no123', 'F000No.mflac')
        row = self.status_for('未授权.mflac')
        events = []
        with patch.object(studio, 'read_credentials') as credentials, \
                patch.object(studio, 'restore_audio') as restore:
            with self.assertRaisesRegex(studio.StudioError, '授权'):
                studio.convert(self.settings, self.request_for(row, authorized=False), events.append)
        credentials.assert_not_called()
        restore.assert_not_called()
        self.assertEqual(list(self.mp3_dir.iterdir()), [])
        self.assertEqual(list(self.original_dir.iterdir()), [])
        self.assertEqual(self.status_for('未授权.mflac')['status'], 'pending')

    def test_direct_decoder_requires_explicit_permission_for_key_fetch(self):
        source = write_musicex(self.source, '本地检查.mflac', 'local123', 'F000Local.mflac')
        with patch.object(studio.decrypt, 'decrypt_bytes', return_value=(b'audio', {})) as decode:
            self.assertEqual(real_restore_audio(source), b'audio')
            decode.assert_called_once_with(str(source), ekey=None, auto_ekey=False)

    def test_selection_cannot_transfer_to_replaced_source(self):
        filename = '同名版本.mflac'
        write_musicex(self.source, filename, 'older123', 'F000Older.mflac')
        selected = self.status_for(filename)
        write_musicex(self.source, filename, 'newer123', 'F000Newer.mflac')
        events = []
        with patch.object(studio, 'read_credentials') as credentials, \
                patch.object(studio, 'restore_audio') as restore:
            studio.convert(self.settings, self.request_for(selected, authorized=True), events.append)
        credentials.assert_not_called()
        restore.assert_not_called()
        self.assertEqual(events[-1]['skipped'], 1)
        row = self.status_for(filename)
        self.assertNotEqual(row['id'], selected['id'])
        self.assertEqual(row['status'], 'pending')
        self.assertEqual(list(self.mp3_dir.iterdir()), [])
        self.assertEqual(list(self.original_dir.iterdir()), [])

    def test_both_outputs_verify_and_are_not_recreated_on_repeat(self):
        write_musicex(self.source, '待转码.mflac', 'test123', 'F000Convert.mflac')
        row = self.status_for('待转码.mflac')
        payload = self.request_for(row, authorized=True)
        fake_login = types.SimpleNamespace(uin='test-account', authst='offline-placeholder')
        original_bytes = self.original.read_bytes()
        events = []
        with patch.object(studio, 'read_credentials', return_value=fake_login), \
                patch.object(studio, 'restore_audio', return_value=original_bytes) as restore:
            studio.convert(self.settings, payload, events.append)
            restore.assert_called_once_with(Path(row['source']), None, allow_key_fetch=True)
        self.assertEqual(events[-1]['success'], 1, events)

        output = self.mp3_dir / '待转码.mp3'
        original = self.original_dir / '待转码.flac'
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual(list(self.mp3_dir.iterdir()), [output])
        self.assertEqual(list(self.original_dir.iterdir()), [original])
        ffmpeg('-xerror', '-i', output, '-map', '0:a:0', '-f', 'null', '-')
        self.assertEqual(self.status_for('待转码.mflac')['status'], 'success')
        self.assertIn('待转码', self.report.read_text(encoding='utf-8'))
        self.assertTrue(events)

        before = {item: item.stat().st_mtime_ns for item in [output, original]}
        with patch.object(studio, 'read_credentials', return_value=fake_login), \
                patch.object(studio, 'restore_audio') as restore:
            studio.convert(self.settings, payload, events.append)
            restore.assert_not_called()
        self.assertEqual(events[-1]['skipped'], 1, events)
        self.assertEqual({item: item.stat().st_mtime_ns for item in before}, before)
        self.assertEqual(list(self.mp3_dir.iterdir()), [output])
        self.assertEqual(list(self.original_dir.iterdir()), [original])

    def test_original_only_can_be_completed_offline(self):
        filename = '原音保真.mflac'
        write_musicex(self.source, filename, 'raw123', 'F000Original.mflac')
        row = self.status_for(filename)
        fake_login = types.SimpleNamespace(uin='test-account', authst='offline-placeholder')
        original_bytes = self.original.read_bytes()
        events = []
        with patch.object(studio, 'read_credentials', return_value=fake_login), \
                patch.object(studio, 'restore_audio', return_value=original_bytes) as restore:
            studio.convert(self.settings, self.request_for(row, authorized=True, mode='qmc'), events.append)
            restore.assert_called_once()
        self.assertEqual(events[-1]['success'], 1, events)

        original = self.original_dir / '原音保真.flac'
        before = original.stat().st_mtime_ns
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual(list(self.mp3_dir.iterdir()), [])
        inventory = studio.inventory(self.settings)
        self.assertEqual(inventory['summary']['original_ready'], 1)
        row = next(candidate for candidate in inventory['rows'] if candidate['id'] == row['id'])
        self.assertFalse(row['needs_login'])

        with patch.object(studio, 'read_credentials',
                          side_effect=AssertionError('Verified original should bypass login')) as credentials, \
                patch.object(studio, 'restore_audio',
                             side_effect=AssertionError('Verified original should bypass restore')) as restore:
            studio.convert(self.settings, self.request_for(row, authorized=False), events.append)
            credentials.assert_not_called()
            restore.assert_not_called()
        self.assertEqual(events[-1]['success'], 1, events)

        output = self.mp3_dir / '原音保真.mp3'
        ffmpeg('-xerror', '-i', output, '-map', '0:a:0', '-f', 'null', '-')
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual(original.stat().st_mtime_ns, before)
        self.assertEqual(self.status_for(filename)['status'], 'success')

    def test_bad_audio_cleans_temporary_files_and_records_failure(self):
        filename = '坏音源.mflac'
        write_musicex(self.source, filename, 'bad123', 'F000Bad.mflac')
        row = self.status_for(filename)
        fake_login = types.SimpleNamespace(uin='test-account', authst='offline-placeholder')
        events = []
        with patch.object(studio, 'read_credentials', return_value=fake_login), \
                patch.object(studio, 'restore_audio', return_value=b'fLaC' + b'\0' * 250):
            studio.convert(self.settings, self.request_for(row, authorized=True), events.append)
        self.assertEqual(events[-1]['failed'], 1, events)

        self.assertEqual(self.status_for(filename)['status'], 'failed')
        self.assertEqual(list(self.mp3_dir.iterdir()), [])
        self.assertEqual(list(self.original_dir.iterdir()), [])
        self.assertIn('坏音源.mflac', self.report.read_text(encoding='utf-8'))
        self.assertNotIn('offline-placeholder', str(events) + self.state.read_text(encoding='utf-8'))

    def test_export_rolls_back_when_the_second_link_fails(self):
        filename = '发布失败.mflac'
        write_musicex(self.source, filename, 'link123', 'F000Link.mflac')
        row = self.status_for(filename)
        fake_login = types.SimpleNamespace(uin='test-account', authst='offline-placeholder')
        events = []
        real_link = os.link

        def fail_mp3_link(source, destination):
            if destination.suffix == '.mp3':
                raise OSError('Simulated error publishing the MP3')
            return real_link(source, destination)

        with patch.object(studio, 'read_credentials', return_value=fake_login), \
                patch.object(studio, 'restore_audio', return_value=self.original.read_bytes()), \
                patch.object(studio.os, 'link', side_effect=fail_mp3_link) as link:
            studio.convert(self.settings, self.request_for(row, authorized=True), events.append)
        self.assertEqual(link.call_count, 2)
        self.assertEqual(events[-1]['failed'], 1, events)
        self.assertEqual(self.status_for(filename)['status'], 'failed')
        self.assertEqual(list(self.mp3_dir.iterdir()), [])
        self.assertEqual(list(self.original_dir.iterdir()), [])
        self.assertEqual(list(self.export.glob('.qqmusic-convert-*')), [])

    def test_qm_uses_a_different_mp3_preset_with_the_same_original(self):
        paths = [write_musicex(self.source, name, media_id, resource) for name, media_id, resource in [
            ('VBR.mflac', 'vbr123', 'F000VBR.mflac'),
            ('CBR.mflac', 'cbr123', 'F000CBR.mflac'),
        ]]
        fake_login = types.SimpleNamespace(uin='test-account', authst='offline-placeholder')
        original_bytes = self.original.read_bytes()
        events = []
        with patch.object(studio, 'read_credentials', return_value=fake_login), \
                patch.object(studio, 'restore_audio', return_value=original_bytes):
            for path, mode in zip(paths, ('qm', 'tunefree')):
                row = self.status_for(path.name)
                studio.convert(self.settings, self.request_for(row, authorized=True, mode=mode), events.append)
                self.assertEqual(events[-1]['success'], 1, events)

        for path in paths:
            original = self.original_dir / f'{path.stem}.flac'
            self.assertEqual(original.read_bytes(), original_bytes)
            self.assertEqual(self.status_for(path.name)['status'], 'success')
        # A steady test tone is substantially smaller at LAME V2 than CBR 320.
        vbr = self.mp3_dir / 'VBR.mp3'
        cbr = self.mp3_dir / 'CBR.mp3'
        self.assertLess(vbr.stat().st_size, cbr.stat().st_size)
        ffmpeg('-xerror', '-i', vbr, '-map', '0:a:0', '-f', 'null', '-')


if __name__ == '__main__':
    unittest.main()
