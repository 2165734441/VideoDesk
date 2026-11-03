"""Folder import interaction checks against an isolated backend and browser."""
import json
from pathlib import Path
import shutil
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]


def main():
    qa=ROOT/'.local/stability-qa';folder=qa/'folder-import';(folder/'子目录').mkdir(parents=True,exist_ok=True)
    shutil.copy2(ROOT/'.local/ai22-qa/fixtures/商品.版本2.mp4',folder/'商品 视频.mp4')
    shutil.copy2(ROOT/'.local/ai22-qa/fixtures/第二个视频.mp4',folder/'子目录/第二段.mp4')
    (folder/'说明.txt').write_text('不是视频',encoding='utf-8')
    chrome=next((ROOT/'.local/build-exe/payload/runtime/browsers').glob('chromium-*/chrome-win64/chrome.exe'))
    errors=[];cases=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=str(chrome),headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:18982');page.locator('[data-page="analysis"]').click()
        page.wait_for_function('()=>serverSettingsLoaded');page.locator('#analysisSource').select_option('local')
        page.locator('#analysisLocalSources [data-source-action="clear"]').click()
        page.locator('#analysisSource').select_option('url');shortcut=page.locator('[data-import-folder="analysisSource"]')
        expect(shortcut).to_be_visible();phase={'status':'cancelled'}
        def picker(route):
            route.fulfill(json={'id':'qa-picker','status':'pending'} if route.request.method=='POST' else
                {'id':'qa-picker','status':phase['status'],'paths':[str(folder)] if phase['status']=='done' else [],'error':'测试选择失败'})
        page.route('**/api/pickers',picker);page.route('**/api/pickers/qa-picker',picker)
        shortcut.click();expect(page.locator('#analysisSource')).to_have_value('local')
        control=page.locator('#analysisLocalSources [data-source-action="folder"]');expect(control).to_be_enabled()
        phase['status']='error';control.click();expect(control).to_be_enabled()
        phase['status']='done';control.click();expect(page.locator('#analysisLocalSources .source-row')).to_have_count(2)
        cases+=['folder shortcut from online source','cancel releases selection button','failed selector can retry','native recursive folder discovers two videos']
        clear=page.locator('#analysisLocalSources [data-source-action="clear"]');clear.click()
        page.locator('#analysisLocalSources [data-source-recursive]').uncheck()
        page.locator('#analysisLocalSources [data-source-folder]').set_input_files(folder)
        expect(page.locator('#analysisLocalSources .source-row')).to_have_count(1)
        clear.click();page.locator('#analysisLocalSources [data-source-recursive]').check()
        page.locator('#analysisLocalSources [data-source-folder]').set_input_files(folder)
        expect(page.locator('#analysisLocalSources .source-row')).to_have_count(2)
        cases+=['browser folder respects recursion toggle','browser folder ignores text files and imports Chinese space filenames']
        result=page.evaluate('''async()=>{
          const fileEntry=name=>({isFile:true,name,file:ok=>ok({name})});
          const dirEntry=(name,children)=>({isDirectory:true,name,createReader:()=>{let done=false;return {readEntries:ok=>{if(done)ok([]);else{done=true;ok(children);}}}}});
          const entries=[dirEntry('root',[fileEntry('first.mp4'),fileEntry('readme.txt'),dirEntry('nested',[fileEntry('second.mkv')])])];
          return {flat:(await readDroppedEntries(entries,false)).map(f=>f.name),recursive:(await readDroppedEntries(entries,true)).map(f=>f.name)};
        }''')
        assert result=={'flat':['first.mp4'],'recursive':['first.mp4','second.mkv']},result
        cases+=['dragged folder traverses paged entries and honors recursion']
        page.locator('[name="analysisPurpose"][value="rules"]').check();page.locator('#analysisStart').click()
        expect(page.locator('[data-import-folder="extractMode"]')).to_be_visible()
        page.locator('[data-import-folder="extractMode"]').click();expect(page.locator('#extractMode')).to_have_value('local')
        expect(page.locator('#page-extract .source-row')).to_have_count(4)
        cases+=['rule extraction shares folder import and queue']
        assert not errors,errors
        page.screenshot(path=str(qa/'folder-import.png'),full_page=True);browser.close()
    (qa/'folder-ui-verification.json').write_text(json.dumps({'passed':True,'cases':cases,'errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
