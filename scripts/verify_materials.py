"""API, real exported packages and browser acceptance, in an isolated app home."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request,urlopen
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from integrations.material_packages import UPLOAD,EXTRA,CONTAINER
from integrations.ai_package import report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url');parser.add_argument('--real',action='store_true');parser.add_argument('--ui',action='store_true');args=parser.parse_args()
    qa=ROOT/'.local/materials-qa';qa.mkdir(exist_ok=True)
    run=qa/('run-'+uuid.uuid4().hex[:8]);run.mkdir();data=run/'data';data.mkdir();output=run/'output';output.mkdir()
    cases=[];proc=None;log=None
    def done(name,**extra):cases.append({'case':name,'passed':True,**extra});print('PASS '+name,flush=True)
    from PIL import Image
    fixtures=[]
    for i in range(2):
        folder=run/('素材 中文 空格 '+str(i+1));(folder/UPLOAD).mkdir(parents=True);(folder/EXTRA).mkdir()
        Image.new('RGB',(80,60),'#136e64').save(folder/UPLOAD/'03_必要细节_F00001.png')
        (folder/UPLOAD/'01_完整内容_001.txt').write_text('完整口述'+str(i)+'价格199\n末尾信息不能丢失',encoding='utf-8')
        (folder/UPLOAD/'00_阅读说明.txt').write_text('该来源文件说明',encoding='utf-8')
        names=sorted(p.name for p in (folder/UPLOAD).iterdir());report(folder,folder.name,names,['F00001 画面文字置信度偏低，请核对必要细节图'],{})
        (folder/EXTRA/'联合内容源.json').write_text(json.dumps({'title':folder.name,'from':2,'to':7,'rows':[],'visuals':[],'images':['03_必要细节_F00001.png'],'timeline':[]}),encoding='utf-8')
        fixtures.append(folder)
    if args.url:base=args.url
    else:
        jobs=[{'id':f'fixture{i:05d}','title':p.name,'kind':'analysis','directory':str(p),'status':'done','created':time.time(),'options':{},'files':[],
               'ai_package':json.loads((p/EXTRA/'资料索引.json').read_text('utf-8'))} for i,p in enumerate(fixtures)]
        (data/'jobs.json').write_text(json.dumps(jobs,ensure_ascii=False),encoding='utf-8')
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        env={**os.environ,'VIDEO_DESK_DATA':str(data),'VIDEO_DESK_PORT':str(port),'VIDEO_DESK_DEFAULT_OUTPUT':str(output),'PYTHONIOENCODING':'utf-8'}
        log=(run/'server.log').open('w',encoding='utf-8');proc=subprocess.Popen([sys.executable,str(ROOT/'app.py')],env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        deadline=time.monotonic()+40
        while True:
            try:state=json.load(urlopen(base+'/api/state',timeout=2));break
            except OSError:
                if time.monotonic()>deadline:raise RuntimeError('QA server startup timeout')
                time.sleep(.2)
        token=state['token']
        def api(path,body=None):
            req=Request(base+path,data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,headers={'Content-Type':'application/json','X-App-Token':token})
            with urlopen(req,timeout=30) as r:return json.load(r)
        def wait(job):
            deadline=time.monotonic()+120
            while job['status'] in ('queued','running','processing'):
                if time.monotonic()>deadline:raise RuntimeError('merge timed out')
                time.sleep(.1);job=next(j for j in api('/api/state')['jobs'] if j['id']==job['id'])
            assert job['status']=='done',(job['status'],job.get('message'));return job
        imported=api('/api/materials/import',{'paths':[str(p) for p in fixtures]})['materials'];assert len(imported)==2
        catalog=api('/api/materials/catalog')['materials'];assert all(m['available'] and m['count']==3 for m in imported)
        done('existing image/text packages selectable without an exported video')
        h=api(f'/api/materials/{imported[0]["id"]}/health');assert h['review_count']==1 and h['missing']==[] and not h['content_missing']
        done('file completeness separated from OCR quality with frame detail link')
        request={'items':[{'id':m['id'],'episode':i+4,'episode_title':f'第{i+4}课'} for i,m in enumerate(imported)],'title':'历史素材联合包','output':str(output),'request_id':uuid.uuid4().hex}
        job=api('/api/materials/merge',request);assert api('/api/materials/merge',request)['id']==job['id'];job=wait(job)
        joint=Path(job['directory']);nodes=json.loads((joint/EXTRA/CONTAINER).read_text('utf-8'))['nodes']
        assert len(nodes)==2
        for n,p in zip(nodes,fixtures):
            for f in n['files']:assert (joint/UPLOAD/f['path']).read_bytes()==(p/UPLOAD/f['name']).read_bytes()
        done('ordered multi-selection merge retains every original file and idempotent request',job=job['id'])
        moved=run/'移动后的素材';fixtures[0].rename(moved)
        assert not next(m for m in api('/api/materials/catalog')['materials'] if m['id']==imported[0]['id'])['available']
        relocated=api('/api/materials/import',{'paths':[str(moved)],'replace_id':imported[0]['id']})['materials'][0]
        assert relocated['available'] and not any(m['id']==imported[0]['id'] for m in api('/api/materials/catalog')['materials'])
        fixtures[0]=moved
        done('moved package re-location replaces stale catalog entry')
        nested=api('/api/materials/import',{'paths':[str(joint)]})['materials'][0]
        again=wait(api('/api/materials/merge',{'items':[{'id':nested['id']},{'id':relocated['id']}],'title':'再次合并','output':str(output),'request_id':uuid.uuid4().hex}))
        assert len(json.loads((Path(again['directory'])/EXTRA/CONTAINER).read_text('utf-8'))['nodes'])==3
        done('joint package can merge again without multiplying ancestor indexes')
        bad=fixtures[1]/UPLOAD/'03_必要细节_F00001.png';original=bad.read_bytes();bad.unlink()
        try:api('/api/materials/merge',{**request,'request_id':uuid.uuid4().hex});raise AssertionError('missing files accepted')
        except HTTPError as e:assert e.code==409
        bad.write_bytes(original);done('missing required files block completion and can be restored')
        if args.real:
            home=Path(os.environ['LOCALAPPDATA'])/'VideoDesk'
            originals=[Path(j['directory']) for j in json.loads((home/'data/jobs.json').read_text('utf-8')) if j['status']=='done' and j.get('ai_package')]
            assert len(originals)>=2
            originals=originals[:2]
            before={str(p):hashlib.file_digest(p.open('rb'),'sha256').hexdigest() for f in originals for p in (f/UPLOAD).rglob('*') if p.is_file()}
            real=api('/api/materials/import',{'paths':[str(p) for p in originals]})['materials']
            realjob=wait(api('/api/materials/merge',{'items':[{'id':m['id'],'episode':i+1} for i,m in enumerate(real)],'title':'两份已有资料实测合集','output':str(output),'request_id':uuid.uuid4().hex}))
            realbase=Path(realjob['directory']);content=json.loads((realbase/EXTRA/CONTAINER).read_text('utf-8'))
            mapped=0
            for node in content['nodes']:
                for f in node['files']:
                    assert hashlib.file_digest((realbase/UPLOAD/f['path']).open('rb'),'sha256').hexdigest()==f['sha256'];mapped+=1
            assert before=={str(p):hashlib.file_digest(p.open('rb'),'sha256').hexdigest() for f in originals for p in (f/UPLOAD).rglob('*') if p.is_file()}
            timeline=json.loads((realbase/EXTRA/'视频顺序与时间轴.json').read_text('utf-8'));assert timeline[1]['from']==timeline[0]['to']
            for m in real:
                h=api(f'/api/materials/{m["id"]}/health')
                if h['count']==176:assert h['review_count']==133 and len(h['missing'])==0
            done('two production packages: all files and hashes preserved; 176-file OCR warning clarified',copied_files=mapped,directory=str(realbase))
        if args.ui:
            from playwright.sync_api import sync_playwright,expect
            chrome=next((ROOT/'.local/build-exe/payload/runtime/browsers').glob('chromium-*/chrome-win64/chrome.exe'))
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=str(chrome),headless=True);page=browser.new_page(viewport={'width':1440,'height':1100});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(base);page.locator('[data-page="analysis"]').click();page.locator('#analysisSource').select_option('job')
                expect(page.locator('[data-material-select]').first).to_be_visible();expect(page.locator('#analysisStart')).to_be_hidden()
                page.locator('#materialSearch').fill('移动后的素材');page.locator('#materialSearch').fill('')
                page.locator(f'[data-material-select="{relocated["id"]}"]').check();page.locator(f'[data-material-select="{imported[1]["id"]}"]').check()
                expect(page.locator('#materialQueue .material-selected')).to_have_count(2)
                page.locator('[data-material-down]').first.click();page.locator('[data-material-episode]').first.fill('9');page.locator('[data-material-title]').first.fill('页面测试集标题');page.locator('#materialTitle').fill('页面合并实测')
                page.locator('#output').fill(str(output));page.locator('#materialMerge').click();expect(page.locator('#page-library')).to_be_visible()
                page.wait_for_function("jobs.some(j=>j.title==='页面合并实测'&&j.status==='done')",timeout=20000)
                page.locator('[data-page="analysis"]').click();page.locator('[data-material-health]').first.click();expect(page.locator('.material-health-dialog')).to_be_visible();page.locator('[data-material-close]').click()
                # Native dialog state contract, including failure and cancel, with no desktop interaction.
                picker={'fail':True,'polls':0}
                def picker_route(route):
                    if route.request.method=='POST' and route.request.url.endswith('/api/pickers'):
                        if picker['fail']:picker['fail']=False;route.fulfill(status=500,json={'detail':'测试选择失败'})
                        else:route.fulfill(json={'id':'ui-picker','status':'pending'})
                    elif route.request.url.endswith('/cancel'):route.fulfill(json={'status':'cancelled'});picker['polls']=99
                    else:
                        picker['polls']+=1;route.fulfill(json={'status':'cancelled' if picker['polls']>5 else 'pending','paths':[]})
                page.route('**/api/pickers**',picker_route)
                page.locator('#materialImport').click();expect(page.locator('#materialImport')).to_be_enabled()
                page.locator('#materialImport').click();expect(page.locator('#materialCancelPicker')).to_be_visible();page.locator('#materialCancelPicker').click();expect(page.locator('#materialImport')).to_be_enabled()
                page.unroute('**/api/pickers**',picker_route)
                expect(page.locator('.brand-icon img')).to_be_visible();page.screenshot(path=str(qa/'materials-ui.png'),full_page=True)
                assert not errors,errors;browser.close()
            done('browser multi-select, order, episode edit, merge, status dialog, failed picker retry and cancel')
        (qa/'verification.json').write_text(json.dumps({'passed':True,'version':state['version'],'url':base,'run':str(run),'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        if proc:
            proc.terminate();proc.wait(timeout=15)
        if log:log.close()


if __name__=='__main__':main()
