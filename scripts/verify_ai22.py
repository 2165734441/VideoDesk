"""Real FFmpeg/API acceptance in a dedicated fixture directory, never user data."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
from urllib.request import Request,urlopen
import uuid
import zipfile
import imageio_ffmpeg
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:18982');args=parser.parse_args()
    qa=ROOT/'.local/ai22-qa';fixtures=qa/'fixtures';fixtures.mkdir(parents=True,exist_ok=True);output=qa/'output';output.mkdir(exist_ok=True)
    state=json.load(urlopen(args.url+'/api/state'));token=state['token'];cases=[]
    def api(path,body=None):
        with urlopen(Request(args.url+path,data=json.dumps(body).encode() if body is not None else None,headers={'X-App-Token':token,'Content-Type':'application/json'}),timeout=30) as r:return json.load(r)
    def wait(job):
        deadline=time.monotonic()+600;stamp=''
        while job['status'] in ['queued','running','processing']:
            if stamp!=job['message']:print(job['message'],flush=True);stamp=job['message']
            if time.monotonic()>deadline:raise AssertionError('timeout '+str(job))
            time.sleep(.3);job=next(j for j in api('/api/state')['jobs'] if j['id']==job['id'])
        assert job['status']=='done',job
        return job
    def done(case,**extra):cases.append({'case':case,**extra});print(json.dumps(cases[-1],ensure_ascii=False),flush=True)
    ff=imageio_ffmpeg.get_ffmpeg_exe()
    def run(parts):subprocess.run([ff,'-v','error','-y',*map(str,parts)],check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    texts=['商品说明：完整组合 A，原价1299，现价199','操作第二步：版本2，不要丢失末尾信息']
    images=[]
    for i,text in enumerate(texts):
        im=Image.new('RGB',(1280,720),'white');d=ImageDraw.Draw(im);d.text((60,80),text,fill='black',font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',34));im.save(fixtures/f'{i}.png');images.append(fixtures/f'{i}.png')
    video=fixtures/'商品.版本2.mp4';second=fixtures/'第二个视频.mp4'
    run(['-loop','1','-i',images[0],'-loop','1','-i',images[1],'-f','lavfi','-i','sine=frequency=440:sample_rate=16000','-filter_complex','[0:v]trim=duration=2,setpts=PTS-STARTPTS[a];[1:v]trim=duration=2.35,setpts=PTS-STARTPTS[b];[a][b]concat=n=2:v=1:a=0[v]','-map','[v]','-map','2:a','-t','4.35','-r','20','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',video])
    run(['-loop','1','-i',images[1],'-t','2.35','-r','20','-c:v','libx264','-pix_fmt','yuv420p',second])
    srt=fixtures/'完整.srt';full='商品完整台词：价格1299元改成199元。'+('每个字都必须保留，不能按格子截断。'*60)+'最后一句。'
    srt.write_text('1\n00:00:00,000 --> 00:00:04,350\n'+full+'\n',encoding='utf-8')
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (video,second,srt)}
    ids=[s['id'] for s in api('/api/sources/register',{'paths':[str(video),str(second)]})['sources']]
    body={'sources':ids,'kind':'board','output':str(output),'outputs':{'puzzles':True,'transcript':True,'ai':True,'zip':True},'analysis':{'transcript':'auto','transcript_file':str(srt),'max_frames':0,'max_sheets':0,'max_cells_per_sheet':1},'ai_package':{'enabled':True,'combined':True,'screen_text':True,'scan_seconds':1},'request_id':uuid.uuid4().hex}
    job=wait(api('/api/batches',body));base=Path(job['directory']);children=api('/api/batches/'+job['id'])['children']
    assert any(Path(c['directory']).name=='商品.版本2' for c in children),children
    combined=base/'联合AI分析包/一起上传给AI';assert combined.is_dir()
    text='\n'.join(p.read_text('utf-8') for p in combined.glob('*.txt'))
    assert full in text and 'V01 标题：商品.版本2.mp4' in text and 'V02 标题：第二个视频.mp4' in text
    assert '1299' in text and '199' in text
    for child in children:
        result=api('/api/analysis/'+child['id']);assert all(len(s['cells'])<=1 for s in result['evidence']['sheets'])
        private=Path(child['directory'])/'无需上传/联合内容源.json';data=json.loads(private.read_text('utf-8'))
        assert data['timeline'][0][0]==0 and all(0<=t<data['to'] for t,_ in data['timeline'])
        assert len(list((Path(child['directory'])/'一起上传给AI').glob('02_*.png')))==data['metrics']['unique_frames']
    with zipfile.ZipFile(base/'联合AI分析包.zip') as archive:assert all(n.startswith('一起上传给AI/') for n in archive.namelist())
    done('joint pack: queue order / complete transcript / OCR numbers / title folders / cap1 / upload-only ZIP',job=job['id'],directory=str(base))
    # Real engine geometry with arbitrary custom count, independently of AI package.
    for limit in [4,9]:
        current=wait(api('/api/analysis',{'input':str(video),'output':str(output),'transcript':'off','keep_frames':False,'max_frames':0,'max_sheets':0,'integral_seconds':1,'max_cells_per_sheet':limit,'ai_package':{'enabled':False}}))
        result=api('/api/analysis/'+current['id']);assert all(len(s['cells'])<=limit for s in result['evidence']['sheets'])
        assert sum(len(s['cells']) for s in result['evidence']['sheets'])==len(result['evidence']['cells'])
        done('real storyboard custom cap',limit=limit,frames=len(result['evidence']['cells']))
    # Replace one finished video folder only after regeneration succeeds.
    child=children[0];old=Path(child['directory']);old_files=set(f['name'] for f in child['files'])
    request={'request_id':uuid.uuid4().hex,'outputs':{'frames':True},'analysis':body['analysis'],'ai_package':{'enabled':False},'replace_job_id':child['id']}
    regenerated=wait(api('/api/exports/'+child['id'],request));assert Path(regenerated['directory'])==old,regenerated
    assert api('/api/exports/'+child['id'],request)['id']==regenerated['id']
    assert any(old.rglob('*.jpg')) and not (old/'一起上传给AI').exists()
    old_record=next(j for j in api('/api/state')['jobs'] if j['id']==child['id']);assert old_record['superseded_by']==regenerated['id']
    done('regeneration replaces generated folder after success; originals intact',job=regenerated['id'])
    # A user-added file prevents replacement, leaving both results reviewable.
    (old/'用户自己的文件.txt').write_text('keep',encoding='utf-8')
    fallback=wait(api('/api/exports/'+regenerated['id'],{**request,'request_id':uuid.uuid4().hex,'replace_job_id':regenerated['id']}))
    assert Path(fallback['directory'])!=old and (old/'用户自己的文件.txt').read_text('utf-8')=='keep'
    assert '另存' in fallback['replacement_note'];done('user-added file blocks overwrite, new result saved separately')
    preview=api('/api/materials/'+fallback['id']+'/cleanup');assert preview['files']
    first=preview['files'][0];path=Path(preview['directory'])/first;moved=qa/('moved-'+uuid.uuid4().hex+path.suffix);path.rename(moved)
    cleaned=api('/api/materials/'+fallback['id']+'/cleanup',{});assert moved.exists() and any(x['name']==first for x in cleaned['skipped'])
    for p,sha in hashes.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==sha
    done('cleanup skips relocated materials and preserves source SHA256',skipped=len(cleaned['skipped']))
    report={'passed':True,'cases':cases,'original_hashes':hashes,'server':args.url};(qa/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
