"""Regressions for the supplied independent VD26 report."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from integrations import live, live_browser, media_probe, translation

ROOT = Path(__file__).resolve().parents[1]


class Report26Regressions(unittest.TestCase):
    def test_damage_messages_share_one_detector(self):
        for text in ('File ended prematurely', 'premature EOF', 'Packet corrupt', 'Error while decoding', 'partial file'):
            self.assertTrue(media_probe.damaged_log(text))
            self.assertTrue(media_probe.damaged_log(text.encode()))
        self.assertFalse(media_probe.damaged_log('frame=90 video:231KiB audio:45KiB'))

    def test_transport_tail_partial_packet_and_complete_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            for width, prefix in ((188, 0), (192, 4), (204, 0)):
                packet = b'\0'*prefix+b'\x47'+b'\0'*(width-prefix-1)
                source = Path(tmp)/'capture.ts'; source.write_bytes(packet*12)
                media_probe.check_transport_tail(source)
                source.write_bytes((packet*12)[:-4])
                with self.assertRaises(media_probe.ProbeError): media_probe.check_transport_tail(source)

    def test_non_transport_file_is_not_rejected_by_packet_checker(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.mp4';source.write_bytes(b'ordinary-container-header'*200)
            media_probe.check_transport_tail(source)

    def test_room_tracking_variants_have_one_identity(self):
        canonical='https://live.douyin.com/123456789'
        for url in (canonical+'?from=share',canonical+'/#fragment','http://live.douyin.com/123456789'):
            self.assertEqual(live_browser.normalize(url),canonical)
        with tempfile.TemporaryDirectory() as tmp:
            a=SimpleNamespace(DATA=Path(tmp),JOBS={},load_json=lambda p,d:d)
            manager=live.Manager(a)
            try:
                manager.put(live.Anchor(url=canonical))
                with self.assertRaises(HTTPException) as caught: manager.put(live.Anchor(url=canonical+'?from=share'))
                self.assertEqual(caught.exception.status_code,409)
            finally:manager.close()

    def test_subtitle_encoding_is_actionable_and_utf16_bom_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'subtitle.srt'
            path.write_bytes(b'1\n00:00:00,000 --> 00:00:02,000\n\xff\xfe\x80bad utf8\n')
            with self.assertRaisesRegex(ValueError,'UTF-8'):translation.parse_subtitles(path,3)
            path.write_text('1\n00:00:00,000 --> 00:00:02,000\nHello\n',encoding='utf-16')
            self.assertEqual(translation.parse_subtitles(path,3)[0]['en'],'Hello')

    def test_cloud_input_errors_precede_component_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=FastAPI();a=SimpleNamespace(DATA=Path(tmp),load_json=lambda p,d:d)
            pipeline=translation.install(app,a)
            try:
                create=next(r.endpoint for r in app.routes if r.path=='/api/translation')
                with self.assertRaises(HTTPException) as caught:create(translation.TranslateRequest(paths=[str(Path(tmp)/'missing.mp4')],backend='cloud'))
                self.assertEqual(caught.exception.status_code,400)
                self.assertIn('存在的本地视频',caught.exception.detail)
            finally:pipeline.close()

    def test_cloud_background_missing_offers_explicit_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=FastAPI();a=SimpleNamespace(DATA=Path(tmp),load_json=lambda p,d:d)
            pipeline=translation.install(app,a);source=Path(tmp)/'source.mp4';source.write_bytes(b'fixture')
            try:
                create=next(r.endpoint for r in app.routes if r.path=='/api/translation')
                response=create(translation.TranslateRequest(paths=[str(source)],backend='cloud'))
                self.assertEqual(response.status_code,409)
                body=json.loads(response.body)
                self.assertTrue(body['can_disable_background'])
                self.assertEqual(body['code'],'background_component_required')
            finally:pipeline.close()

    def test_cloud_without_background_does_not_require_local_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=FastAPI();a=SimpleNamespace(DATA=Path(tmp),load_json=lambda p,d:d,new_job=lambda *args,**kwargs:{'id':'cloud-job'})
            pipeline=translation.install(app,a);source=Path(tmp)/'source.mp4';source.write_bytes(b'fixture')
            try:
                create=next(r.endpoint for r in app.routes if r.path=='/api/translation')
                with patch.object(pipeline,'schedule') as scheduled:
                    response=create(translation.TranslateRequest(paths=[str(source)],backend='cloud',background=False))
                    self.assertEqual(response['id'],'cloud-job')
                    scheduled.assert_called_once_with('cloud-job')
            finally:pipeline.close()

    def test_release_checksum_detects_rebuilt_binary(self):
        spec=importlib.util.spec_from_file_location('build26check',ROOT/'scripts/build_windows_exe.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);binary=folder/'VideoDesk-test-Windows-x64.exe';binary.write_bytes(b'first')
            module.write_release_checksums(folder,'test');module.verify_release_checksums(folder,'test')
            binary.write_bytes(b'rebuilt')
            with self.assertRaises(RuntimeError):module.verify_release_checksums(folder,'test')
            module.write_release_checksums(folder,'test');module.verify_release_checksums(folder,'test')
            self.assertIn(hashlib.sha256(b'rebuilt').hexdigest(),(folder/'VideoDesk-test-SHA256SUMS.txt').read_text())

    def test_cli_help_uses_shared_version(self):
        node=ROOT/'.local/build-exe/payload/runtime/node/node.exe'
        if not node.is_file():self.skipTest('Bundled Node not available')
        result=subprocess.run([str(node),str(ROOT/'bin/video-desk.mjs'),'help'],capture_output=True,text=True,encoding='utf-8',timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('Video Desk '+json.loads((ROOT/'version.json').read_text())['version'],result.stdout)


if __name__=='__main__':unittest.main()
