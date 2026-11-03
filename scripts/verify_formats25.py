"""Synthetic codec/clock/orientation coverage using the isolated 18977 server."""
import hashlib
import json
from pathlib import Path
import subprocess
import time
from urllib.request import Request,urlopen
import uuid
import imageio_ffmpeg
from PIL import Image

ROOT=Path(__file__).resolve().parents[1];QA=ROOT/'.local/v25-qa';BASE='http://127.0.0.1:18977'
TOKEN=json.load(urlopen(BASE+'/api/state'))['token']


def api(path,body=None):
    with urlopen(Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json','X-App-Token':TOKEN}),timeout=30) as r:return json.load(r)


def main():
    folder=QA/'formats';folder.mkdir(exist_ok=True);ff=imageio_ffmpeg.get_ffmpeg_exe();cases=[]
    def run(args):subprocess.run([ff,'-v','error','-nostdin','-y',*args],check=True,capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    for name,codec,extra in [('h264','libx264',[]),('hevc','libx265',['-x265-params','pools=1:frame-threads=1:log-level=error']),('vp9','libvpx-vp9',['-deadline','realtime']),('av1','libaom-av1',['-cpu-used','8'])]:
        path=folder/(name+('.webm' if name in ('vp9','av1') else '.mp4'))
        run(['-f','lavfi','-i','testsrc2=size=96x64:rate=6','-t','1','-an','-c:v',codec,'-threads','1',*extra,str(path)])
    run(['-display_rotation:v:0','90','-i',str(folder/'h264.mp4'),'-c','copy',str(folder/'旋转 空格.mp4')])
    run(['-f','lavfi','-i','testsrc2=size=96x64:rate=12','-t','2','-vf',r'select=not(mod(n\,2))+not(mod(n\,3))','-fps_mode','vfr','-c:v','libx264','-threads','1',str(folder/'vfr.mp4')])
    run(['-f','lavfi','-i','color=size=96x64:rate=1','-t','600','-c:v','libx264','-threads','1',str(folder/'long.mp4')])
    run(['-f','lavfi','-i','sine=frequency=500','-t','1','-c:a','aac',str(folder/'audio-only.mp4')])
    (folder/'broken.mp4').write_bytes(b'invalid media')
    for path in sorted(folder.iterdir()):
        original=hashlib.sha256(path.read_bytes()).hexdigest()
        source=api('/api/sources/register',{'paths':[str(path)]})['sources'][0]
        for _ in range(150):
            source=next(s for s in api('/api/sources')['sources'] if s['id']==source['id'])
            if source['status']!='validating':break
            time.sleep(.1)
        if path.stem in ('broken','audio-only'):
            expected='container_error' if path.stem=='broken' else 'missing_video_stream'
            assert source['error_category']==expected,source
            cases.append({'case':path.stem,'category':expected});continue
        assert source['status']=='ready',source
        parent=api('/api/batches',{'sources':[source['id']],'output':str(QA/'format-output'),'workflow':'rules','kind':'rules','outputs':{'frames':True},'frames':{'mode':'seconds','value':300 if path.stem=='long' else .25},'request_id':uuid.uuid4().hex})
        for _ in range(300):
            result=api('/api/batches/'+parent['id'])
            if result['job']['status'] not in ('processing','running','queued'):break
            time.sleep(.1)
        assert result['job']['status']=='done',result
        child=result['children'][0];files=[Path(child['directory'])/x['name'] for x in child['files']]
        assert files and all(p.suffix=='.jpg' for p in files),files
        if path.stem=='旋转 空格':
            with Image.open(files[0]) as im:assert im.size==(64,96),im.size
        assert hashlib.sha256(path.read_bytes()).hexdigest()==original
        cases.append({'case':path.stem,'duration':source['duration'],'images':len(files),'job':child['id']})
    (QA/'formats25.json').write_text(json.dumps({'passed':True,'cases':cases},ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(cases,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
