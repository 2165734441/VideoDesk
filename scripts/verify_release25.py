"""Check 2.5 workflows, original-copy extras and old CLI/DSH on the built EXE."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import Request, urlopen
import uuid

ROOT=Path(__file__).resolve().parents[1];QA=ROOT/'.local/v25-qa'


def main():
    verified=json.loads((ROOT/'.local/build-exe/exe-verification.json').read_text('utf-8'))
    home=Path(verified['test_home'])/'app home';folder=QA/'packaged-workflows';folder.mkdir(exist_ok=True)
    report=folder/'server.json';report.unlink(missing_ok=True)
    stop=folder/'server.stop';stop.unlink(missing_ok=True)
    exe=ROOT/'release/VideoDesk-2.5.0-Windows-x64.exe'
    env={**os.environ,'PATH':str(Path(os.environ['SystemRoot'])/'System32')}
    proc=subprocess.Popen([str(exe),'--self-test','--home',str(home),'--report',str(report),'--stop-file',str(stop)],
        env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        deadline=time.monotonic()+360
        while not report.exists():
            assert proc.poll() is None,'EXE exited before report'
            assert time.monotonic()<deadline,'EXE startup timed out'
            time.sleep(.2)
        server=json.loads(report.read_text('utf-8'));assert server['success']
        base=server['url'];token=json.load(urlopen(base+'/api/state'))['token']
        def api(path,body=None):
            with urlopen(Request(base+path,data=None if body is None else json.dumps(body).encode(),
                headers={'X-App-Token':token,'Content-Type':'application/json'}),timeout=90) as r:return json.load(r)
        def wait(job):
            deadline=time.monotonic()+600
            while time.monotonic()<deadline:
                result=api('/api/batches/'+job['id'])
                if result['job']['status'] not in ('queued','processing','running'):
                    assert result['job']['status']=='done',result
                    return result['children'][0]
                time.sleep(.4)
            raise AssertionError('Packaged workflow timeout')
        output=folder/'output';api('/api/settings',{'output':str(output)})
        sample=QA/'sample.mp4';source_hash=hashlib.sha256(sample.read_bytes()).hexdigest()
        source=api('/api/sources/register',{'paths':[str(sample)]})['sources'][0]
        for _ in range(300):
            source=next(s for s in api('/api/sources')['sources'] if s['id']==source['id'])
            if source['status']!='validating':break
            time.sleep(.1)
        assert source['status']=='ready',source
        def create(kind,**extra):
            return api('/api/batches',{'sources':[source['id']],'output':str(output),'kind':kind,'workflow':kind,
                'request_id':uuid.uuid4().hex,'performance':'balanced',**extra})
        rules=wait(create('rules',outputs={'frames':True,'ai':True,'transcript':True,'puzzles':True},
            frames={'mode':'seconds','value':3}))
        assert len(rules['files'])==7 and all(f['name'].endswith('.jpg') for f in rules['files'])
        assert not rules['options']['outputs']['ai']
        subtitles=wait(create('subtitles',analysis={'strip_step':3,'strip_count':24}))
        assert all('subtitle-strip.jpg' in f['name'] for f in subtitles['files'])
        board=wait(create('board',outputs={'audio':True,'frames':True,'video':True,'zip':True},
            analysis={'duration':2,'transcript_file':str(QA/'resume-transcript.json'),'max_cells_per_sheet':4},
            ai_package={'enabled':True,'screen_text':True,'scan_seconds':1}))
        dest=Path(board['directory']);upload=dest/'一起上传给AI'
        assert (upload/'00_阅读说明.txt').is_file() and board['ai_package']['files']
        video=next((dest/'附加素材').glob('原视频.*'))
        assert hashlib.sha256(video.read_bytes()).hexdigest()==source_hash
        assert (dest/'audio/audio.m4a').is_file() and list((dest/'frames').glob('*.jpg'))
        assert not any(p.suffix.lower() in ('.mp4','.m4a') for p in upload.iterdir())
        assert (dest/'素材包.zip').is_file()
        with (folder/'cli.log').open('wb') as log:
            subprocess.run([str(exe),'--home',str(home),'--cli','batch',str(sample),'--outputs','audio',
                '--output',str(output/'cli')],env=env,stdout=log,stderr=log,timeout=90,check=True,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        cli=next(j for j in api('/api/state')['jobs'] if j['kind']=='batch')
        assert cli['status']=='done' and cli['files'] and all(f['name'].endswith('.m4a') for f in cli['files'])
        env.update(VIDEO_DESK_URL=base,VIDEO_DESK_TEST_VIDEO=str(sample),VIDEO_DESK_TEST_OUTPUT=str(output/'dsh'))
        with (folder/'dsh.log').open('wb') as log:
            subprocess.run([server['node'],'--test',str(ROOT/'tests/test_dsh_adapter.mjs')],env=env,
                stdout=log,stderr=log,timeout=120,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        assert hashlib.sha256(sample.read_bytes()).hexdigest()==source_hash
        with exe.open('rb') as stream:exe_hash=hashlib.file_digest(stream,'sha256').hexdigest()
        (QA/'packaged25.json').write_text(json.dumps({'passed':True,'exe_sha256':exe_hash,
            'rules_images':len(rules['files']),'rules_ignores_ai_flags':True,'subtitle_only':True,
            'ai_required_files':len(board['ai_package']['files']),'optional_original_copy_sha256':source_hash,
            'optional_frames_audio_outside_upload':True,'actual_exe_cli_audio_only':True,
            'actual_runtime_dsh_tests':2,'original_unchanged':True},indent=2),'utf-8')
        print('PACKAGED WORKFLOWS, CLI AND DSH PASSED',flush=True)
    finally:
        stop.write_text('stop','ascii')
        try:proc.wait(timeout=30)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()


if __name__=='__main__':main()
