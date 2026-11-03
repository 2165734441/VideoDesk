"""2.1 contract tests: paths, segment clocks, source transport and permissions."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from urllib.request import Request,urlopen
from fastapi import FastAPI
import socket
import time
import uvicorn
from urllib.error import HTTPError
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed,InvalidStatus
from integrations.video_sources import normalize_path,parse_paths,local_video
from integrations.local_sources import install,discover,CHUNK,SourceUnavailable
from integrations.batch_pipeline import boundaries,segment_transcript,write_transcript,BatchRequest,Outputs


class Contracts(unittest.TestCase):
    def test_atomic_metadata_retries_transient_windows_sharing_lock(self):
        from unittest.mock import patch
        from integrations.storage import atomic_json
        replace=os.replace;calls=[]
        def locked_once(source,destination):
            calls.append(1)
            if len(calls)<3:raise PermissionError('test sharing lock')
            replace(source,destination)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'jobs.json';path.write_text('old',encoding='utf-8')
            with patch('integrations.storage.os.replace',locked_once):atomic_json(path,{'saved':True})
            self.assertEqual(json.loads(path.read_text('utf-8')),{'saved':True})
            self.assertEqual(len(calls),3);self.assertEqual(list(Path(tmp).glob('*.tmp')),[])

    def test_failed_atomic_write_preserves_previous_records(self):
        from unittest.mock import patch
        from integrations.storage import atomic_json
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'jobs.json';path.write_text('old',encoding='utf-8')
            with patch('integrations.storage.os.replace',side_effect=PermissionError('test')),patch('integrations.storage.time.sleep'):
                with self.assertRaises(PermissionError):atomic_json(path,{'saved':False})
            self.assertEqual(path.read_text('utf-8'),'old');self.assertEqual(list(Path(tmp).glob('*.tmp')),[])

    def test_paths_preserve_spaces_commas_and_ignore_quotes(self):
        self.assertEqual(parse_paths(' \n "D:\\商品 视频\\a,b.mp4" \r\n“D:\\第二段.mp4”\n'),['D:\\商品 视频\\a,b.mp4','D:\\第二段.mp4'])
        self.assertEqual(normalize_path(' ""D:/a.mp4"" '),'D:/a.mp4')

    def test_segment_remainder_and_fractional_boundaries(self):
        self.assertEqual(boundaries(1.1,6.4,2),[(1.1,3.1),(3.1,5.1),(5.1,6.4)])
        self.assertEqual(boundaries(0,20,10),[(0,10),(10,20)])
        self.assertEqual(boundaries(0,3,0),[(0,3)])

    def test_subtitle_overlap_keeps_both_clocks(self):
        original=[{'from':1.5,'to':3.5,'text':'跨段台词'}]
        left=segment_transcript(original,0,2);right=segment_transcript(original,2,4)
        self.assertEqual(left[0]['to'],2);self.assertEqual(right[0]['from'],2)
        self.assertEqual(right[0]['original_from'],1.5)
        self.assertEqual(original[0]['to'],3.5)
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'subtitles';write_transcript(folder,right,2)
            self.assertIn('00:00:02,000',(folder/'原视频时间.srt').read_text('utf-8'))
            self.assertIn('00:00:00,000',(folder/'片段内时间.srt').read_text('utf-8'))

    def test_explicit_outputs_are_independent_and_default_is_ai(self):
        self.assertFalse(Outputs(audio=True).frames)
        self.assertFalse(Outputs(audio=True).transcript)
        self.assertFalse(Outputs(audio=True).puzzles)
        self.assertTrue(BatchRequest(sources=['id'],request_id='12345678').outputs.ai)
        with self.assertRaises(ValueError):BatchRequest(sources=['id'],request_id='12345678',dedup={'threshold':17})
        with self.assertRaises(ValueError):BatchRequest(sources=['id'],request_id='12345678',segments={'seconds':float('nan')})

    def test_folder_recursion_and_bad_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'子目录').mkdir();(root/'中文 空格,a.mp4').write_bytes(b'v');(root/'子目录/另一个.mkv').write_bytes(b'v');(root/'ignore.txt').write_bytes(b'text')
            self.assertEqual(len(discover(root,False)[0]),1)
            self.assertEqual(len(discover(root,True)[0]),2)
            try:
                os.symlink(root,root/'子目录/loop',target_is_directory=True)
                paths,skipped=discover(root,True);self.assertEqual(len(paths),2);self.assertTrue(skipped)
            except OSError:pass
            with self.assertRaisesRegex(ValueError,'格式不支持'):local_video(root/'ignore.txt')
            (root/'empty.mp4').touch()
            with self.assertRaisesRegex(ValueError,'为空'):local_video(root/'empty.mp4')
            with self.assertRaisesRegex(ValueError,'不存在'):local_video(root/'missing.mp4')


class BrowserTransport(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();data=Path(self.tmp.name)
        def write(path,value):path.write_text(json.dumps(value),encoding='utf-8')
        self.a=SimpleNamespace(DATA=data,TOKEN='session-secret',load_json=lambda p,f:f,atomic_json=write,JOBS={},PROCESSES={})
        self.app=FastAPI();self.sources=install(self.app,self.a)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        self.base=f'http://127.0.0.1:{port}'
        self.server=uvicorn.Server(uvicorn.Config(self.app,host='127.0.0.1',port=port,log_level='error',ws_max_size=2*CHUNK))
        self.thread=threading.Thread(target=self.server.run,daemon=True);self.thread.start()
        deadline=time.monotonic()+5
        while not self.server.started:
            if time.monotonic()>deadline:raise RuntimeError('test server did not start')
            time.sleep(.01)
        def post(path,json):
            import json as codec
            try:
                with urlopen(Request(self.base+path,data=codec.dumps(json).encode(),headers={'Content-Type':'application/json'})) as response:
                    data=codec.load(response);return SimpleNamespace(status_code=response.status,json=lambda:data)
            except HTTPError as error:
                data=codec.load(error);return SimpleNamespace(status_code=error.code,json=lambda:data)
        self.client=SimpleNamespace(post=post)
        self.meta={'name':'large.mp4','size':8*1024**3,'modified':123,'fingerprint':'a'*64}
        self.source=self.client.post('/api/sources/browser',json=self.meta).json();self.id=self.source['id']
    def tearDown(self):
        self.server.should_exit=True;self.thread.join(timeout=5);self.sources.server.shutdown();self.sources.server.server_close();self.sources.pool.shutdown(wait=True);self.tmp.cleanup()
    @contextmanager
    def connection(self,origin=None):
        with connect(self.base.replace('http:','ws:')+f'/api/sources/{self.id}/read',origin=origin or self.base,proxy=None) as ws:
            yield SimpleNamespace(send_json=lambda v:ws.send(json.dumps(v)),receive_json=lambda:json.loads(ws.recv()),send_bytes=ws.send)
    def test_same_origin_and_token_required(self):
        with self.assertRaises((ConnectionClosed,InvalidStatus)):
            with self.connection('http://evil.example') as ws:ws.send_json({'token':'session-secret'})
        with self.connection() as ws:
            ws.send_json({'token':'wrong'})
            with self.assertRaises((ConnectionClosed,InvalidStatus)):ws.receive_json()
    def test_large_file_range_bounded_buffer_and_disconnect(self):
        with self.connection() as ws:
            ws.send_json({'token':'session-secret'});self.assertTrue(ws.receive_json()['ready'])
            address=self.sources.address(self.id)
            def fetch():
                with urlopen(Request(address,headers={'Range':f'bytes={CHUNK*100}-{CHUNK*102-1}'})) as response:
                    return response.status,response.headers['Content-Range'],response.read()
            with ThreadPoolExecutor(1) as pool:
                future=pool.submit(fetch)
                for i in range(2):
                    request=ws.receive_json();self.assertEqual(request['length'],CHUNK)
                    self.assertEqual(request['offset'],CHUNK*(100+i))
                    ws.send_bytes(request['read'].encode()+b'x'*CHUNK)
                status,header,body=future.result(timeout=5)
            self.assertEqual(status,206);self.assertEqual(len(body),CHUNK*2)
            self.assertIn('/8589934592',header)
            self.assertLessEqual(self.sources.readers[self.id].peak_buffer,8*CHUNK)
            self.assertFalse(any(p.suffix=='.mp4' for p in Path(self.tmp.name).rglob('*')))
        self.assertFalse(self.sources.connected(self.id))
        with self.assertRaises(SourceUnavailable):self.sources.address(self.id)
    def test_reattach_requires_same_file_metadata_and_hash(self):
        bad=self.client.post('/api/sources/browser',json={**self.meta,'source_id':self.id,'fingerprint':'b'*64})
        self.assertEqual(bad.status_code,409)
        good=self.client.post('/api/sources/browser',json={**self.meta,'source_id':self.id})
        self.assertEqual(good.status_code,200);self.assertEqual(good.json()['id'],self.id)
    def test_unknown_source_capability_rejected(self):
        self.assertEqual(self.client.post('/api/sources/not-a-source/preview',json={}).status_code,404)

if __name__=='__main__':unittest.main()
