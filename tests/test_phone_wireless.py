"""Unplugged-cable behaviour: the phone bridge must survive on Wi-Fi.

The phone always calls 127.0.0.1:47631, and the desktop forwards that port over
whichever ADB transport is alive. These tests pin the rules that keep that path
working without a cable:
  * one physical phone is listed once, even when USB and Wi-Fi both answer;
  * the reverse mapping is always rebuilt on the transport that can survive an
    unplug (Wi-Fi first, USB as the fallback);
  * a silent phone is reconnected before anything declares it offline.
"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from integrations import mobile

USB = '10ADA41UT7002GG'
WIFI = '192.168.0.11:5555'


class FakeDesk:
    def __init__(self, root):
        self.DATA = Path(root)
        self.ROOT = Path(root)

    def load_json(self, path, default=None):
        try:
            return json.loads(Path(path).read_text(encoding='utf-8'))
        except Exception:
            return default


class WirelessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.desk = FakeDesk(self.tmp.name)
        self.phone = mobile.Companion(self.desk)
        self.phone.server = SimpleNamespace(server_port=7641)
        self.calls = []

    def fake_adb(self, output=None, reach=None):
        output = output or {}

        def run(*args, timeout=25):
            self.calls.append(list(args))
            key = ' '.join(args)
            for pattern, value in output.items():
                if pattern in key:
                    if isinstance(value, Exception):
                        raise value
                    return value
            return ''

        self.phone.adb = run
        self.phone.reachable = reach or (lambda serial: serial in (USB, WIFI))

    def test_usb_and_wifi_are_one_device(self):
        self.fake_adb({'devices': 'List of devices attached\n%s device model:V2304A\n%s device model:V2304A\n' % (USB, WIFI)})
        self.phone.wireless = WIFI
        listed = self.phone.devices()
        self.assertEqual([d['serial'] for d in listed], [USB])
        self.assertFalse(listed[0]['wireless'])

    def test_only_the_paired_wifi_entry_survives_unplug(self):
        stale = '192.168.0.99:5555'
        self.fake_adb({'devices': 'List of devices attached\n%s device model:V2304A\n%s device model:V2304A\n' % (stale, WIFI)})
        self.phone.wireless = WIFI
        self.assertEqual([d['serial'] for d in self.phone.devices()], [WIFI])

    def test_wifi_is_preferred_while_it_answers(self):
        self.phone.paired_serial = USB
        self.phone.wireless = WIFI
        self.fake_adb()
        self.assertEqual(self.phone.preferred(), WIFI)
        self.phone.reachable = lambda serial: serial == USB
        self.assertEqual(self.phone.preferred(), USB)

    def test_reverse_is_rebuilt_on_the_surviving_transport(self):
        self.phone.wireless = WIFI
        self.fake_adb()
        self.assertTrue(self.phone.apply_reverse())
        self.assertIn(['-s', WIFI, 'reverse', 'tcp:47631', 'tcp:7641'], self.calls)
        self.assertEqual(self.phone.reverse_on, WIFI)

    def test_silent_phone_reconnects_over_wifi(self):
        self.phone.paired_serial = USB
        self.phone.wireless = WIFI
        self.phone.last_seen = time.time() - 120
        seen = set()

        def reach(serial):
            return serial == USB or serial in seen

        def run(*args, timeout=25):
            self.calls.append(list(args))
            if args[:1] == ('connect',):
                seen.add(args[1])
        self.phone.adb = run
        self.phone.reachable = reach
        self.assertTrue(self.phone.reconnect())
        self.assertEqual(self.phone.reverse_on, WIFI)
        self.assertIn(['connect', WIFI], self.calls)

    def test_fresh_link_is_left_alone(self):
        self.phone.paired_serial = USB
        self.phone.last_seen = time.time()
        self.fake_adb()
        self.assertFalse(self.phone.reconnect())
        self.assertEqual(self.calls, [])

    def test_pulling_the_cable_moves_the_forward_at_once(self):
        self.phone.paired_serial = USB
        self.phone.wireless = WIFI
        self.phone.reverse_on = USB
        self.phone.last_seen = time.time()
        self.fake_adb()
        self.phone.reachable = lambda serial: serial == WIFI
        self.assertTrue(self.phone.keep_link())
        self.assertEqual(self.phone.reverse_on, WIFI)

    def test_cable_still_plugged_keeps_usb(self):
        self.phone.paired_serial = USB
        self.phone.wireless = WIFI
        self.phone.reverse_on = USB
        self.phone.last_seen = time.time()
        self.fake_adb()
        self.phone.reachable = lambda serial: serial in (USB, WIFI)
        self.phone.keep_link()
        self.assertEqual(self.phone.reverse_on, USB)

    def test_wireless_needs_a_wifi_address(self):
        self.phone.paired_serial = USB
        self.fake_adb({'devices': 'List of devices attached\n%s device\tdevice model:V2304A\n' % USB,
                       'addr show wlan0': ''})
        with self.assertRaises(HTTPException) as caught:
            self.phone.enable_wireless()
        self.assertIn('Wi-Fi', str(caught.exception.detail))

    def test_wireless_is_remembered_after_it_connects(self):
        self.phone.paired_serial = USB
        self.fake_adb({'devices': 'List of devices attached\n%s device\tdevice model:V2304A\n' % USB,
                       'addr show wlan0': '    inet 192.168.0.11/24 brd 192.168.0.255 scope global wlan0'})
        self.phone.reachable = lambda serial: True
        result = self.phone.enable_wireless()
        self.assertEqual(self.phone.wireless, WIFI)
        self.assertIn('拔掉数据线', result['message'])
        saved = json.loads((self.desk.DATA / 'credentials/mobile-pair.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['wireless'], WIFI)
        self.assertEqual(self.phone.reverse_on, WIFI)

    def test_first_launch_tries_wireless_once(self):
        self.phone.paired_serial = USB
        self.phone.stopping.wait = lambda timeout: False
        self.fake_adb()
        self.phone.enable_wireless = lambda serial='': setattr(self.phone, 'wireless', WIFI)
        self.phone.initial_link()
        self.assertEqual(self.phone.wireless, WIFI)

    def test_learned_address_skips_the_attempt(self):
        self.phone.paired_serial = USB
        self.phone.wireless = WIFI
        self.phone.stopping.wait = lambda timeout: False
        self.fake_adb()
        tried = []
        self.phone.enable_wireless = lambda serial='': tried.append(serial)
        self.phone.initial_link()
        self.assertEqual(tried, [])

    def test_state_reports_the_active_transport(self):
        self.phone.wireless = WIFI
        self.phone.reverse_on = WIFI
        self.fake_adb({'devices': 'List of devices attached\n'})
        state = self.phone.state()
        self.assertEqual(state['wireless'], {'address': WIFI, 'on_wifi': True})
        self.phone.reverse_on = USB
        self.assertFalse(self.phone.state()['wireless']['on_wifi'])


class ReviveTests(unittest.TestCase):
    """A frozen companion must be woken before a live start is lost."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        desk = FakeDesk(self.tmp.name)
        self.phone = mobile.Companion(desk)
        self.phone.paired_serial = USB
        self.phone.reachable = lambda serial: True
        self.calls = []

    def fake_adb(self, wakefulness='Asleep'):
        def run(*args, timeout=25):
            self.calls.append(list(args))
            if 'dumpsys' in args:
                return 'mWakefulness=' + wakefulness
            return ''
        self.phone.adb = run

    def started(self):
        return any('am' in call and 'start' in call for call in self.calls)

    def test_screen_off_silence_wakes_the_app(self):
        self.fake_adb('Asleep')
        self.phone.last_seen = time.time() - 40
        self.assertTrue(self.phone.revive())
        self.assertTrue(self.started())

    def test_recent_heartbeat_is_left_alone(self):
        self.fake_adb('Asleep')
        self.phone.last_seen = time.time() - 5
        self.assertFalse(self.phone.revive())
        self.assertFalse(self.started())

    def test_busy_phone_is_not_interrupted_early(self):
        self.fake_adb('Awake')
        self.phone.last_seen = time.time() - 60
        self.assertFalse(self.phone.revive())
        self.assertFalse(self.started())

    def test_long_silence_wins_over_a_lit_screen(self):
        self.fake_adb('Awake')
        self.phone.last_seen = time.time() - 200
        self.assertTrue(self.phone.revive())
        self.assertTrue(self.started())

    def test_repeat_wakes_are_throttled(self):
        self.fake_adb('Asleep')
        self.phone.last_seen = time.time() - 40
        self.assertTrue(self.phone.revive())
        self.calls.clear()
        self.phone.last_seen = time.time() - 40
        self.assertFalse(self.phone.revive())
        self.assertFalse(self.started())

    def test_backgrounded_phone_still_gets_the_command(self):
        import threading
        self.phone.a.LIVE = SimpleNamespace(anchors={'a': {'id': 'a', 'name': '主播'}},
                                           lock=threading.RLock())
        self.fake_adb('Awake')
        self.phone.last_seen = time.time() - 40
        self.phone.request_record('a')
        self.assertEqual(len(self.phone.commands), 1)
        self.assertTrue(self.started())

    def test_unpaired_phone_is_refused(self):
        import threading
        self.phone.a.LIVE = SimpleNamespace(anchors={'a': {'id': 'a', 'name': '主播'}},
                                           lock=threading.RLock())
        self.fake_adb('Awake')
        self.phone.paired_serial = ''
        self.phone.last_seen = 0
        with self.assertRaises(HTTPException):
            self.phone.request_record('a')
        self.assertEqual(self.phone.commands, [])

    def test_paired_phone_after_restart_is_woken(self):
        import threading
        self.phone.a.LIVE = SimpleNamespace(anchors={'a': {'id': 'a', 'name': '主播'}},
                                           lock=threading.RLock())
        self.fake_adb('Awake')
        self.phone.paired_serial = USB
        self.phone.last_seen = 0
        self.phone.request_record('a')
        self.assertEqual(len(self.phone.commands), 1)
        self.assertTrue(self.started())

    def listening(self):
        return any('allow_listener' in call for call in self.calls)

    def test_update_leaves_listener_unbound_and_is_rebound(self):
        # A freshly installed companion keeps the permission entry but the
        # system never connects the listener, so the heartbeat dies silently.
        self.fake_adb('Awake')
        self.assertTrue(self.phone.ensure_listener())
        self.assertTrue(self.listening())

    def test_listener_rebind_is_throttled(self):
        self.fake_adb('Awake')
        self.assertTrue(self.phone.ensure_listener())
        self.calls.clear()
        self.assertFalse(self.phone.ensure_listener())
        self.assertFalse(self.listening())

    def test_silent_phone_gets_listener_rebound_by_keep_link(self):
        self.fake_adb('Awake')
        self.phone.server = SimpleNamespace()
        self.phone.reconnect = lambda: False
        self.phone.last_seen = time.time() - 120
        self.phone.keep_link()
        self.assertTrue(self.listening())


if __name__ == '__main__':
    unittest.main()
