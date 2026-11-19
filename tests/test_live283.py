"""Desktop monitoring must not depend on where recording happens."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from integrations import live


def fake_app(root):
    return SimpleNamespace(
        DATA=root, SETTINGS={'output': str(root / 'out')}, JOBS={}, EVENTS={},
        LOCK=threading.RLock(), PROCESSES={}, BROWSER=None,
        load_json=lambda path, default: default,
        new_job=Mock(return_value={'id': 'job1', 'directory': str(root / 'job1')}),
        change=Mock(), job_by_id=Mock(return_value={'message': '', 'directory': str(root)}),
        safe_file=Mock(), refresh_partial_files=Mock(), refresh_files=Mock(),
        drop_job=Mock(return_value=True),
        material_history=Mock())


class MonitorPolicy(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.app = fake_app(self.root)
        self.manager = live.Manager(self.app)

    def anchor(self, **overrides):
        item = {'id': 'a', 'name': '', 'url': 'https://live.douyin.com/123456', 'enabled': True,
                'quality': 'origin', 'windows': [], 'monitor_device': 'phone', 'record_target': 'phone'}
        item.update(overrides)
        self.manager.anchors['a'] = item
        return item

    def test_phone_only_anchor_is_still_checked_by_desktop(self):
        """No companion answering means nobody would notice a live start."""
        self.anchor(monitor_device='phone')
        self.manager.start = Mock(return_value={})
        self.manager.tick()
        self.manager.start.assert_called_once_with('a', automatic=True)

    def test_desktop_stops_checking_while_the_phone_monitors(self):
        self.anchor(monitor_device='phone')
        self.app.MOBILE = SimpleNamespace(last_seen=time.time(), lock=threading.RLock(),
                                         phone_status={'monitoring': True})
        self.manager.start = Mock(return_value={})
        self.manager.tick()
        self.manager.start.assert_not_called()

    def test_anchor_already_recording_on_the_phone_is_not_checked(self):
        self.anchor(monitor_device='phone')
        self.app.MOBILE = SimpleNamespace(last_seen=time.time(), lock=threading.RLock(),
                                         phone_status={'monitoring': False,
                                                       'active': [{'anchor_id': 'a', 'bytes': 4096}]})
        self.manager.start = Mock(return_value={})
        self.manager.tick()
        self.manager.start.assert_not_called()

    def test_stale_phone_flag_does_not_silence_the_desktop(self):
        self.anchor(monitor_device='phone')
        self.app.MOBILE = SimpleNamespace(last_seen=time.time() - 300, lock=threading.RLock(),
                                         phone_status={'monitoring': True, 'active': [{'anchor_id': 'a'}]})
        self.manager.start = Mock(return_value={})
        self.manager.tick()
        self.manager.start.assert_called_once_with('a', automatic=True)

    def test_disabled_anchor_is_not_checked(self):
        self.anchor(enabled=False)
        self.manager.start = Mock(return_value={})
        self.manager.tick()
        self.manager.start.assert_not_called()

    def test_stop_keeps_monitoring_and_only_suppresses_this_room(self):
        self.anchor()
        self.manager.runtime['a'] = {'state': 'recording', 'room_id': '7700000000000000000'}
        with self.manager.lock:
            self.manager.stop('a')
        self.assertTrue(self.manager.anchors['a']['enabled'])
        self.assertEqual(self.manager.suppressed['a'], '7700000000000000000')

    def test_phone_capture_requires_matching_anchor(self):
        self.app.MOBILE = SimpleNamespace(last_seen=time.time(), lock=threading.RLock(),
                                         phone_status={'active': [{'anchor_id': 'a', 'bytes': 4096}]})
        self.assertEqual(self.manager.phone_capture('a')['bytes'], 4096)
        self.assertIsNone(self.manager.phone_capture('b'))

    def test_unpaired_phone_falls_back_to_desktop(self):
        self.assertFalse(self.manager.notify_phone('a', 'record', {'url': 'https://x'}))

    def offline_job(self):
        base = self.root / 'job1'
        base.mkdir(exist_ok=True)
        job = {'id': 'job1', 'directory': str(base), 'message': '当前未开播，自动监控会继续检查'}
        self.app.job_by_id = Mock(return_value=job)
        self.app.EVENTS['job1'] = threading.Event()
        return base

    def test_prune_removes_only_empty_automatic_checks(self):
        empty = self.root / 'empty'
        empty.mkdir()
        real = self.root / 'real'
        real.mkdir()
        (real / 'part-0001.mkv').write_bytes(b'x' * 10)
        self.app.SESSION_LOCKS = {}
        self.app.persist = Mock()
        self.app.EVENTS.update({k: threading.Event() for k in ('j1', 'j2', 'j3', 'j4')})
        base = {'kind': 'live-recording', 'status': 'error', 'replay': [], 'options': {'automatic': True}}
        self.app.JOBS.update({
            'j1': dict(base, id='j1', directory=str(empty)),
            'j2': dict(base, id='j2', directory=str(empty), options={'automatic': False}),
            'j3': dict(base, id='j3', directory=str(real)),
            'j4': {'id': 'j4', 'kind': 'dedup', 'status': 'error', 'replay': [], 'directory': str(empty), 'options': {}},
        })
        self.assertEqual(self.manager.prune_empty_checks(), 1)
        self.assertEqual(sorted(self.app.JOBS), ['j2', 'j3', 'j4'])
        self.assertFalse(empty.exists())

    def test_automated_check_without_capture_leaves_no_job_or_folder(self):
        # A minute-by-minute check that captured nothing must not leave a card
        # and an empty folder behind, otherwise the list fills with noise.
        base = self.offline_job()
        with patch.object(live.live_browser, 'resolve', return_value={'online': False}):
            self.manager.record('a', self.anchor(), 'job1', automatic=True)
        self.app.drop_job.assert_called_once_with('job1', remove_directory=True)
        self.assertEqual(self.manager.runtime['a']['state'], 'idle')
        self.assertIn('未开播', self.manager.runtime['a']['message'])
        self.assertIn('检查于', self.manager.runtime['a']['message'])

    def test_manual_check_without_capture_keeps_the_explanation(self):
        self.offline_job()
        with patch.object(live.live_browser, 'resolve', return_value={'online': False}):
            self.manager.record('a', self.anchor(), 'job1', automatic=False)
        self.app.drop_job.assert_not_called()

    def test_captured_segment_survives_remux_failure(self):
        base = self.offline_job()

        def capture(jid, info, target, duration, on_first_bytes=None, stop_when=None):
            target.write_bytes(b'x' * 5000)
            if on_first_bytes:
                on_first_bytes()
            return 0, True, True

        self.manager.capture_segment = capture
        info = {'online': True, 'room_id': '7700000000000000000', 'url': 'https://x'}
        with patch.object(live.live_browser, 'resolve', return_value=info):
            self.manager.record('a', self.anchor(), 'job1', automatic=True)
        # A failed remux (no ffmpeg here) must not throw away the recording.
        self.app.drop_job.assert_not_called()
        self.assertTrue((base / 'replay.json').is_file())
        saved = json.loads((base / 'replay.json').read_text(encoding='utf-8'))
        self.assertEqual(len(saved['segments']), 1)

    def test_desktop_hands_the_room_to_a_phone_that_starts_late(self):
        """A phone joining mid-segment used to leave two copies of one live."""
        base = self.offline_job()
        phone = SimpleNamespace(last_seen=time.time(), lock=threading.RLock(),
                                phone_status={'active': []})
        self.app.MOBILE = phone
        fired = []

        def capture(jid, info, target, duration, on_first_bytes=None, stop_when=None):
            target.write_bytes(b'x' * 5000)
            if on_first_bytes: on_first_bytes()
            phone.phone_status['active'] = [{'anchor_id': 'a', 'bytes': 1024}]
            fired.append(stop_when() if stop_when else None)
            return 0, True, True

        self.manager.capture_segment = capture
        anchor = self.anchor(record_target='phone')
        info = {'online': True, 'room_id': '7700000000000000000', 'url': 'https://x'}
        with patch.object(live.live_browser, 'resolve', return_value=info):
            self.manager.record('a', anchor, 'job1', automatic=True)
        self.assertEqual(fired, [True])
        self.assertEqual(self.manager.runtime['a']['device'], 'phone')
        self.assertIn('手机', self.manager.runtime['a']['message'])
        # Whatever landed before the hand-over stays in the job.
        self.assertTrue((base / 'replay.json').is_file())


if __name__ == '__main__':
    unittest.main()
