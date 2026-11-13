"""Real Chromium DOM, API and preview checks on isolated local data."""
import json
from pathlib import Path
import sys
import time
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    url = sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8876'
    output = ROOT/'.local/v26-qa/screenshots';output.mkdir(exist_ok=True)
    browser_exe=next((ROOT/'.local/build-exe/payload/runtime/browsers').glob('chromium-*/chrome-win*/chrome.exe'))
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=str(browser_exe),headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1050},device_scale_factor=1)
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url,wait_until='networkidle')
        page.locator('[data-page="live"]').click()
        page.wait_for_function("document.querySelector('#liveConcurrency').value==='4'")
        name='UI 测试主播 '+str(time.time_ns())[-8:]
        page.locator('#liveName').fill(name)
        page.locator('#liveUrls').fill('https://live.douyin.com/'+str(time.time_ns()))
        page.locator('#liveTimed').check()
        page.locator('#liveFrom').fill('22:00');page.locator('#liveTo').fill('02:00')
        page.locator('#liveSave').click()
        page.wait_for_function("name=>document.querySelector('#liveAnchors').textContent.includes(name)",arg=name)
        record=page.evaluate("async name=>(await (await fetch('/api/live')).json()).anchors.find(a=>a.name===name)",name)
        assert record['windows'][0]['start']=='22:00' and record['windows'][0]['end']=='02:00'
        page.screenshot(path=str(output/'live-desktop.png'),full_page=True)
        page.locator('[data-page="translate"]').click()
        page.wait_for_function("document.querySelector('#translateComponents').textContent.includes('已就绪')")
        values=page.locator('#translateJobSelect option').evaluate_all('(items)=>items.filter(i=>i.value).map(i=>i.value)')
        assert values,'Expected completed local AI sample'
        page.locator('#translateJobSelect').select_option(values[0])
        page.locator('#translateLoadRows').click()
        page.wait_for_selector('#translateEditor:not([hidden]) textarea')
        assert page.locator('[data-language="zh"]').first.input_value()
        assert page.locator('#translateVideo').get_attribute('src')
        page.locator('#translateVideo').evaluate('(v)=>v.load()')
        page.wait_for_function("document.querySelector('#translateVideo').readyState>=1")
        assert page.locator('#translateVideo').evaluate('(v)=>v.duration')>0
        page.locator('#translateVideo').evaluate('(v)=>{v.muted=true;return v.play();}')
        page.wait_for_function("document.querySelector('#translateVideo').readyState>=2")
        page.locator('#translateVideo').evaluate('(v)=>{v.pause();v.currentTime=0.7;}')
        page.wait_for_timeout(400)
        page.screenshot(path=str(output/'translate-desktop.png'),full_page=True)
        page.set_viewport_size({'width':800,'height':1000})
        page.screenshot(path=str(output/'translate-narrow.png'),full_page=True)
        assert not errors,errors
        browser.close()
    result={'url':url,'errors':errors,'checks':['live anchor save and cross-midnight schedule','translation model status','Chinese subtitle editor','real MP4 metadata playback','desktop and narrow layout']}
    (output/'ui-evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
