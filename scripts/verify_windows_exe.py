"""Exercise the actual EXE with a clean app home and no developer tools on PATH."""
from __future__ import annotations
import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', type=Path, default=ROOT / 'release/VideoDesk-2.5.1-Windows-x64.exe')
    parser.add_argument('--expected-version', default='2.5.1')
    parser.add_argument('--duplicate-proxies', action='store_true', help='Use synthetic case-duplicate native environment keys')
    parser.add_argument('--report-output', type=Path, default=ROOT/'.local/build-exe/exe-verification.json')
    parser.add_argument('--youtube', action='store_true', help='Also test a public YouTube clip; needs platform access')
    args = parser.parse_args()
    folder = Path(tempfile.mkdtemp(prefix='VideoDesk EXE 验证-'))
    report = folder / 'first-run.json'
    stop = folder / 'stop.signal'
    env = dict(os.environ)
    env['PATH'] = str(Path(os.environ.get('SystemRoot','C:/Windows')) / 'System32')
    env['PYTHONHOME'] = str(folder / 'nonexistent-python')
    env['PYTHONPATH'] = str(folder / 'nonexistent-site-packages')
    if args.duplicate_proxies:
        env={k:v for k,v in env.items() if k.upper() not in ('HTTP_PROXY','HTTPS_PROXY')}
        # Empty values keep local HTTP usable while preserving duplicate names.
        env.update(HTTP_PROXY='',http_proxy='',HTTPS_PROXY='',https_proxy='')
    proc = subprocess.Popen([str(args.exe), '--self-test', '--home', str(folder / 'app home'),
                             '--report', str(report), '--stop-file', str(stop)], env=env,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    media_server = None
    try:
        for _ in range(1200):
            if report.is_file():
                state = json.loads(report.read_text(encoding='utf-8'))
                break
            if proc.poll() is not None:
                raise RuntimeError('EXE exited before producing a report, code=' + str(proc.returncode))
            time.sleep(.25)
        else:
            raise RuntimeError('Initial setup timed out')
        assert state['success'],state
        base = state['url']
        with urlopen(base+'/api/state',timeout=10) as response:
            health=json.load(response)
        assert health['version']==args.expected_version
        assert health['engines']=={'yt_dlp':True,'mediacrawler':True},health['engines']
        assert health['youtube']['ready'],health['youtube']
        assert health['jobs']==[], 'New install must not inherit any sender jobs'
        assert state['main_python'].startswith(str(folder))
        assert state['crawler_python'].startswith(str(folder))
        assert state['node'].startswith(str(folder))
        assert '3.13.17' in state['main_check']
        cli=subprocess.run([str(args.exe),'--home',str(folder/'app home'),'--cli','help'],env=env,
            capture_output=True,timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        assert cli.returncode==0,cli.stderr.decode('utf-8',errors='replace')
        token=health['token']
        def api(path,body):
            request=Request(base+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-App-Token':token})
            with urlopen(request,timeout=20) as response:return json.load(response)

        runtime_root=Path(state['install'])
        ffmpeg=next((runtime_root/'runtime/main/Lib/site-packages/imageio_ffmpeg/binaries').glob('ffmpeg*.exe'))
        fixture=folder/'sample.mp4'
        subprocess.run([str(ffmpeg),'-v','error','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=10',
            '-f','lavfi','-i','sine=frequency=440:sample_rate=16000','-t','2','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(fixture)],check=True,
            env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        media_server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(SimpleHTTPRequestHandler,directory=str(folder)))
        threading.Thread(target=media_server.serve_forever,daemon=True).start()
        jid=api('/api/download',{'urls':[f'http://127.0.0.1:{media_server.server_port}/sample.mp4'],
            'output':str(folder/'素材输出'),'login':False,'extract':True,'frames':{'mode':'seconds','value':.5,'audio':True}})['ids'][0]
        for _ in range(300):
            with urlopen(base+'/api/state',timeout=10) as response:jobs=json.load(response)['jobs']
            job=next(j for j in jobs if j['id']==jid)
            if job['status'] in ('done','error','cancelled'):break
            time.sleep(.1)
        assert job['status']=='done',job
        assert job['frames']==4,job
        saved=Path(job['directory'])
        assert (saved/'audio.m4a').stat().st_size>100
        assert (saved/'video.mp4').stat().st_size>1000
        timeline=json.loads((saved/'timeline.json').read_text(encoding='utf-8'))
        assert [f['seconds'] for f in timeline]==[0,.5,1,1.5]
        stream_id=api('/api/download',{'urls':[f'http://127.0.0.1:{media_server.server_port}/sample.mp4'],
            'output':str(folder/'素材输出'),'login':False,'frames_only':True,
            'frames':{'mode':'seconds','value':.5,'start':.5,'duration':1,'audio':True}})['ids'][0]
        def wait_job(task_id):
            for _ in range(600):
                with urlopen(base+'/api/state',timeout=10) as response:current=json.load(response)['jobs']
                task=next(j for j in current if j['id']==task_id)
                if task['status'] in ('done','error','cancelled'):
                    assert task['status']=='done',task
                    return task
                time.sleep(.1)
            raise RuntimeError('EXE media processing timed out')
        streamed=wait_job(stream_id)
        assert streamed['frames']==2,streamed
        assert not any(p.suffix in ('.mp4','.webm','.ts','.part') for p in Path(streamed['directory']).rglob('*'))
        local_id=api('/api/extract-local',{'path':str(fixture),'output':str(folder/'素材输出'),
            'frames':{'mode':'fps','value':2,'audio':False}})['id']
        local=wait_job(local_id)
        assert local['frames']==4,local
        assert not any(p.suffix in ('.mp4','.webm','.ts','.part') for p in Path(local['directory']).rglob('*'))
        if args.youtube:
            youtube_id=api('/api/download',{'urls':['https://www.youtube.com/watch?v=aqz-KE-bpKQ'],
                'output':str(folder/'YouTube 素材'),'login':False,'frames_only':True,'max_height':720,
                'frames':{'mode':'seconds','value':1,'duration':2,'limit':10,'audio':True}})['ids'][0]
            youtube=wait_job(youtube_id)
            youtube_folder=Path(youtube['directory'])
            assert youtube['frames']==2,youtube
            assert (youtube_folder/'audio.m4a').stat().st_size>100
            assert not any(p.suffix in ('.mp4','.webm','.ts','.part') for p in youtube_folder.rglob('*'))
        photos=folder/'照片去重测试'
        photos.mkdir()
        image=next((saved/'frames').glob('*.jpg'))
        shutil.copy2(image,photos/'first.jpg');shutil.copy2(image,photos/'second.jpg')
        dedup_id=api('/api/dedup',{'source':str(photos),'output':str(folder/'素材输出'),'mode':'similar'})['id']
        def wait_dedup():
            for _ in range(600):
                with urlopen(base+'/api/state',timeout=10) as response: current=json.load(response)['jobs']
                task=next(j for j in current if j['id']==dedup_id)
                if task['status'] in ('done','error','cancelled'):
                    assert task['status']=='done',task
                    return task
                time.sleep(.1)
            raise RuntimeError('Image deduplication timed out')
        wait_dedup()
        with urlopen(base+'/api/dedup/'+dedup_id,timeout=10) as response: dedup_report=json.load(response)
        assert dedup_report['engine']=='ImageHash 4.3.2'
        assert dedup_report['summary']['duplicates']==1,dedup_report['summary']
        group=dedup_report['groups'][0]
        for action in ('export','quarantine','restore'):
            api(f'/api/dedup/{dedup_id}/action',{'action':action,'choices':{group['id']:group['keep']}})
            wait_dedup()
            assert len(list(photos.glob('*.jpg')))==(1 if action=='quarantine' else 2),action
        first_elapsed = next((line for line in (folder/'app home/launcher.log').read_text(encoding='utf-8').splitlines() if '释放' in line),'')
        stop.write_text('stop',encoding='ascii')
        assert proc.wait(timeout=20)==0
        second=folder/'second-run.json'
        start=time.monotonic()
        reused=subprocess.run([str(args.exe),'--self-test','--home',str(folder/'app home'),'--report',str(second)],
                              env=env,timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        assert reused.returncode==0
        second_state=json.loads(second.read_text(encoding='utf-8'))
        assert second_state['success']
        assert second_state['install']==state['install'],'Re-run should reuse the prepared runtime'
        summary={'success':True,'exe':str(args.exe),'test_home':str(folder),
                 'version':health['version'],'duplicate_proxy_environment':args.duplicate_proxies,'cli_help':True,
                 'first_install':state['install'],'isolated_path':env['PATH'],
                 'engines':health['engines'],'download_and_extraction':'passed',
                 'frames':job['frames'],'audio':'passed','timeline':'passed',
                 'imagehash_scan_export_quarantine_restore':'passed',
                 'remote_direct_frames_without_video':'passed','local_direct_frames_without_copy':'passed',
                 'youtube_node_ejs':health['youtube'],
                 'public_youtube_direct_frames_and_audio':'passed' if args.youtube else 'not_run',
                 'second_launch_reused_runtime':True,'second_launch_seconds':round(time.monotonic()-start,2)}
        output=args.report_output;output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(summary,ensure_ascii=False),flush=True)
    finally:
        if proc.poll() is None:
            stop.write_text('stop',encoding='ascii')
            try:proc.wait(timeout=20)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
        if media_server:media_server.shutdown();media_server.server_close()


if __name__=='__main__':main()
