"""End-to-end local file tests. No social-platform account or network is used."""
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import imageio_ffmpeg
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='video-desk-tests-')
        cls.folder = Path(cls.tmp.name)
        cls.output = cls.folder / '素材输出'
        cls.fixture = cls.folder / 'sample.mp4'
        subprocess.run([FFMPEG, '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=10',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000',
            '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(cls.fixture)], check=True)
        subprocess.run([FFMPEG,'-v','error','-y','-i',str(cls.fixture),'-c','copy','-hls_time','0.5',
            '-hls_playlist_type','vod',str(cls.folder/'sample.m3u8')],check=True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        cls.base=f'http://127.0.0.1:{port}'
        cls.log=(cls.folder/'server.log').open('wb')
        cls.proc=subprocess.Popen([sys.executable,str(ROOT/'app.py')],cwd=ROOT,
            env={**os.environ,'VIDEO_DESK_PORT':str(port),'VIDEO_DESK_DATA':str(cls.folder/'data')},
            stdout=cls.log,stderr=cls.log)
        for _ in range(100):
            try:
                with urlopen(cls.base+'/api/state',timeout=1) as response: state=json.load(response)
                cls.token=state['token'];break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('Test server did not start')
        cls.media_server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(SimpleHTTPRequestHandler,directory=str(cls.folder)))
        cls.media_thread=threading.Thread(target=cls.media_server.serve_forever,daemon=True)
        cls.media_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate();cls.proc.wait(timeout=15);cls.log.close()
        cls.media_server.shutdown();cls.media_server.server_close()
        cls.tmp.cleanup()

    def api(self,path,body=None,raw=None,headers=None):
        data=json.dumps(body).encode() if body is not None else raw
        hdr={'X-App-Token':self.token}
        if body is not None: hdr['Content-Type']='application/json'
        hdr.update(headers or {})
        with urlopen(Request(self.base+path,data=data,headers=hdr),timeout=30) as response:
            return json.load(response)

    def wait_job(self,jid):
        for _ in range(150):
            job=next(j for j in self.api('/api/state')['jobs'] if j['id']==jid)
            if job['status'] in ('done','error','cancelled'): return job
            time.sleep(.1)
        self.fail('Job timed out')

    def upload(self,options):
        boundary='video-desk-test-boundary'
        chunks=[]
        for name,value in [('options',json.dumps(options)),('output',str(self.output))]:
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample.mp4"\r\nContent-Type: video/mp4\r\n\r\n'.encode())
        chunks.append(self.fixture.read_bytes());chunks.append(f'\r\n--{boundary}--\r\n'.encode())
        jid=self.api('/api/extract',raw=b''.join(chunks),headers={'Content-Type':f'multipart/form-data; boundary={boundary}'})['id']
        job=self.wait_job(jid);self.assertEqual(job['status'],'done',job['message']);return job

    def test_seconds_crop_timestamps_and_audio(self):
        job=self.upload({'mode':'seconds','value':.5,'crop':[.25,.25,.5,.5],'audio':True})
        folder=Path(job['directory'])
        self.assertEqual(len(list((folder/'frames').glob('*.jpg'))),4)
        with Image.open(folder/'frames/000001.jpg') as image:
            self.assertEqual(image.size,(160,90))
        timeline=json.loads((folder/'timeline.json').read_text(encoding='utf-8'))
        self.assertEqual([f['seconds'] for f in timeline],[0,.5,1,1.5])
        self.assertGreater((folder/'audio.m4a').stat().st_size,100)
        self.assertTrue((folder/'AI分析提示词.txt').is_file())

    def test_dedup_scan_thumbnail_export_quarantine_restore_api(self):
        import shutil
        source=self.folder/'照片去重 API'
        source.mkdir()
        Image.new('RGB',(160,90),'#18746c').save(source/'first.png')
        shutil.copy2(source/'first.png',source/'second.png')
        jid=self.api('/api/dedup',{'source':str(source),'output':str(self.output)})['id']
        job=self.wait_job(jid);self.assertEqual(job['status'],'done',job['message'])
        report=self.api('/api/dedup/'+jid)
        self.assertEqual(report['summary']['duplicates'],1)
        group=report['groups'][0]
        with urlopen(self.base+f"/api/dedup/{jid}/thumbnail/{group['keep']}") as response:
            self.assertEqual(response.headers['Content-Type'],'image/jpeg')
            self.assertGreater(len(response.read()),100)
        for action in ('export','quarantine','restore'):
            self.api(f'/api/dedup/{jid}/action',{'action':action,'choices':{group['id']:group['keep']}})
            job=self.wait_job(jid);self.assertEqual(job['status'],'done',job['message'])
            self.assertEqual(len(list(source.glob('*.png'))),1 if action=='quarantine' else 2)
        report=self.api('/api/dedup/'+jid)
        self.assertEqual(len(list(Path(report['operations'][0]['directory']).glob('*.png'))),1)
        self.assertEqual(report['operations'][1]['state'],'restored')

    def test_every_nth_frame_and_original_trim_times(self):
        job=self.upload({'mode':'frames','value':5,'start':.5,'duration':1,'audio':False})
        timeline=json.loads((Path(job['directory'])/'timeline.json').read_text(encoding='utf-8'))
        self.assertEqual([f['seconds'] for f in timeline],[.5,1])

    def test_all_frames_enforces_limit_and_warns(self):
        job=self.upload({'mode':'all','limit':3,'audio':False})
        folder=Path(job['directory']);self.assertEqual(job['frames'],3)
        self.assertTrue(json.loads((folder/'manifest.json').read_text(encoding='utf-8'))['warnings'])

    def test_streaming_chunks_finalize_to_valid_mp4(self):
        webm=self.folder/'sample.webm'
        subprocess.run([FFMPEG,'-hide_banner','-loglevel','error','-y','-i',str(self.fixture),
            '-c:v','libvpx-vp9','-c:a','libopus',str(webm)],check=True)
        jid=self.api('/api/capture',{'title':'Synthetic capture','output':str(self.output)})['id']
        data=webm.read_bytes();parts=[data[:len(data)//2],data[len(data)//2:]]
        for i,part in enumerate(parts):self.api(f'/api/capture/{jid}/chunk?index={i}',raw=part)
        shot=io.BytesIO();Image.new('RGB',(100,60),'green').save(shot,format='JPEG')
        self.api(f'/api/capture/{jid}/shot?index=0&seconds=0.5',raw=shot.getvalue())
        self.api(f'/api/capture/{jid}/finish',{'chunks':2,'mp4':True,'audio':True})
        job=self.wait_job(jid);self.assertEqual(job['status'],'done',job['message'])
        folder=Path(job['directory']);self.assertGreater((folder/'recording.mp4').stat().st_size,1000)
        result=subprocess.run([FFMPEG,'-v','error','-i',str(folder/'recording.mp4'),'-f','null','-'],capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(job['frames'],1)
        with self.assertRaises(HTTPError) as caught:
            self.api(f'/api/capture/{jid}/chunk?index=2',raw=b'late')
        self.assertEqual(caught.exception.code,409)
        caught.exception.close()

    def test_missing_chunk_prevents_false_success(self):
        jid=self.api('/api/capture',{'output':str(self.output)})['id']
        with self.assertRaises(HTTPError) as caught:
            self.api(f'/api/capture/{jid}/finish',{'chunks':1})
        self.assertEqual(caught.exception.code,409)
        caught.exception.close()
        self.api(f'/api/capture/{jid}/finish',{'chunks':0})
        self.wait_job(jid)

    def test_yt_dlp_real_download_and_automatic_extraction(self):
        url=f'http://127.0.0.1:{self.media_server.server_port}/sample.mp4'
        jid=self.api('/api/download',{'urls':[url],'output':str(self.output),'login':False,
            'extract':True,'frames':{'mode':'fps','value':2,'audio':True}})['ids'][0]
        job=self.wait_job(jid);self.assertEqual(job['status'],'done',job['message'])
        self.assertEqual(job['frames'],4)
        self.assertTrue(any(f['name']=='video.mp4' for f in job['files']))

    def test_remote_direct_frames_no_saved_video_with_crop_time_and_audio(self):
        url=f'http://127.0.0.1:{self.media_server.server_port}/sample.mp4'
        jid=self.api('/api/download',{'urls':[url],'output':str(self.output),'login':False,
            'frames_only':True,'frames':{'mode':'seconds','value':.5,'start':.5,'duration':1,
            'crop':[.25,.25,.5,.5],'audio':True}})['ids'][0]
        job=self.wait_job(jid);self.assertEqual(job['status'],'done',job['message'])
        folder=Path(job['directory'])
        self.assertEqual(job['frames'],2)
        self.assertFalse(any(p.suffix in ('.mp4','.webm','.part','.ts','.mkv') for p in folder.rglob('*')))
        with Image.open(folder/'frames/000001.jpg') as image:self.assertEqual(image.size,(160,90))
        timeline=json.loads((folder/'timeline.json').read_text(encoding='utf-8'))
        self.assertEqual([f['seconds'] for f in timeline],[.5,1])
        self.assertGreater((folder/'audio.m4a').stat().st_size,100)
        self.assertFalse(json.loads((folder/'manifest.json').read_text(encoding='utf-8'))['video_saved'])
        self.assertNotIn('http://',(folder/'.frames.log').read_text(encoding='utf-8'))

    def test_local_direct_frames_preview_and_original_unchanged(self):
        import hashlib
        fingerprint=hashlib.sha256(self.fixture.read_bytes()).hexdigest()
        preview=self.api('/api/local-video/preview',{'path':str(self.fixture)})
        with urlopen(Request(self.base+preview['url'],headers={'Range':'bytes=0-99'})) as response:
            self.assertEqual(response.status,206);self.assertEqual(len(response.read()),100)
        jid=self.api('/api/extract-local',{'path':str(self.fixture),'output':str(self.output),
            'frames':{'mode':'fps','value':2,'audio':False}})['id']
        job=self.wait_job(jid);self.assertEqual(job['status'],'done',job['message'])
        self.assertEqual(job['frames'],4)
        self.assertFalse(any(f['name'].endswith(('.mp4','.webm','.mkv','.m4a')) for f in job['files']))
        self.assertEqual(hashlib.sha256(self.fixture.read_bytes()).hexdigest(),fingerprint)

    def test_hls_direct_frames_does_not_write_playlist_or_segments(self):
        url=f'http://127.0.0.1:{self.media_server.server_port}/sample.m3u8'
        jid=self.api('/api/download',{'urls':[url],'output':str(self.output),'login':False,
            'frames_only':True,'frames':{'mode':'fps','value':2,'audio':False}})['ids'][0]
        job=self.wait_job(jid);self.assertEqual(job['status'],'done',job['message'])
        self.assertEqual(job['frames'],4)
        self.assertFalse(any(p.suffix in ('.mp4','.ts','.m3u8','.webm','.part') for p in Path(job['directory']).rglob('*')))

    def test_csrf_and_file_boundaries(self):
        with self.assertRaises(HTTPError) as caught:
            with urlopen(Request(self.base+'/api/settings',data=b'{}',headers={'Content-Type':'application/json'})):pass
        self.assertEqual(caught.exception.code,403)
        caught.exception.close()
        with self.assertRaises(HTTPError) as caught:
            self.api('/api/settings',{'output':str(self.output)},headers={'Origin':'https://example.org'})
        self.assertEqual(caught.exception.code,403)
        caught.exception.close()
        jid=self.api('/api/capture',{'output':str(self.output)})['id']
        with self.assertRaises(HTTPError) as caught:
            with urlopen(self.base+f'/api/jobs/{jid}/files/%2e%2e/%2e%2e/app.py'):pass
        self.assertEqual(caught.exception.code,404)
        caught.exception.close()
        self.api(f'/api/capture/{jid}/finish',{'chunks':0});self.wait_job(jid)


if __name__=='__main__':unittest.main(verbosity=2)
