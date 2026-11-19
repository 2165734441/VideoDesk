"""Regressions from the 2026-11-19 deep test round.

BUG-02: a manual recording that already saved a segment reported error with
"没有取得直播流" when the page died after the stream ended.
BUG-03: an anchor that was only being checked could not be deleted, because the
route treated any active/pending job as a recording that must be stopped first.
"""
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

from integrations import live


def fake_app(root):
    return SimpleNamespace(
        DATA=root, SETTINGS={'output': str(root / 'out')}, JOBS={}, EVENTS={},
        LOCK=threading.RLock(), PROCESSES={}, BROWSER=None,
        load_json=lambda path, default: default,
        new_job=Mock(return_value={'id': 'job1', 'directory': str(root / 'job1')}),
        change=Mock(), job_by_id=Mock(return_value={'message': '', 'directory': str(root)}),
        safe_file=Mock(), refresh_partial_files=Mock(), refresh_files=Mock(),
        drop_job=Mock(return_value=True), material_history=Mock())


class LiveEnding(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.app = fake_app(self.root)
        self.manager = live.Manager(self.app)
        self.anchor = {'id': 'a', 'name': '', 'url': 'https://live.douyin.com/123456',
                       'enabled': True, 'quality': 'origin', 'windows': [],
                       'monitor_device': 'pc', 'record_target': 'pc'}
        self.manager.anchors['a'] = self.anchor
        # A real Event would make the retry backoff sleep for ten seconds.
        self.app.EVENTS['job1'] = Mock(is_set=lambda: False, wait=lambda timeout: False)

    def statuses(self):
        return [c.kwargs['status'] for c in self.app.change.call_args_list if 'status' in c.kwargs]

    def record(self, automatic, resolve):
        def capture(jid, info, target, duration, on_first_bytes=None, stop_when=None):
            target.write_bytes(b'x' * 4096)
            if on_first_bytes: on_first_bytes()
            return 0, False, True
        self.manager.capture_segment = capture
        with patch.object(live.live_browser, 'resolve', side_effect=resolve):
            self.manager.record('a', self.anchor, 'job1', automatic=automatic)

    def test_saved_segment_then_dead_page_finishes_as_done(self):
        calls = []

        def resolve(*args, **kwargs):
            calls.append(1)
            if len(calls) > 1: raise ValueError('直播页面连接失败：ERR_CONNECTION_CLOSED')
            return {'online': True, 'room_id': '7700000000000000000', 'url': 'https://x'}

        self.record(False, resolve)
        self.assertIn('done', self.statuses())
        self.assertNotIn('error', self.statuses())
        saved = json_ok(self.root / 'replay.json')
        self.assertEqual(len(saved['segments']), 1)

    def test_automatic_check_keeps_retrying_before_giving_up(self):
        """A short blip must not end a recording that is still going."""
        calls = []

        def resolve(*args, **kwargs):
            calls.append(1)
            if len(calls) > 1: raise ValueError('读取超时')
            return {'online': True, 'room_id': '7700000000000000000', 'url': 'https://x'}

        self.record(True, resolve)
        # Two failures in a row are below the automatic threshold, so the job
        # keeps recording instead of being closed on the first hiccup.
        self.assertGreaterEqual(len(calls), 2)
        self.assertNotIn('error', self.statuses())

    def test_nothing_captured_still_reports_the_failure(self):
        def resolve(*args, **kwargs):
            raise ValueError('直播间读取队列超时，请减少同时检查的主播并重试')

        self.record(False, resolve)
        self.assertIn('error', self.statuses())


def json_ok(path):
    import json
    return json.loads(Path(path).read_text(encoding='utf-8'))


class AnchorDelete(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.app = fake_app(self.root)
        self.manager = live.Manager(self.app)
        self.manager.anchors['a'] = {'id': 'a', 'name': 't', 'url': 'https://live.douyin.com/1',
                                     'enabled': True, 'quality': 'origin', 'windows': [],
                                     'monitor_device': 'phone', 'record_target': 'phone'}

    def test_delete_while_only_checking_cancels_the_check(self):
        self.app.EVENTS['job1'] = Mock()
        self.app.job_by_id = Mock(return_value={'status': 'running', 'directory': str(self.root)})
        self.manager.active['a'] = 'job1'
        self.manager.runtime['a'] = {'state': 'error', 'message': '主播未开播'}
        self.assertEqual(self.manager.remove('a'), {'ok': True})
        self.assertNotIn('a', self.manager.anchors)
        self.assertNotIn('a', self.manager.active)
        self.app.EVENTS['job1'].set.assert_called_once()

    def test_delete_while_recording_is_refused(self):
        self.app.job_by_id = Mock(return_value={'status': 'recording', 'directory': str(self.root)})
        self.manager.active['a'] = 'job1'
        self.manager.runtime['a'] = {'state': 'recording', 'message': '录制中', 'device': 'pc'}
        with self.assertRaises(HTTPException) as raised:
            self.manager.remove('a')
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn('a', self.manager.anchors)

    def test_delete_of_phone_recording_is_refused(self):
        self.manager.runtime['a'] = {'state': 'recording', 'message': '手机正在录制', 'device': 'phone'}
        with self.assertRaises(HTTPException):
            self.manager.remove('a')
        self.assertIn('a', self.manager.anchors)


if __name__ == '__main__':
    unittest.main()
