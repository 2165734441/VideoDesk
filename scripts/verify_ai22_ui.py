"""Headless frontend integration test, isolated browser profile and QA backend."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parents[1]


def main():
    qa=ROOT/'.local/ai22-qa';errors=[];submitted=[];exports=[]
    chrome=next((ROOT/'.local/build-exe/payload/runtime/browsers').glob('chromium-*/chrome-win64/chrome.exe'))
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=str(chrome),headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto('http://127.0.0.1:18982');page.locator('[data-page="analysis"]').click()
        page.locator('#analysisSource').select_option('local');page.locator('#analysisLocalSources [data-source-action="paste"]').click()
        paths='\n'.join('"'+str(qa/'fixtures'/n)+'"' for n in ['商品.版本2.mp4','第二个视频.mp4'])
        page.locator('#analysisLocalSources [data-source-paths]').fill(paths);page.locator('#analysisLocalSources [data-source-action="paths"]').click()
        expect(page.locator('#analysisLocalSources .source-row')).to_have_count(2)
        before=page.locator('#analysisLocalSources .source-row strong').all_text_contents()
        page.locator('#analysisLocalSources [data-move-source="1"]').first.click()
        after=page.locator('#analysisLocalSources .source-row strong').all_text_contents();assert after==list(reversed(before))
        detail=page.locator('details.advanced').filter(has=page.locator('#analysisStrategy'));detail.locator('summary').click()
        page.locator('#analysisCellsPerSheet').fill('4')
        expect(page.locator('#analysisOCR')).not_to_be_checked()
        help_button=page.locator('label').filter(has=page.locator('#analysisOCR')).locator('.option-help')
        help_button.hover();expect(page.locator('#optionHelpBubble')).to_be_visible();expect(page.locator('#optionHelpBubble')).to_contain_text('画进视频像素')
        help_button.click();expect(page.locator('#analysisOCR')).not_to_be_checked()
        help_button.focus();help_button.press('Space');expect(page.locator('#analysisOCR')).not_to_be_checked()
        count=detail.locator('input,select,textarea').count();assert count==detail.locator('.option-help').count()
        page.locator('#analysisExportChoices [data-setting="ai_package.combined"]').check()
        page.locator('#analysisTranscriptFile').fill(str(qa/'fixtures/完整.srt'))
        def submit(route):
            submitted.append(route.request.post_data_json);route.fulfill(json={'id':'ui-submission-only','status':'queued'})
        page.route('**/api/batches',submit)
        page.locator('#analysisStart').click();expect(page.locator('#page-library')).to_be_visible()
        assert submitted and submitted[0]['ai_package']['combined'] and submitted[0]['analysis']['max_cells_per_sheet']==4
        page.get_by_role('button',name='选择内容再导出',exact=True).first.click()
        expect(page.locator('#commonExportDialog')).to_be_visible()
        regen=page.locator('#commonExportDialog details').filter(has=page.locator('[data-original-id="analysisCellsPerSheet"]'))
        regen.locator('summary').click();page.locator('[data-original-id="analysisCellsPerSheet"]').fill('7')
        page.locator('#replaceGeneratedMaterials').check()
        def export(route):exports.append(route.request.post_data_json);route.fulfill(json={'id':'ui-export-only','status':'queued'})
        page.route('**/api/exports/*',export)
        page.locator('#commonExportDialog [data-export-submit]').click();expect(page.locator('#commonExportDialog')).not_to_be_visible()
        assert exports and exports[0]['analysis']['max_cells_per_sheet']==7 and exports[0]['replace_job_id']
        assert len(exports[0]['request_id'])<=100
        page.locator('[data-page="analysis"]').click()
        page.locator('#analysisStrategy').scroll_into_view_if_needed()
        page.screenshot(path=str(qa/'frontend.png'),full_page=True)
        assert not errors,errors
        (qa/'frontend-verification.json').write_text(json.dumps({'passed':True,'browser_errors':errors,'advanced_help_count':count,'cases':['reorder queue','custom sheet count reaches submission','hover explains hard subtitles','help click and keyboard do not toggle checkbox','all advanced controls have help','joint package checkbox reaches request','regeneration parameter and overwrite choice reach idempotent request']},ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()


if __name__=='__main__':main()
