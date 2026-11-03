"""Browser source entry interactions, real file WebSocket, mocked native choices."""
import json
import os
from pathlib import Path
import shutil
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
QA=ROOT/'.local/v25-qa'


def main():
    folder=QA/'来源 中文 空格';(folder/'子目录').mkdir(parents=True,exist_ok=True)
    for target in (folder/'商品 版本2.mp4',folder/'子目录/第二段.mp4'):
        if not target.exists():shutil.copy2(QA/'sample.mp4',target)
    errors=[];cases=[]
    chrome=next((ROOT/'.local/build-exe/payload/runtime/browsers').glob('chromium-*/chrome-win64/chrome.exe'))
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=str(chrome),headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
        traffic={'sent':0,'received':0}
        def socket(ws):
            def sent(data):
                traffic['sent']+=1
                if traffic['sent']<6:print('sent',len(data),flush=True)
            def received(data):
                traffic['received']+=1
                if traffic['received']<6:print('received',data if isinstance(data,str) else len(data),flush=True)
            ws.on('framesent',sent);ws.on('framereceived',received)
            ws.on('close',lambda:print('socket closed',traffic,flush=True))
        page.on('websocket',socket)
        page.goto(os.environ.get('VIDEO_DESK_TEST_URL','http://127.0.0.1:18976'));page.wait_for_function('serverSettingsLoaded')
        page.locator('[data-page="extract"]').click()
        host=page.locator('#extractLocalPanel .local-source-panel');host.locator('[data-source-action="clear"]').click()
        state={'status':'cancelled'}
        def picker(route):
            route.fulfill(json={'id':'qa-picker25','status':'pending'} if route.request.method=='POST' else {'id':'qa-picker25','status':state['status'],'paths':[str(folder)] if state['status']=='done' else [],'error':'测试选择失败'})
        page.route('**/api/pickers',picker);page.route('**/api/pickers/qa-picker25',picker)
        button=host.locator('[data-source-action="folder"]')
        button.click();expect(button).to_be_enabled()
        state['status']='error';button.click();expect(button).to_be_enabled()
        state['status']='done';button.click();expect(host.locator('.source-row')).to_have_count(2)
        cases+=['cancel and failed picker release controls','native folder result uses real recursive discovery']
        host.locator('[data-source-action="clear"]').click();host.locator('[data-source-recursive]').uncheck()
        button.click();expect(host.locator('.source-row')).to_have_count(1)
        cases+=['folder without subdirectories']
        host.locator('[data-source-action="clear"]').click()
        host.locator('[data-source-action="paste"]').click()
        host.locator('[data-source-paths]').fill('"'+str(folder/'商品 版本2.mp4')+'"\n"'+str(folder/'子目录/第二段.mp4')+'"')
        host.locator('[data-source-action="paths"]').click();expect(host.locator('.source-row')).to_have_count(2)
        cases+=['quoted multiple paths with Chinese and spaces']
        host.locator('[data-source-action="clear"]').click()
        host.locator('[data-source-web]').set_input_files(folder/'商品 版本2.mp4')
        expect(host.locator('.source-row')).to_have_count(1)
        page.wait_for_function('localSources.length===1&&localSources[0].status==="ready"',timeout=60000)
        try:page.wait_for_function('localSources[0].duration>0',timeout=60000)
        except Exception:
            print(page.evaluate('localSources'),flush=True)
            print(page.evaluate('({sockets:[...sourceSockets].map(([id,s])=>({id,ready:s.readyState})),files:browserFiles.size})'),flush=True)
            raise
        cases+=['browser file sends real bounded WebSocket blocks and ffprobe validates it']
        # Drag the same browser File through the actual drop handler.
        page.evaluate("window.qaDragFile=browserFiles.values().next().value")
        host.locator('[data-source-action="clear"]').click()
        host.locator('.source-drop').evaluate("el=>{const dt=new DataTransfer();dt.items.add(window.qaDragFile);el.dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:dt}));}")
        expect(host.locator('.source-row')).to_have_count(1)
        page.wait_for_function('localSources[0]?.status==="ready"',timeout=60000)
        cases+=['dragged File reads without copying original video']
        # Check modern extraction controls remain independent after this shared import.
        expect(page.locator('#extractExportChoices [data-setting="outputs.frames"]')).to_be_checked()
        assert not page.locator('#page-extract [data-setting="outputs.ai"]').count()
        page.screenshot(path=str(QA/'sources25.png'),full_page=True)
        assert not errors,errors
        (QA/'sources25.json').write_text(json.dumps({'passed':True,'cases':cases,'errors':errors},ensure_ascii=False,indent=2),'utf-8')
        browser.close()


if __name__=='__main__':main()
