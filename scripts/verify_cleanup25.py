"""Exercise batch cleanup UI and native recycle with disposable generated files."""
import hashlib
import json
from pathlib import Path
import sys
import time
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from verify_v25 import QA, api, create, wait


def main():
    sample=QA/'sample.mp4';original=hashlib.sha256(sample.read_bytes()).hexdigest()
    sid=json.loads((QA/'acceptance25.json').read_text('utf-8'))['probe']['id']
    parent=create(sid,frames={'mode':'seconds','value':10})
    result,_=wait(parent);child=result['children'][0];folder=Path(child['directory'])
    files=sorted((folder/'frames').glob('*.jpg'));assert len(files)==2
    changed=files[0];changed.write_bytes(changed.read_bytes()+b'QA user edit')
    personal=folder/'personal.txt';personal.write_text('QA user-created file','utf-8')
    ids=[parent['id'],child['id']];started=time.monotonic()
    preview=api('/api/materials/cleanup-preview',{'ids':ids});seconds=time.monotonic()-started
    assert len(preview['files'])==1,preview
    chrome=next((ROOT/'.local/build-exe/payload/runtime/browsers').glob('chromium-*/chrome-win64/chrome.exe'))
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=str(chrome),headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto('http://127.0.0.1:18976');page.wait_for_function('serverSettingsLoaded')
        page.locator('[data-page="library"]').click()
        for jid in ids:page.locator('[data-clean-select="'+jid+'"]').check()
        page.locator('#batchCleanup').click()
        expect(page.locator('[data-clean-note]')).to_contain_text('1 个生成文件',timeout=60000)
        page.screenshot(path=str(QA/'cleanup25-preview.png'),full_page=True)
        page.locator('[data-clean-submit]').click();page.wait_for_function('!cleanDialog.open',timeout=60000)
        for jid in ids:expect(page.locator('#jobs [data-id="'+jid+'"]')).to_have_count(0)
        page.locator('#showCleaned').check()
        assert page.locator('#jobs [data-id="'+parent['id']+'"]').count()>0
        assert not errors,errors
        browser.close()
    assert changed.exists() and personal.exists() and not files[1].exists()
    assert hashlib.sha256(sample.read_bytes()).hexdigest()==original
    state=api('/api/batches/'+parent['id'])
    assert state['job']['materials_deleted'] and state['children'][0]['materials_deleted']
    (QA/'cleanup25.json').write_text(json.dumps({'passed':True,'parent_child_deduplicated':True,
        'native_recycled_files':1,'user_edit_and_new_file_preserved':True,'original_sha256_unchanged':True,
        'hidden_after_cleanup':True,'show_cleaned_restores_rows':True,'preview_skips':len(preview['skipped']),
        'preview_seconds':round(seconds,3),'errors':errors},indent=2),'utf-8')
    print('BATCH CLEANUP UI AND NATIVE RECYCLE PASSED',flush=True)


if __name__=='__main__':main()
