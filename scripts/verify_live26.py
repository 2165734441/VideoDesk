"""Four real FFmpeg captures against a controlled HTTP stream, not a Douyin claim."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import argparse
    from concurrent.futures import ThreadPoolExecutor
    parser=argparse.ArgumentParser();parser.add_argument('--with-translation',action='store_true');args=parser.parse_args()
    data = ROOT/'.local/v26-qa/live-check'; data.mkdir(parents=True, exist_ok=True)
    os.environ['VIDEO_DESK_DATA'] = str(data)
    import app as a
    sample = data/'stream.ts'
    subprocess.run([a.FFMPEG, '-hide_banner', '-loglevel', 'error', '-y', '-stream_loop', '-1', '-i',
        str(ROOT/'.local/v26-qa/samples/english.mp4'), '-t', '120', '-c', 'copy', '-f', 'mpegts', str(sample)], check=True)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            self.send_response(200);self.send_header('Content-Type','video/mp2t');self.send_header('Content-Length',str(sample.stat().st_size));self.end_headers()
            try:
                with sample.open('rb') as source:
                    while block := source.read(32*1024):
                        self.wfile.write(block);self.wfile.flush();time.sleep(.1)
            except (ConnectionError, OSError): pass
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    manager = a.LIVE; manager.settings['output'] = str(data/'output')
    def resolve(browser,url,quality,cancelled=lambda:False):
        return {'url':f'http://127.0.0.1:{server.server_port}/stream.ts','online':True,'room_id':url.rsplit('/',1)[-1]}
    result = {'fixture':'Controlled local HTTP MPEG-TS; resolver injected, not real Douyin validation'}
    try:
        with patch('integrations.live.live_browser.resolve',side_effect=resolve):
            keys=[];ids=[]
            for i in range(4):
                url='https://live.douyin.com/'+str(800000+i)
                existing=next((v for v in manager.anchors.values() if v['url']==url),{})
                item=manager.put(a.live.Anchor(id=existing.get('id',''),url=url,name='test-'+str(i)))
                keys.append(item['id']); ids.append(manager.start(item['id'])['id'])
            deadline=time.monotonic()+20
            while not all(a.JOBS[j]['status']=='recording' for j in ids) and time.monotonic()<deadline:time.sleep(.1)
            assert all(a.JOBS[j]['status']=='recording' for j in ids)
            result['four_recording']=True
            translation_job=None;translation_pool=None;translation_future=None
            if args.with_translation:
                a.atomic_json(data/'translation-location.json', {'path':str(ROOT/'.local/v26-qa')})
                source=ROOT/'.local/v26-qa/samples/english.mp4'
                request=a.translation.TranslateRequest(paths=[str(source)],output=str(data/'output'),review=False)
                translation_job=a.new_job('translation','Concurrent translation',request.output,str(source),request.model_dump(exclude={'paths','output','source_job','relative'}))
                translation_pool=ThreadPoolExecutor(max_workers=1)
                translation_future=translation_pool.submit(a.TRANSLATION.run,translation_job['id'])
            url='https://live.douyin.com/800005'
            existing=next((v for v in manager.anchors.values() if v['url']==url),{})
            fifth=manager.put(a.live.Anchor(id=existing.get('id',''),url=url,name='fifth'))
            waiting=manager.start(fifth['id'])
            assert waiting.get('queued') and len(manager.active)==4
            manager.stop(fifth['id'])
            assert a.JOBS[waiting['id']]['status']=='cancelled'
            result['concurrency_limit_and_waiting_cancel']=True
            waiting=manager.start(fifth['id'])
            manager.start_service()
            time.sleep(4)
            manager.stop(keys[0])
            deadline=time.monotonic()+20
            while keys[0] in manager.active and time.monotonic()<deadline:time.sleep(.2)
            assert keys[0] not in manager.active
            assert all(a.JOBS[j]['status']=='recording' for j in ids[1:])
            result['stop_isolated']=True
            deadline=time.monotonic()+15
            while a.JOBS[waiting['id']]['status']!='recording' and time.monotonic()<deadline:time.sleep(.2)
            assert a.JOBS[waiting['id']]['status']=='recording' and len(manager.active)<=4
            result['queued_capture_started_when_slot_freed']=True
            time.sleep(3)
            if translation_future:
                translation_future.result(timeout=120);translation_pool.shutdown()
                assert a.JOBS[translation_job['id']]['status']=='done',a.JOBS[translation_job['id']]['message']
                assert all(a.JOBS[j]['status']=='recording' for j in [*ids[1:],waiting['id']])
                result['translation_completed_while_four_captures_active']=True
            for key in [*keys[1:],fifth['id']]:manager.stop(key)
            deadline=time.monotonic()+30
            while manager.active and time.monotonic()<deadline:time.sleep(.2)
            assert not manager.active
            results=[]
            for jid in [*ids,waiting['id']]:
                job=a.JOBS[jid]
                assert job['status']=='done',job
                assert job.get('replay'),job
                segment=job['replay'][0]
                source=Path(job['directory'])/segment['preview']
                info=a.translation.media_probe.inspect(source,str(a.ANALYSIS_ENGINE.probe))
                assert info['duration']>0 and info['hasAudio']
                results.append({'id':jid,'duration':info['duration'],'preview':segment['preview'],'bytes':source.stat().st_size})
            result['replays']=results
            # Metadata corruption should never prevent opening the main app.
            manager.save(); result['configuration_persisted']=manager.path.is_file()
    finally:
        manager.close();server.shutdown();server.server_close()
    a.atomic_json(data/'evidence.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
