"""Real HTTP-video / API / browser verification in isolated QA directories."""
import argparse
import functools
import hashlib
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request,urlopen
import uuid

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:18982');parser.add_argument('--ui',action='store_true');parser.add_argument('--ui-only',action='store_true');args=parser.parse_args()
    qa=ROOT/'.local/episodes-qa';qa.mkdir(exist_ok=True);fixtures=ROOT/'.local/ai22-qa/fixtures';cases=[]
    files=[fixtures/'商品.版本2.mp4',fixtures/'第二个视频.mp4'];assert all(p.is_file() for p in files)
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(fixtures)));threading.Thread(target=server.serve_forever,daemon=True).start()
    links=[f'http://127.0.0.1:{server.server_port}/'+quote(p.name) for p in files]
    token=json.load(urlopen(args.url+'/api/state'))['token']
    def api(path,body=None):
        with urlopen(Request(args.url+path,data=json.dumps(body).encode() if body is not None else None,headers={'X-App-Token':token,'Content-Type':'application/json'}),timeout=30) as r:return json.load(r)
    def wait(job):
        deadline=time.monotonic()+240
        while job['status'] in ('queued','running','processing'):
            if time.monotonic()>deadline:raise AssertionError('bounded wait expired')
            time.sleep(.3);job=api('/api/batches/'+job['id'])['job']
        assert job['status']=='done',job
        return job
    def done(name,**extra):cases.append({'name':name,'passed':True,**extra});print('PASS',name,flush=True)
    request={'sources':['online'],'online_sources':[{'input':links[0],'episode':1,'episode_title':'第一课'},
        {'input':links[1],'episode':1,'episode_title':'第一课'},{'input':links[0],'episode':2,'episode_title':'复盘'}],
        'request_id':uuid.uuid4().hex,'output':str(qa/'output'),'outputs':{'puzzles':True,'transcript':True,'ai':True,'zip':True},
        'analysis':{'mode':'direct','login':False,'transcript_file':str(fixtures/'完整.srt'),'max_frames':0,'max_sheets':0,'max_cells_per_sheet':4},
        'ai_package':{'enabled':True,'combined':True,'screen_text':True,'ocr_threads':2}}
    try:
        if not args.ui_only:
            first=api('/api/batches',request);assert api('/api/batches',request)['id']==first['id']
            job=wait(first);base=Path(job['directory']);children=api('/api/batches/'+job['id'])['children']
            assert len(children)==3 and all(j['status']=='done' for j in children)
            index=json.loads((base/'分集目录.json').read_text('utf-8'));assert [i['episode'] for i in index]==[1,2]
            assert (base/'第001集_第一课/本集AI分析包/一起上传给AI').is_dir()
            assert (base/'第002集_复盘').is_dir()
            for child in children:
                assert Path(child['directory']).parent.name.startswith('第00')
                assert Path(child['directory']).name==child['title'].replace('.mp4','') or child['title'] in str(child['directory'])
                assert not list(Path(child['directory']).rglob('*.mp4'))
            joint=base/'联合AI分析包';text='\n'.join(p.read_text('utf-8') for p in (joint/'一起上传给AI').glob('*.txt'))
            assert '第001集：第一课' in text and '第002集：复盘' in text
            timeline=json.loads((joint/'无需上传/视频顺序与时间轴.json').read_text('utf-8'))
            assert [p['episode']['number'] for p in timeline]==[1,1,2]
            assert timeline[1]['from']==timeline[0]['to'] and timeline[2]['from']==timeline[1]['to']
            done('multi-link real collection, two links in episode 1, repeated source in episode 2, title folders, joint boundaries and no video copy',job=job['id'])
            separate={**request,'request_id':uuid.uuid4().hex,'online_sources':[{'input':links[0],'episode':1},{'input':links[1],'episode':2}],
                      'ai_package':{**request['ai_package'],'combined':False}}
            separate_job=wait(api('/api/batches',separate));folder=Path(separate_job['directory'])
            assert not (folder/'联合AI分析包').exists() and (folder/'第001集').is_dir() and (folder/'第002集').is_dir()
            done('unmerged episode export has individual folders and no joint package',job=separate_job['id'])
            # Existing child export and parent regeneration must retain appropriate sources.
            exp=wait(api('/api/exports/'+children[0]['id'],{'request_id':uuid.uuid4().hex,'output':str(qa/'reexport'),
                'outputs':request['outputs'],'ai_package':request['ai_package']}))
            assert len(exp['children'])==1
            done('single child re-export does not accidentally export all links')
            invalid={**request,'request_id':uuid.uuid4().hex,'online_sources':[{'input':links[0],'episode':1,'episode_title':'a'},{'input':links[1],'episode':1,'episode_title':'b'}]}
            try:api('/api/batches',invalid);raise AssertionError('conflicting titles accepted')
            except HTTPError as exc:assert exc.code==400
            done('conflicting episode titles are rejected before queue creation')
            bad={**separate,'request_id':uuid.uuid4().hex,'online_sources':[{'input':links[0]+'missing','episode':1},{'input':links[1],'episode':2}]}
            partial=wait(api('/api/batches',bad));assert partial['counts']['error']==1 and partial['counts']['done']==1
            done('one unreadable URL does not block the next episode')
            prefs={'mode':'custom','items':request['online_sources']}
            api('/api/batch-settings',{'settings':{'outputs':request['outputs'],'ai_package':request['ai_package'],'online_episodes':prefs}})
            assert api('/api/batch-settings')['settings']['online_episodes']==prefs
            done('episode list, grouping and speed preferences survive reload')
        if args.ui:
            from playwright.sync_api import sync_playwright,expect
            chrome=next((ROOT/'.local/build-exe/payload/runtime/browsers').glob('chromium-*/chrome-win64/chrome.exe'))
            errors=[];submitted=[]
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=str(chrome),headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1100});page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(args.url);page.locator('[data-page="analysis"]').click()
                page.wait_for_timeout(700)
                page.locator('#onlineEpisodes [data-episode-action="clear"]').click()
                page.locator('#analysisInput').fill('\n'.join(links));page.locator('#onlineEpisodes [data-episode-action="add"]').click()
                expect(page.locator('#onlineEpisodes .source-row')).to_have_count(2)
                page.locator('#episodeLayout').select_option('one')
                expect(page.locator('[data-episode-number="1"]')).to_have_value('1')
                page.locator('[data-episode-title="1"]').fill('合并演示');page.locator('[data-episode-title="1"]').press('Tab')
                expect(page.locator('[data-episode-title="0"]')).to_have_value('合并演示')
                page.locator('#episodeLayout').select_option('custom');page.locator('[data-episode-number="1"]').fill('2');page.locator('[data-episode-number="1"]').press('Tab')
                page.locator('#episodeCombined').check()
                panel=page.locator('#analysisExportChoices details').filter(has=page.locator('[data-setting="ai_package.ocr_threads"]'))
                panel.locator('summary').click();page.locator('#analysisExportChoices [data-setting="ai_package.ocr_threads"]').select_option('1')
                page.locator('#analysisExportChoices [data-setting="ai_package.enabled"]').check();page.locator('#analysisExportChoices [data-setting="outputs.ai"]').check()
                def submit(route):submitted.append(route.request.post_data_json);route.fulfill(json={'id':'ui-only','status':'processing'})
                page.route('**/api/batches',submit);page.locator('#analysisStart').click();expect(page.locator('#page-library')).to_be_visible()
                assert submitted[0]['online_sources'][0]['episode']==1 and submitted[0]['online_sources'][1]['episode']==2
                assert submitted[0]['ai_package']['ocr_threads']==1 and submitted[0]['ai_package']['combined']
                assert not errors,errors
                page.screenshot(path=str(qa/'episode-ui.png'),full_page=True);browser.close()
            done('browser multi-link input, one/custom grouping, synchronized titles, speed selection, request and no JS errors')
        assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in before.items())
        done('source video hashes unchanged')
        (qa/'verification.json').write_text(json.dumps({'passed':True,'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
    finally:server.shutdown();server.server_close()


if __name__=='__main__':main()
