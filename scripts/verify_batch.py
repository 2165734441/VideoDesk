"""2.1 real API / FFmpeg / WebSocket acceptance suite; never touches user data."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import uuid
import zipfile
import imageio_ffmpeg
from websockets.sync.client import connect

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:18971');parser.add_argument('--data',type=Path,default=ROOT/'.local/batch-qa/data');parser.add_argument('--report',type=Path,default=ROOT/'.local/batch-qa/verification.json');parser.add_argument('--qa-dir',type=Path,default=ROOT/'.local/batch-qa');args=parser.parse_args()
    base=args.url;token=json.load(urlopen(base+'/api/state'))['token'];qa=args.qa_dir.resolve();assert qa.is_relative_to((ROOT/'.local').resolve());fixture=qa/'fixtures';fixture.mkdir(parents=True,exist_ok=True);output=qa/'output';output.mkdir(exist_ok=True)
    def api(path,body=None):
        with urlopen(Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json','X-App-Token':token}),timeout=20) as response:return json.load(response)
    def wait(parent,expected='done'):
        deadline=time.monotonic()+180
        while True:
            result=api('/api/batches/'+parent['id']);job=result['job']
            if job['status'] not in ('queued','running','processing'):break
            if time.monotonic()>deadline:raise AssertionError('timeout '+str(result))
            time.sleep(.12)
        assert job['status']==expected,result
        return result
    def register(path):return api('/api/sources/register',{'paths':[str(path)]})['sources'][0]['id']
    def create(ids,outputs,**opts):
        return api('/api/batches',{'sources':ids,'output':str(output),'kind':'rules','outputs':outputs,'frames':{'mode':'seconds','value':.5},'request_id':uuid.uuid4().hex,**opts})
    def visible(child):return [f['name'] for f in child['files']]
    def child(result):return result['children'][0]
    results=[]
    def record(case,**extra):results.append({'case':case,**extra});print(json.dumps(results[-1],ensure_ascii=False),flush=True)
    ff=imageio_ffmpeg.get_ffmpeg_exe();probe=ROOT/'runtime-tools/ffprobe.exe'
    def run(parts):subprocess.run([ff,'-v','error','-nostdin','-y',*map(str,parts)],check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    video=fixture/'中文 商品, 空格.mp4';silent=fixture/'无声音.mp4'
    run(['-f','lavfi','-i','testsrc2=size=320x240:rate=20','-f','lavfi','-i','sine=frequency=700:sample_rate=48000','-t','5.35','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',video])
    run(['-f','lavfi','-i','color=c=red:size=320x240:rate=20','-t','5.35','-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',silent])
    nested=fixture/'子文件夹';nested.mkdir(exist_ok=True);same=nested/video.name;shutil.copy2(video,same)
    broken=fixture/'损坏.mp4';broken.write_bytes(b'not a video');empty=fixture/'空文件.mp4';empty.write_bytes(b'')
    text=fixture/'不是视频.txt';text.write_text('fixture',encoding='utf-8')
    originals={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (video,silent,same,broken,empty,text)}
    rows=api('/api/sources/register',{'paths':f'\n "{video}" \n“{same}”\n{broken}\n{empty}\n{text}\n{fixture/"missing.mp4"}\n'})['sources']
    assert len(rows)==6 and rows[3]['status']=='invalid' and rows[4]['status']=='invalid' and rows[5]['status']=='invalid'
    assert rows[0]['id']==register(video)
    discovered=api('/api/sources/discover',{'folder':str(fixture),'recursive':True});flat=api('/api/sources/discover',{'folder':str(fixture),'recursive':False})
    assert len(discovered['sources'])==len(flat['sources'])+1
    ids=[rows[0]['id'],rows[1]['id'],rows[2]['id']];vs,ss=ids[0],register(silent)
    record('quoted paths / recursive folder / duplicates / bad files',sources=len(rows))
    previous=api('/api/batch-settings')
    try:
        saved={'source_ids':[vs,ss],'settings':{'outputs':{'audio':True},'dedup':{'enabled':True,'mode':'similar','threshold':12},'segments':{'enabled':True,'seconds':2.5,'organize':False}}}
        api('/api/batch-settings',saved);loaded=api('/api/batch-settings')
        assert [s['id'] for s in loaded['sources']]==[vs,ss] and loaded['settings']['outputs']['audio'] and loaded['settings']['segments']['seconds']==2.5
        try:api('/api/batch-settings',{'settings':{'dedup':{'threshold':17}}})
        except HTTPError as exc:assert exc.code==400
        else:raise AssertionError('invalid preferences accepted')
        assert api('/api/batch-settings')['settings']==loaded['settings']
    finally:
        api('/api/batch-settings',{'source_ids':[s['id'] for s in previous.get('sources',[])],'settings':previous.get('settings',{}),'batch':previous.get('batch',''),'pending':previous.get('pending')})
    record('workflow preferences survive origin changes and reject invalid values')
    parent=create(ids,{'frames':True});result=wait(parent);assert result['job']['counts']['done']==2 and result['job']['counts']['error']==1
    assert len({j['directory'] for j in result['children']})==3
    for c in result['children'][:2]:assert all(n.endswith('.jpg') for n in visible(c)),c
    record('mixed batch isolates corrupt file and same-name outputs',batch=parent['id'])
    for mode,value,expected in [('seconds',.5,2),('fps',4,4),('frames',5,4),('all',1,20)]:
        result=wait(create([vs],{'frames':True},frames={'mode':mode,'value':value,'start':1.125,'duration':1,'limit':2000}))
        c=child(result);index=json.loads((args.data/'batch-work'/c['id']/'part-0001/frame-index.json').read_text('utf-8'))
        stamps=[r['seconds'] for r in index['timeline']]
        assert len(stamps)==expected and all(1.125<=n<2.125+.00001 for n in stamps),(mode,stamps)
        record('frame mode '+mode,images=len(stamps),first=stamps[0])

    for kind,expected in [('frames',{'.jpg'}),('audio',{'.m4a'}),('puzzles',{'.png'}),('transcript',{'.srt','.txt'})]:
        opts={}
        if kind in ('puzzles','transcript'):
            srt=fixture/'cross-boundary.srt';srt.write_text('1\n00:00:01,500 --> 00:00:03,500\n跨段商品介绍\n\n2\n00:00:04,000 --> 00:00:05,350\n最后一段\n',encoding='utf-8')
            opts['analysis']={'transcript_file':str(srt),'transcript':'subtitles','max_frames':8,'max_sheets':2}
        result=wait(create([vs],{kind:True},**opts));c=child(result);names=visible(c)
        assert names and {Path(n).suffix for n in names}<=expected,(kind,names)
        assert not any(p.suffix=='.mp4' for p in Path(c['directory']).rglob('*'))
        if kind=='audio':assert not (args.data/'batch-work'/c['id']/'part-0001/frames').exists() and not (args.data/'batch-work'/c['id']/'part-0001/.speech').exists()
        record('only '+kind,job=c['id'],files=len(names))
    result=wait(create([ss],{'audio':True}),expected='error');assert '没有音轨' in child(result)['message'];assert not visible(child(result));record('missing sole requested output is error')
    for mode,threshold in [('exact',0),('similar',4),('similar',8),('similar',12),('similar',16)]:
        result=wait(create([ss],{'frames':True},dedup={'enabled':True,'mode':mode,'threshold':threshold},segments={'enabled':True,'seconds':2,'organize':True}))
        c=child(result);names=visible(c);assert 3<=len(names)<11 and len({Path(n).parts[0] for n in names})==3
        record('dedup '+mode+' '+str(threshold),images=len(names),job=c['id'])
    srt=fixture/'cross-boundary.srt'
    combo=wait(create([vs],{'frames':True,'audio':True,'puzzles':True,'transcript':True,'clips':True,'ai':True,'zip':True},segments={'enabled':True,'seconds':2,'organize':True},analysis={'transcript_file':str(srt),'transcript':'subtitles','max_frames':8,'max_sheets':2}))
    c=child(combo);directory=Path(c['directory']);clips=sorted(directory.glob('*/clips/*.mp4'));assert len(clips)==3
    errors=[]
    for index,path in enumerate(clips):
        media=json.loads(subprocess.check_output([str(probe),'-v','error','-show_format','-show_streams','-of','json',str(path)],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)))
        stream=next(s for s in media['streams'] if s['codec_type']=='video');expected=2 if index<2 else 1.35
        error=abs(float(stream['duration'])-expected);errors.append(error);assert error<=.05+1e-5,(path,error)
        assert (stream['width'],stream['height'],stream['codec_name'])==(320,240,'h264')
        assert next(s for s in media['streams'] if s['codec_type']=='audio')['codec_name']=='aac'
    parts=sorted(p for p in directory.iterdir() if p.is_dir());assert len(parts)==3
    assert '00:00:02,000' in (parts[1]/'transcript/原视频时间.srt').read_text('utf-8')
    assert '00:00:00,000' in (parts[1]/'transcript/片段内时间.srt').read_text('utf-8')
    with zipfile.ZipFile(directory/'素材包.zip') as z:assert z.testzip() is None and not any(n.startswith('.') for n in z.namelist())
    resultdata=api('/api/analysis/'+c['id']);assert resultdata['evidence']['sheets'] and resultdata['transcript']
    assert api('/api/analysis/'+c['id']+'/locate',{'query':'商品'})['matches']
    record('combined outputs / clipped subtitles / H264 AAC accuracy / ZIP',job=c['id'],segment_errors=errors)
    zoom=api('/api/analysis/'+c['id']+'/zoom',{'cell':resultdata['evidence']['cells'][0]['cell']})
    zoomparent={'id':zoom['options']['parent_id']};zoomed=child(wait(zoomparent));assert any(n.endswith('.png') for n in visible(zoomed))
    record('batch result supports upstream cell zoom and transcript alignment',job=zoomed['id'])
    silent_clip=child(wait(create([ss],{'clips':True},segments={'enabled':True,'seconds':2,'organize':True})))
    assert len(visible(silent_clip))==3 and all(n.endswith('.mp4') for n in visible(silent_clip))
    record('video-only export with a silent source')

    ungrouped=child(wait(create([vs],{'audio':True,'clips':True},segments={'enabled':True,'seconds':2,'organize':False})))
    assert not any(n.startswith('片段-') for n in visible(ungrouped));assert len(visible(ungrouped))==6
    record('segment material without per-segment folders')
    request={'sources':[vs],'output':str(output),'kind':'rules','outputs':{'frames':True},'request_id':uuid.uuid4().hex}
    first=api('/api/batches',request);second=api('/api/batches',request);assert first['id']==second['id'];wait(first)
    request['outputs']={'audio':True}
    try:api('/api/batches',request);raise AssertionError('id collision accepted')
    except HTTPError as error:assert error.code==409
    expreq={'outputs':{'audio':True},'request_id':uuid.uuid4().hex,'output':str(output)}
    one=api('/api/exports/'+c['id'],expreq);two=api('/api/exports/'+c['id'],expreq);assert one['id']==two['id'];assert all(n.endswith('.m4a') for n in visible(child(wait(one))))
    record('batch / export idempotency and selected-only re-export')

    class Provider:
        def __init__(self,source_id):self.id=source_id;self.socket=None;self.maximum=0;self.reads=0;self.error='';self.ended=False;self.gate=threading.Event();self.gate.set()
        def start(self):
            self.context=connect(base.replace('http:','ws:')+f'/api/sources/{self.id}/read',origin=base,proxy=None,max_size=2*1024**2)
            self.socket=self.context.__enter__()
            self.socket.send(json.dumps({'token':token}));assert json.loads(self.socket.recv())['ready']
            def loop():
                try:
                    with video.open('rb') as file:
                        while not self.ended:
                            request=json.loads(self.socket.recv());self.gate.wait(10)
                            size=request['length'];self.maximum=max(self.maximum,size);self.reads+=1;assert size<=1024**2
                            file.seek(request['offset']);self.socket.send(request['read'].encode()+file.read(size))
                except Exception as exc:
                    if not self.ended:self.error=str(exc)
            self.thread=threading.Thread(target=loop,daemon=True);self.thread.start()
        def stop(self):
            self.ended=True;self.gate.set();self.socket.close();self.thread.join(timeout=5);self.context.__exit__(None,None,None)
    size=video.stat().st_size;fingerprint=hashlib.sha256(video.read_bytes()[:1024**2]+video.read_bytes()[max(1024**2,size-1024**2):]).hexdigest()
    metadata={'name':video.name,'size':size,'modified':1234,'fingerprint':fingerprint}
    source=api('/api/sources/browser',metadata);provider=Provider(source['id']);provider.start()
    browser=wait(create([source['id']],{'frames':True,'audio':True},segments={'enabled':True,'seconds':2,'organize':True}));assert provider.reads and not provider.error,provider.error
    browserchild=child(browser);assert not any(p.suffix=='.mp4' for p in Path(browserchild['directory']).rglob('*'));provider.stop()
    # Disconnected source releases media worker, allowing the next native task.
    waiting=create([source['id'],vs],{'frames':True},segments={'enabled':True,'seconds':2,'organize':True});partial=wait(waiting,expected='waiting_source');assert partial['children'][1]['status']=='done'
    bad={**metadata,'source_id':source['id'],'fingerprint':'b'*64}
    try:api('/api/sources/browser',bad);raise AssertionError('mismatched file accepted')
    except HTTPError as error:assert error.code==409
    api('/api/sources/browser',{**metadata,'source_id':source['id']});provider=Provider(source['id']);provider.start();api('/api/batches/'+waiting['id']+'/retry',{});continued=wait(waiting);assert child(continued)['status']=='done';provider.stop()
    record('browser File / WebSocket streaming / no original copy / release worker / reattach',max_chunk=provider.maximum)
    api('/api/sources/browser',{**metadata,'source_id':source['id']});provider=Provider(source['id']);provider.start()
    # Pause reads once the first completed checkpoint is visible, then disconnect
    # during the next segment; completed material hashes must survive resume.
    live=create([source['id']],{'frames':True,'clips':True},segments={'enabled':True,'seconds':.5,'organize':True})
    end=time.monotonic()+30;captured=None
    while time.monotonic()<end:
        snapshot=api('/api/batches/'+live['id']);item=child(snapshot)
        if item.get('completed_segments',0)>=1 and item['status']=='running':
            provider.stop();captured=item;break
        if item['status']=='done':break
        time.sleep(.01)
    assert captured,'fixture completed too quickly to exercise interrupted segment'
    paused=wait(live,expected='waiting_source');before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(child(paused)['directory']).rglob('*') if p.is_file() and not p.name.startswith('.')}
    assert before
    api('/api/sources/browser',{**metadata,'source_id':source['id']});provider=Provider(source['id']);provider.start();api('/api/batches/'+live['id']+'/retry',{});finished=wait(live);provider.stop()
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in before.items())
    record('resume unfinished segments preserves completed material',completed_before=len(before),completed_after=child(finished)['completed_segments'])
    cancelled=create([source['id'],vs],{'frames':True});api('/api/jobs/'+cancelled['id']+'/cancel',{});ended=wait(cancelled,expected='cancelled');assert all(j['status'] in ('cancelled','done') for j in ended['children'])
    record('cancel batch including waiting source')
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in originals.items())
    record('original file hashes unchanged',files=len(originals))
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps({'version':'2.1.0','url':base,'results':results,'passed':True},ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS',len(results),flush=True)

if __name__=='__main__':main()
