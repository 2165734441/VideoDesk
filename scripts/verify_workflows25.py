"""Isolated browser regression for workflow migration, navigation and requests."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / '.local' / 'v25-qa'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', action='store_true')
    args = parser.parse_args()
    errors, submitted = [], []
    chrome = next((ROOT/'.local/build-exe/payload/runtime/browsers').glob('chromium-*/chrome-win64/chrome.exe'))
    old = {'outputs': {'frames': False, 'puzzles': True, 'transcript': True, 'ai': True},
           'dedup': {'enabled': False, 'mode': 'exact', 'threshold': 4},
           'segments': {'enabled': False, 'seconds': 10, 'organize': True},
           'ai_package': {'enabled': True, 'combined': True, 'screen_text': True}}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(chrome), headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.add_init_script('if(!localStorage.getItem("yingge25"))localStorage.setItem("yingge21",'+json.dumps(json.dumps({'settings': old}))+');')
        if not args.baseline:
            import sys
            sys.path.insert(0,str(ROOT))
            from integrations.workflow_settings import defaults
            state=page.request.get('http://127.0.0.1:18976/api/state').json()
            page.request.post('http://127.0.0.1:18976/api/batch-settings',headers={'X-App-Token':state['token']},data={'workflows':{k:defaults(k) for k in ('rules','board','subtitles')},'source_ids':[],'saved_at':0})
        page.goto('http://127.0.0.1:18976')
        page.wait_for_function('typeof serverSettingsLoaded!=="undefined" && serverSettingsLoaded')
        sample=page.evaluate("async path=>(await api('/api/sources/register',{method:'POST',body:{paths:[path]}})).sources[0]",str(QA/'sample.mp4'))
        page.evaluate("source=>{localSources=[source];renderLocalSources();goto('extract')}",sample)
        def submit(route):
            submitted.append(route.request.post_data_json)
            route.fulfill(json={'id': 'ui-only', 'status': 'queued'})
        page.route('**/api/batches', submit)
        page.locator('#extractBtn').click()
        expect(page.locator('#page-library')).to_be_visible()
        assert submitted, 'No batch request'
        if args.baseline:
            assert submitted[0]['outputs'].get('ai') and not submitted[0]['outputs'].get('frames')
        else:
            body = submitted[0]
            assert body['workflow']=='rules' and body['outputs']['frames'] and not body['outputs']['ai']
            assert not body['outputs']['puzzles'] and not body['outputs']['transcript'] and not body['ai_package']['enabled']
            page.locator('[data-page="analysis"]').click()
            page.locator('#analysisSource').select_option('local')
            expect(page.locator('#analysisExportChoices [data-setting="outputs.audio"]')).not_to_be_checked()
            page.locator('#analysisExportChoices [data-setting="outputs.audio"]').check()
            page.locator('#analysisStart').click()
            expect(page.locator('#page-library')).to_be_visible()
            assert submitted[-1]['workflow']=='board' and submitted[-1]['outputs']['audio']
            assert submitted[-1]['outputs']['ai'] and submitted[-1]['ai_package']['enabled']
            for _ in range(3):
                page.locator('[data-page="extract"]').click()
                expect(page.locator('#extractExportChoices [data-setting="outputs.audio"]')).not_to_be_checked()
                assert page.locator('#page-extract [data-setting="outputs.ai"]').count()==0
                page.locator('[data-page="analysis"]').click()
                expect(page.locator('#analysisExportChoices [data-setting="outputs.audio"]')).to_be_checked()
            page.reload()
            page.wait_for_function('serverSettingsLoaded')
            page.locator('[data-page="analysis"]').click()
            expect(page.locator('#analysisExportChoices [data-setting="outputs.audio"]')).to_be_checked()
            page.locator('[data-page="extract"]').click()
            expect(page.locator('#extractExportChoices [data-setting="outputs.audio"]')).not_to_be_checked()
            for host in ['#analysisLocalSources', '#extractLocalPanel .local-source-panel']:
                assert page.locator(host+' [data-source-action="folder"]').count()==1
            page.locator('[data-page="subtitles"]').click()
            expect(page.locator('#page-subtitles')).to_be_visible()
            assert page.locator('#page-subtitles #analysisStrategy').count()==0
            page.locator('[data-page="analysis"]').click()
            page.screenshot(path=str(QA/'workflow25.png'), full_page=True)
            page.wait_for_function("async()=>{const s=await api('/api/batch-settings');return s.workflows.board.outputs.audio&&!s.workflows.rules.outputs.audio&&s.sources.length===1;}")
            fresh=browser.new_page()
            fresh.goto('http://127.0.0.1:18976');fresh.wait_for_function('serverSettingsLoaded')
            fresh.locator('[data-page="analysis"]').click()
            expect(fresh.locator('#analysisExportChoices [data-setting="outputs.audio"]')).to_be_checked()
            fresh.locator('[data-page="extract"]').click()
            expect(fresh.locator('#extractExportChoices [data-setting="outputs.audio"]')).not_to_be_checked()
            exports=[]
            def export_request(route):
                exports.append(route.request.post_data_json);route.fulfill(json={'id':'export-ui-only'})
            fresh.route('**/api/exports/*',export_request)
            fresh.evaluate("document.getElementById('analysisStartTime').value='99';jobs.push({id:'export-fixture',options:{workflow:'rules',analysis:{start:1,duration:2,strategy:'compact'}}});openCommonExport('export-fixture')")
            assert fresh.locator('#commonExportDialog [data-setting="outputs.ai"]').count()==0
            assert fresh.locator('#commonExportDialog [data-setting="outputs.puzzles"]').count()==0
            expect(fresh.locator('#commonExportDialog details.advanced')).to_be_hidden()
            fresh.locator('#commonExportDialog [data-export-submit]').click()
            fresh.wait_for_function('!exportDialog.open')
            assert exports[-1]['analysis']['start']==1 and exports[-1]['analysis']['duration']==2
            assert fresh.locator('#analysisStartTime').input_value()=='99'
            fresh.evaluate("jobs.push({id:'subtitle-fixture',options:{workflow:'subtitles',analysis:{strip_step:3}}});openCommonExport('subtitle-fixture')")
            assert fresh.locator('#commonExportDialog [data-setting="outputs.audio"]').count()==0
            assert fresh.locator('#commonExportDialog [data-setting="outputs.ai"]').count()==0
            expect(fresh.locator('#commonExportDialog details.advanced')).to_be_hidden()
            fresh.close()
        assert not errors, errors
        (QA/('ui-baseline.json' if args.baseline else 'ui25.json')).write_text(json.dumps({'passed': True, 'errors': errors, 'requests': submitted}, ensure_ascii=False, indent=2), encoding='utf-8')
        browser.close()


if __name__=='__main__': main()
