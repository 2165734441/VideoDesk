"""Real HTTP/FFmpeg/Node integration checks; writes reproducible review artifacts."""
import argparse
import functools
import hashlib
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.request import Request,urlopen
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from integrations import video_sources
import imageio_ffmpeg
from PIL import Image,ImageDraw,ImageFont


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:18970');parser.add_argument('--youtube',action='store_true');parser.add_argument('--data',type=Path,default=ROOT/'.local/merge-qa/data');args=parser.parse_args()
    base=args.url;qa=ROOT/'.local/merge-qa';qa.mkdir(exist_ok=True);output=qa/'output';output.mkdir(exist_ok=True)
    with urlopen(base+'/api/state') as r:token=json.load(r)['token']
    def api(path,body=None):
        req=Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json','X-App-Token':token})
        with urlopen(req,timeout=45) as r:return json.load(r)
    def wait(job):
        deadline=time.monotonic()+900
        while job['status'] in ('queued','running','waiting'):
            if job['status']=='waiting':raise AssertionError('需要人工登录验证：'+job['id'])
            if time.monotonic()>deadline:raise AssertionError('Timeout '+job['id'])
            time.sleep(.25);job=next(j for j in api('/api/state')['jobs'] if j['id']==job['id'])
        assert job['status']=='done',job
        return job
    results=[]
    def board(path,**opts):
        begin=time.monotonic();job=wait(api('/api/analysis',{'input':str(path),'output':str(output),'transcript':'off','keep_frames':False,**opts}))
        folder=Path(job['directory']);evidence=json.loads((folder/'evidence.json').read_text('utf-8')) if (folder/'evidence.json').exists() else None
        results.append({'case':opts.pop('strategy',opts.get('kind','board')),'job':job['id'],'seconds':round(time.monotonic()-begin,2),'sheets':len(evidence['sheets']) if evidence else 0,'cells':len(evidence['cells']) if evidence else 0,'directory':str(folder),'bytes':sum(p.stat().st_size for p in folder.rglob('*') if p.is_file())})
        print(results[-1],flush=True);return job,evidence
    ff=imageio_ffmpeg.get_ffmpeg_exe()
    def run(args):subprocess.run([ff,'-hide_banner','-loglevel','error','-y',*map(str,args)],check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    # Deliberate cuts + portrait + silent source, with visible bilingual text.
    still=qa/'subtitle-fixture.png';im=Image.new('RGB',(360,640),'#183a40');draw=ImageDraw.Draw(im);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',24)
    draw.text((25,540),'测试字幕：视频分析',font=font,fill='white');draw.text((25,580),'Frame and transcript',font=font,fill='white');im.save(still)
    portrait=qa/'portrait-silent.mp4';run(['-loop','1','-i',still,'-t','8','-r','10','-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',portrait])
    multi=qa/'fastcuts.mp4';run(['-f','lavfi','-i','testsrc2=size=640x360:rate=12','-t','8','-vf',"negate=enable='between(t,2,4)+between(t,6,8)'",'-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',multi])
    original=hashlib.sha256(portrait.read_bytes()).hexdigest()
    srt=qa/'bilingual.srt';srt.write_text('1\n00:00:00,000 --> 00:00:03,000\n测试字幕：视频分析\n\n2\n00:00:03,000 --> 00:00:08,000\nFrame and transcript\n',encoding='utf-8')
    for strategy in ('full','standard','compact','skim','brief'):
        job,e=board(multi,strategy=strategy,max_frames=12,max_sheets=4)
        assert e['cells'] and e['sheets'];assert all(0<=c['from']<c['to']<=8.1 for c in e['cells'])
        for s in e['sheets']:
            with Image.open(Path(job['directory'])/s['file']) as img:assert img.width==s['width'] and img.height==s['height']
    job,e=board(portrait,transcript='auto',transcript_file=str(srt),batch_seconds=3,keep_frames=True,audio=False)
    assert len(e['windows'])==3 and any('测试' in c['text'] for c in e['cells'])
    assert '测试字幕' in (Path(job['directory'])/'part-001/分镜与台词清单.txt').read_text('utf-8')
    assert len({c['cell'] for c in e['cells']})==len(e['cells'])
    assert api(f"/api/analysis/{job['id']}/locate",{'query':'视频分析'})['matches']
    zoom=wait(api(f"/api/analysis/{job['id']}/zoom",{'cell':e['cells'][0]['cell']}));assert (Path(zoom['directory'])/'evidence.json').is_file()
    export=wait(api(f"/api/analysis/{job['id']}/export",{'output':str(output),'frames':False,'audio':False,'video':False}))
    with zipfile.ZipFile(Path(export['directory'])/'AI分析包.zip') as z:
        names=z.namelist();assert 'evidence.json' in names and not any(n.startswith('frames/') or n.startswith('.') or 'cookies' in n.lower() for n in names)
    assert hashlib.sha256(portrait.read_bytes()).hexdigest()==original
    sub,_=board(portrait,kind='subtitles',band=[.8,1],strip_step=2,strip_count=4,ocr=True)
    assert (Path(sub['directory'])/'字幕OCR.json').is_file()
    board(portrait,kind='subtitles',strip_step=3,strip_count=3)
    probe,_=board(portrait,kind='probe');p=json.loads((Path(probe['directory'])/'probe.json').read_text('utf-8'));assert not p['hasAudio'] and p['width']==360
    # Serve a real MP4 over HTTP and exercise both resolver modes.
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(SimpleHTTPRequestHandler,directory=str(qa)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    remote=f'http://127.0.0.1:{server.server_port}/fastcuts.mp4'
    try:
        before={p.name for p in (args.data/'media-cache').iterdir()}
        direct,e=board(remote,mode='direct',login=False,duration=3,keep_frames=True)
        assert not e['video_saved']
        assert not any(p.suffix.lower() in video_sources.VIDEO_EXTENSIONS or p.suffix in ('.part','.m3u8') for p in Path(direct['directory']).rglob('*'))
        assert before=={p.name for p in (args.data/'media-cache').iterdir()}
        serialized=json.dumps(e);assert '/media/' not in serialized and '127.0.0.1:' not in serialized.replace(remote,'')
        wait(api(f"/api/analysis/{direct['id']}/zoom",{'start':1,'duration':1}))
        cached,_=board(remote,mode='cache',login=False,duration=3)
        record=args.data/'media-cache'/cached['options']['cache_key']/'cache-record.json';assert record.is_file()
        unchanged=record.stat().st_mtime_ns;board(remote,mode='cache',login=False,duration=3);assert record.stat().st_mtime_ns==unchanged
    finally:server.shutdown();server.server_close()
    # The same committed official pack is imported via the user-facing API.
    pack=ROOT/'release/SenseVoice-offline.tar.bz2'
    if pack.is_file():
        wait(api('/api/models/install',{'id':'sensevoice','path':str(pack)}))
        for language,expected in [('zh','开放'),('en','')]:
            speech=qa/f'speech-{language}.mp4';run(['-loop','1','-i',still,'-i',args.data/f'models/sensevoice/{language}.wav','-shortest','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',speech])
            j,e=board(speech,transcript='auto',language=language)
            transcript=json.loads((Path(j['directory'])/'transcript.json').read_text('utf-8'));assert transcript,language
            results[-1]['transcript']=' '.join(s['text'] for s in transcript)
            assert all(s['from']>=0 and s['to']>s['from'] for s in transcript)
        model=wait(api('/api/models/export',{'output':str(output)}));assert hashlib.sha256((Path(model['directory'])/'SenseVoice-offline.tar.bz2').read_bytes()).hexdigest()==hashlib.sha256(pack.read_bytes()).hexdigest()
    if args.youtube:
        board('https://www.youtube.com/watch?v=aqz-KE-bpKQ',mode='direct',login=False,start=30,duration=5,max_frames=6,transcript='off')
    report={'url':base,'results':results,'models_checked':pack.is_file(),'completed':time.time()}
    (qa/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print('MERGE CHECKS PASSED',flush=True)

if __name__=='__main__':main()
