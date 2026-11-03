"""Credential-scoped streaming and manual takeover regression checks."""
import http.cookiejar
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import io
import tarfile
from urllib.request import Request,urlopen
from types import SimpleNamespace
from integrations.media_relay import MediaRelay
from integrations.storyboard import Engine,TakeoverRequired
from integrations import ai_models
from integrations.asr_worker import transcribe_service


class RelayTests(unittest.TestCase):
    def test_ranges_hls_relative_resources_and_cookie_domain(self):
        seen=[]
        class Origin(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_GET(self):
                seen.append({'path':self.path,'range':self.headers.get('Range'),'cookie':self.headers.get('Cookie')})
                if self.path=='/index.m3u8':body=b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\nclip.ts\n';kind='application/vnd.apple.mpegurl'
                else:body=b'0123456789';kind='application/octet-stream'
                if self.headers.get('Range')=='bytes=2-5':
                    body=body[2:6];self.send_response(206);self.send_header('Content-Range','bytes 2-5/10')
                else:self.send_response(200)
                self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Origin);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            jar=http.cookiejar.CookieJar();jar.set_cookie(http.cookiejar.Cookie(0,'session','test-only-secret',None,False,'127.0.0.1',False,False,'/',True,False,None,True,None,None,{},False))
            with MediaRelay(jar) as relay:
                origin=f'http://127.0.0.1:{server.server_port}'
                link=relay.add(origin+'/clip.ts')
                with urlopen(Request(link,headers={'Range':'bytes=2-5'})) as r:
                    self.assertEqual(r.status,206);self.assertEqual(r.read(),b'2345');self.assertEqual(r.headers['Content-Range'],'bytes 2-5/10')
                with urlopen(relay.add(origin+'/index.m3u8')) as r:playlist=r.read().decode()
                self.assertNotIn(str(server.server_port),playlist);self.assertNotIn('test-only-secret',playlist)
                child=playlist.splitlines()[-1]
                with urlopen(child) as r:self.assertEqual(r.read(),b'0123456789')
                self.assertEqual(seen[-1]['path'],'/clip.ts');self.assertEqual(seen[0]['cookie'],'session=test-only-secret')
        finally:server.shutdown();server.server_close()

    def test_unknown_capability_url_rejected(self):
        from urllib.error import HTTPError
        with MediaRelay() as relay:
            with self.assertRaises(HTTPError) as context:urlopen(f'http://127.0.0.1:{relay.server.server_port}/unknown')
            self.assertEqual(context.exception.code,404)


