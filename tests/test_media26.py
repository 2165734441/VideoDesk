"""Behavior contracts for live capture and translated video output."""
from datetime import datetime
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from integrations import hardware, live, live_browser, translation, translation_components


class Media26Tests(unittest.TestCase):
    def test_cross_midnight_uses_previous_weekday(self):
        windows = [{'days': [4], 'start': '22:00', 'end': '02:00'}]
        self.assertTrue(live.in_window(windows, datetime(2026,11,13,23,0)))
        self.assertTrue(live.in_window(windows, datetime(2026,11,14,1,59)))
        self.assertFalse(live.in_window(windows, datetime(2026,11,14,2,0)))
        self.assertFalse(live.in_window(windows, datetime(2026,11,13,1,0)))

    def test_daily_and_full_day_window(self):
        self.assertTrue(live.in_window([],datetime(2026,11,13,20)))
        self.assertTrue(live.in_window([{'days':[4],'start':'00:00','end':'00:00'}],datetime(2026,11,13,20)))
        self.assertFalse(live.in_window([{'days':[3],'start':'00:00','end':'00:00'}],datetime(2026,11,13,20)))

    def test_schedule_rejects_invalid_times_and_weekdays(self):
        for value in ({'start':'24:00'},{'end':'9:00'},{'days':[7]},{'days':[]}):
            with self.subTest(value=value), self.assertRaises(ValueError): live.Window(**value)

    def test_accepts_share_text_and_profiles(self):
        self.assertEqual(live_browser.normalize('复制 https://v.douyin.com/abcdef/ 去看'), 'https://v.douyin.com/abcdef/')
        self.assertEqual(live_browser.normalize('https://live.douyin.com/123456'), 'https://live.douyin.com/123456')

    def test_rejects_non_douyin_and_published_videos(self):
        for url in ('http://localhost/a', 'https://live.douyin.com.evil.test/a', 'https://user@live.douyin.com/12',
                    'https://www.douyin.com/video/123','https://live.douyin.com:9000/123'):
            with self.subTest(url=url), self.assertRaises(ValueError): live_browser.normalize(url)

    def test_stream_quality_selection(self):
        payload={'data':{'status':2,'id_str':'42','owner':{'nickname':'test'},'stream_url':{'flv_pull_url':{'ORIGIN':'https://media/original','SD1':'https://media/standard'}}}}
        self.assertEqual(live_browser.room_data(payload,'standard')['url'],'https://media/standard')
        self.assertEqual(live_browser.room_data(payload)['room_id'],'42')
        payload['data']['status']=4
        self.assertFalse(live_browser.room_data(payload)['online'])

    def test_profile_resolution_requires_exact_identity(self):
        payload={'user':{'sec_uid':'requested123','room_data':'{"web_rid":"123456"}'},'recommendation':{'sec_uid':'other123456','room_data':'{"web_rid":"999999"}'}}
        self.assertEqual(live_browser.profile_room(payload,'requested123'),'https://live.douyin.com/123456')
        self.assertIsNone(live_browser.profile_room(payload,'unknown123'))

    def test_following_deduplicates_by_stable_id(self):
        payload={'followings':[{'sec_uid':'MS4wLjABCDEF','nickname':'甲'},{'sec_uid':'MS4wLjABCDEF','nickname':'甲新名'},{'sec_uid':'bad/../../','nickname':'bad'}]}
        users=live_browser.following_data(payload)
        self.assertEqual(len(users),1); self.assertEqual(users[0]['name'],'甲新名')

    def test_translation_requires_exactly_one_source_kind(self):
        for req in ({},{'paths':['x.mp4'],'source_job':'job','relative':'part.mp4'},{'paths':['a','b'],'subtitles':'one.srt'}):
            with self.assertRaises(ValueError): translation.TranslateRequest(**req)

    def test_subtitle_validation_rejects_overlaps_nan_and_overruns(self):
        for rows in ([{'start':float('nan'),'end':2,'en':'x'}], [{'start':1,'end':12,'en':'x'}],
                     [{'start':0,'end':3,'en':'x'},{'start':2,'end':4,'en':'y'}]):
            with self.assertRaises(ValueError): translation.validate_segments(rows,10)

    def test_empty_chinese_is_not_accepted_for_render(self):
        with self.assertRaises(ValueError): translation.validate_segments([{'start':0,'end':2,'en':'hello','zh':''}],3,True)

    def test_subtitle_order_and_ass_injection_escaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows=[{'start':0,'end':1,'en':r'English {\p1}', 'zh':'中文'}]
            translation.subtitle_files(tmp,rows)
            text=(Path(tmp)/'bilingual.ass').read_text('utf-8-sig')
            event=text.split('Dialogue: ')[1]
            self.assertLess(event.index('English'),event.index('中文'))
            self.assertNotIn(r'{\p1}',event)
            self.assertIn('\\N',event)

    def test_parse_srt_with_chinese_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'英文字幕.srt';path.write_text('1\n00:00:00,000 --> 00:00:01,500\nHello\n\n2\n00:00:02,000 --> 00:00:03,000\nWorld\n',encoding='utf-8-sig')
            rows=translation.parse_subtitles(path,4)
            self.assertEqual([r['en'] for r in rows],['Hello','World'])

    def test_encoder_falls_back_and_preserves_output_arguments(self):
        calls=[];changed=[]
        def ff(jid,args):
            calls.append(args)
            if 'h264_nvenc' in args: raise RuntimeError('device removed')
        a=SimpleNamespace(FFMPEG='test',run_ffmpeg=ff,check_cancel=lambda jid:None,change=lambda jid,**v:changed.append(v))
        with patch.object(hardware,'capabilities',return_value={'encoder':'h264_nvenc'}):
            self.assertEqual(hardware.encode(a,'j',['-i','source'],['-c:a','aac','out.mp4']),'libx264')
        self.assertEqual(calls[0][-3:],calls[1][-3:]);self.assertTrue(any(v.get('encoder_fallback') for v in changed))

    def test_cpu_does_not_probe_gpu(self):
        a=SimpleNamespace(FFMPEG='test',run_ffmpeg=lambda *v:None,check_cancel=lambda jid:None,change=lambda *v,**kw:None)
        with patch.object(hardware,'capabilities',side_effect=AssertionError('GPU probe')):
            self.assertEqual(hardware.encode(a,'j',[],['out'],'cpu'),'libx264')

    def test_cancel_does_not_retry_hardware_failure(self):
        class Cancelled(Exception):pass
        a=SimpleNamespace(FFMPEG='test',run_ffmpeg=lambda *v:(_ for _ in ()).throw(Cancelled()),
                          check_cancel=lambda jid:(_ for _ in ()).throw(Cancelled()),change=lambda *v,**kw:None)
        with patch.object(hardware,'capabilities',return_value={'encoder':'h264_nvenc'}),self.assertRaises(Cancelled): hardware.encode(a,'j',[],['out'])

    def test_corrupted_model_fails_before_native_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'translation-models/english-asr';folder.mkdir(parents=True)
            path=folder/'model.bin';path.write_bytes(b'good')
            (folder/'verified.json').write_text(json.dumps({'files':{'model.bin':{'sha256':translation_components.digest(path),'bytes':4}}}))
            translation_components.verify(tmp,'english-asr')
            path.write_bytes(b'evil')
            with self.assertRaises(ValueError): translation_components.verify(tmp,'english-asr')

    def test_offline_import_rejects_wrong_hash_and_zip_traversal(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'bad.zip'
            with zipfile.ZipFile(source,'w') as package:package.writestr('../../model.bin',b'evil')
            with self.assertRaises(ValueError):translation_components.import_models(tmp,source,lambda **v:None,lambda:None)
            self.assertFalse((Path(tmp).parent/'model.bin').exists())

    def test_cloud_remote_plaintext_rejected(self):
        with self.assertRaises(ValueError): translation.cloud_request({'base_url':'http://remote.test/v1'},'/audio/speech')

    def test_narration_does_not_truncate_overlong_voice(self):
        import wave
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'voice.wav'
            with wave.open(str(source),'wb') as out:
                out.setnchannels(1);out.setsampwidth(2);out.setframerate(44100);out.writeframes(b'\0'*44100*4)
            with self.assertRaises(ValueError):translation.narration([{'start':0,'voice':str(source)}],1,Path(tmp)/'result.wav',lambda:None)


if __name__ == '__main__': unittest.main()