class AnalysisControlTests(unittest.TestCase):
    def test_browser_fallback_is_scoped_to_manual_douyin_player(self):
        with tempfile.TemporaryDirectory() as directory:
            calls=[]
            media={'url':'https://media.example.test/play.mp4','duration':8,'title':'用户播放的视频','user_agent':'test-player'}
            app=SimpleNamespace(ROOT=Path(directory),DATA=Path(directory),BROWSER=SimpleNamespace(playable_media=lambda url:calls.append(url) or media))
            from yt_dlp import YoutubeDL
            engine=Engine(app);base={'input':'https://www.douyin.com/video/123','login':True}
            with YoutubeDL({'quiet':True,'cachedir':False}) as ydl:
                self.assertIsNone(engine.browser_source({**base,'login':False},ydl))
                self.assertIsNone(engine.browser_source({**base,'cookie_id':'imported'},ydl))
                self.assertIsNone(engine.browser_source({**base,'input':'https://www.bilibili.com/video/123'},ydl))
                self.assertEqual(calls,[])
                info=engine.browser_source(base,ydl)
            self.assertEqual(info['id'],'123');self.assertEqual(info['url'],media['url']);self.assertEqual(info['http_headers']['Referer'],base['input'])

    def test_compatible_asr_segment_offset_terms_and_authenticated_request(self):
        received=[]
        class Service(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_POST(self):
                received.append((self.path,self.headers.get('Authorization'),self.rfile.read(int(self.headers['Content-Length']))))
                body=json.dumps({'segments':[{'start':.2,'end':1.7,'text':'相差'}]}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Service);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                audio=Path(folder)/'speech.wav';audio.write_bytes(b'fixture-only')
                result=transcribe_service({'service':{'base_url':f'http://127.0.0.1:{server.server_port}/v1','api_key':'test-only-key','model':'test-model'},'audio':str(audio),'offset':30,'terms':{'相差':'像差'},'hotwords':'像差'})
                self.assertEqual(result['segments'],[{'from':30.2,'to':31.7,'text':'像差'}]);self.assertEqual(received[0][0],'/v1/audio/transcriptions');self.assertEqual(received[0][1],'Bearer test-only-key');self.assertIn(b'verbose_json',received[0][2])
        finally:server.shutdown();server.server_close()

    def test_takeover_releases_worker_and_requires_explicit_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            job={'kind':'analysis','status':'running','options':{'input':'https://example.test/video'}};opened=[];submitted=[]
            app=SimpleNamespace(ROOT=Path(directory),DATA=Path(directory),BROWSER=SimpleNamespace(open=opened.append),
                change=lambda _,**v:job.update(v),check_cancel=lambda _:None,job_by_id=lambda _:job,
                LOCK=threading.RLock(),WORKERS=SimpleNamespace(submit=lambda *args:submitted.append(args)),guarded=lambda *_:None)
            engine=Engine(app)
            with self.assertRaises(TakeoverRequired):engine.wait_login('job',{'input':'https://example.test/video','login':True})
            self.assertEqual(job['status'],'waiting');self.assertEqual(opened,['https://example.test/video']);self.assertEqual(submitted,[])
            engine.resume('job');self.assertEqual(job['status'],'queued');self.assertEqual(len(submitted),1)

    def test_cookie_import_validates_and_does_not_return_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def atomic(path,value):path.write_text(json.dumps(value),encoding='utf-8')
            app=SimpleNamespace(ROOT=root,DATA=root,atomic_json=atomic)
            engine=Engine(app)
            with self.assertRaises(ValueError):engine.import_cookies('bad.txt','not a cookie file')
            meta=engine.import_cookies('test.txt','# Netscape HTTP Cookie File\n.example.test\tTRUE\t/\tTRUE\t2147483647\tsession\ttest-secret\n')
            self.assertNotIn('test-secret',json.dumps(meta));self.assertEqual(meta['domains'],['.example.test'])
            self.assertTrue(engine.cookie_path(meta['id']).is_file())
            with self.assertRaises(ValueError):engine.cookie_path('../escape')

    def test_offline_model_rejects_modified_archive_before_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'invalid.tar.bz2';source.write_bytes(b'wrong bytes')
            with self.assertRaisesRegex(ValueError,'校验失败'):ai_models.install(root,'sensevoice',lambda **_:None,lambda:None,str(source))
            self.assertFalse((root/'models/sensevoice/verified.json').exists());self.assertEqual(source.read_bytes(),b'wrong bytes')

    def test_imported_offline_model_can_be_exported_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'publisher.tar.bz2'
            with tarfile.open(source,'w:bz2') as archive:
                for name,data in [('model.onnx',b'model fixture'),('tokens.txt',b'tokens fixture')]:
                    item=tarfile.TarInfo('publisher/'+name);item.size=len(data);archive.addfile(item,io.BytesIO(data))
            expected=source.read_bytes();entry={'model':'model.onnx','tokens':'tokens.txt','sha256':ai_models.digest(source),'bytes':len(expected),'url':'https://invalid.test/model'}
            cache=root/'models/sample.offline.tar.bz2';cache.parent.mkdir();cache.write_bytes(b'previous interrupted download')
            with patch.object(ai_models,'catalog',return_value={'sample':entry}),patch.object(ai_models,'urlopen',side_effect=AssertionError('offline export must not connect')) as network:
                ai_models.install(root,'sample',lambda **_:None,lambda:None,offline=str(source))
                ai_models.offline_package(root,'sample',root/'export.tar.bz2')
                network.assert_not_called()
            self.assertEqual((root/'export.tar.bz2').read_bytes(),expected);self.assertEqual(source.read_bytes(),expected)

    def test_interrupted_model_export_does_not_poison_retry(self):
        class Interrupted(io.BytesIO):
            def read(self,*args):
                if self.tell():raise TimeoutError('test connection interrupted')
                return super().read(3)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cache=root/'models/sample.offline.tar.bz2';cache.parent.mkdir();cache.write_bytes(b'old invalid cache')
            expected=b'official publisher archive';entry={'sha256':hashlib.sha256(expected).hexdigest(),'bytes':len(expected),'url':'https://invalid.test/model'}
            with patch.object(ai_models,'catalog',return_value={'sample':entry}):
                with patch.object(ai_models,'urlopen',return_value=Interrupted(expected)):
                    with self.assertRaises(TimeoutError):ai_models.offline_package(root,'sample',root/'export.tar.bz2')
                self.assertEqual(cache.read_bytes(),b'old invalid cache');self.assertFalse(list(cache.parent.glob('.model-download-*')))
                with patch.object(ai_models,'urlopen',return_value=io.BytesIO(expected)):
                    ai_models.offline_package(root,'sample',root/'export.tar.bz2')
                self.assertEqual(cache.read_bytes(),expected);self.assertEqual((root/'export.tar.bz2').read_bytes(),expected)
