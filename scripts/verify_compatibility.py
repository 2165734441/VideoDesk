"""Exercise actual launcher code and the packaged EXE in disposable app homes.

This is process/file isolation on this host, NOT a clean Windows VM test.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
QA=Path(os.environ.get('VIDEO_DESK_QA_ROOT',str(ROOT/'.local/v25-qa/compatibility')))
CREATE=getattr(subprocess,'CREATE_NO_WINDOW',0)


def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def wait_json(path,proc,seconds=240):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        if path.is_file():return json.loads(path.read_text('utf-8-sig'))
        if proc.poll() is not None:raise RuntimeError('Process exited before report: '+str(proc.returncode))
        time.sleep(.15)
    raise TimeoutError('Report not generated: '+str(path))


def api(base,path,body=None,token=None):
    request=Request(base+path,data=None if body is None else json.dumps(body).encode(),
                    headers={'Content-Type':'application/json',**({'X-App-Token':token} if token else {})})
    with urlopen(request,timeout=15) as r:return json.load(r)


def native(stage,env,cases):
    compiler=Path(os.environ.get('SystemRoot','C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    info=QA/'BuildInfo.cs';info.write_text('internal static class BuildInfo {public const string Version="2.5.0";public const string PayloadHash="test";}')
    fake=QA/'fake.exe';harness=QA/'harness.exe'
    subprocess.run([str(compiler),'/nologo','/target:exe','/platform:x64','/out:'+str(fake),str(ROOT/'tests/CompatibilityFake.cs')],check=True)
    subprocess.run([str(compiler),'/nologo','/target:exe','/platform:x64','/codepage:65001','/main:CompatibilityHarness',
        '/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll','/reference:System.IO.Compression.dll',
        '/reference:System.IO.Compression.FileSystem.dll','/reference:System.Web.Extensions.dll','/out:'+str(harness),
        str(ROOT/'launcher/Program.cs'),str(ROOT/'launcher/Compatibility.cs'),str(ROOT/'tests/CompatibilityHarness.cs'),str(info)],check=True)
    home=QA/('native-'+str(time.time_ns()));home.mkdir();stop=home/'stop'
    with (home/'harness.log').open('w',encoding='utf-8') as log:
        proc=subprocess.Popen([str(harness),str(home),str(fake),str(stage),str(stop)],env=env,
                              stdout=log,stderr=subprocess.STDOUT,creationflags=CREATE)
        try:
            record=wait_json(home/'degraded-server.json',proc,60);base=record['url']
            state=api(base,'/api/state');token=state['token']
            assert state['runtime']['degraded']
            bad={v['id'] for v in state['runtime']['components'] if not v['ready']}
            assert bad=={'ocr','asr'},bad
            assert all(v['exit_hex']=='0xC000001D' for v in json.loads((home/'runtime-diagnostics.json').read_text())['components'] if not v['ready'])
            assert 'stdout' not in json.dumps(api(base,'/api/runtime-status'))
            components=api(base,'/api/analysis-settings')['components']
            assert not components['ocr'] and not components['asr_runtime']
            cases.append('native illegal-instruction exit preserved; real server and settings survive isolated OCR/ASR failure')
            fixture=home/'原视频.mp4';ffmpeg=next((stage/'runtime/main/Lib/site-packages/imageio_ffmpeg/binaries').glob('ffmpeg*.exe'))
            subprocess.run([str(ffmpeg),'-v','error','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=10',
                '-t','2','-c:v','libx264','-pix_fmt','yuv420p',str(fixture)],check=True,creationflags=CREATE)
            before=digest(fixture)
            jid=api(base,'/api/extract-local',{'path':str(fixture),'output':str(home/'out'),
                'frames':{'mode':'seconds','value':.5,'audio':False}},token)['id']
            for _ in range(400):
                job=next(j for j in api(base,'/api/state')['jobs'] if j['id']==jid)
                if job['status'] in ('done','error','cancelled'):break
                time.sleep(.1)
            assert job['status']=='done' and job['frames']==4,job
            assert digest(fixture)==before
            cases.append('local no-copy frame export remains usable with OCR/ASR unavailable; source hash unchanged')
            active=api(base,'/api/capture',{'title':'repair guard','output':str(home/'out')},token)['id']
            try:api(base,'/api/runtime-repair',{},token);raise AssertionError('repair interrupted a capture')
            except HTTPError as e:assert e.code==409
            try:api(base,'/api/runtime-repair',{},'wrong token');raise AssertionError('repair accepted unauthenticated request')
            except HTTPError as e:assert e.code==403
            from playwright.sync_api import sync_playwright,expect
            browser_exe=next((stage/'runtime/browsers').glob('chromium-*/chrome-win*/chrome.exe'))
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=str(browser_exe),headless=True,args=['--disable-gpu'])
                page=browser.new_page(viewport={'width':1440,'height':1050});errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(base);expect(page.locator('.runtime-warning')).to_be_visible()
                page.get_by_role('button',name='查看原因与修复',exact=True).click()
                expect(page.locator('.runtime-card')).to_be_visible()
                button=page.get_by_role('button',name='检测并修复',exact=True)
                button.click();expect(page.locator('#toast')).to_contain_text('还有任务')
                expect(button).to_be_enabled()
                expect(page.locator('#runtimeComponents')).to_contain_text('处理器')
                page.screenshot(path=str(QA/'runtime-degraded.png'),full_page=True)
                assert not errors,errors;browser.close()
            api(base,f'/api/capture/{active}/finish',{'chunks':0,'mp4':False,'audio':False},token)
            for _ in range(100):
                finished=next(j for j in api(base,'/api/state')['jobs'] if j['id']==active)
                if finished['status']=='done':break
                time.sleep(.1)
            assert finished['status']=='done',finished
            assert api(base,'/api/runtime-repair',{},token)['ok']
            stamp=(home/'data/repair.request').stat().st_mtime_ns
            assert api(base,'/api/runtime-repair',{},token)['ok']
            assert (home/'data/repair.request').stat().st_mtime_ns==stamp
            try:api(base,'/api/capture',{'title':'too late','output':str(home/'out')},token);raise AssertionError('new task while repair pending')
            except HTTPError as e:assert e.code==409
            cases.append('web warning and repair controls; active-job protection, authentication, duplicate submission and repair queue lock')
            cases.append('signed Microsoft CRT accepted, unsigned executable rejected; native timeout, orphan cleanup and required failure remain actionable')
        finally:
            stop.touch()
            try:assert proc.wait(timeout=20)==0
            except subprocess.TimeoutExpired:proc.kill();proc.wait();raise


def packaged(exe,env,cases):
    run=QA/('packaged-'+str(time.time_ns()));run.mkdir();home=run/'app home'
    def start(name,repair=False,hold=False):
        report=run/(name+'.json');stop=run/(name+'.stop')
        args=[str(exe),'--self-test','--home',str(home),'--report',str(report)]
        if repair:args.append('--repair')
        if hold:args += ['--stop-file',str(stop)]
        proc=subprocess.Popen(args,env=env,creationflags=CREATE)
        record=wait_json(report,proc)
        assert record['success'],record
        if not hold:assert proc.wait(timeout=20)==0
        return record,proc,stop
    first,proc,stop=start('first',hold=True)
    materials={}
    try:
        assert not first['degraded'],first['components']
        assert len(first['components'])==9
        stage=Path(first['install']);base=first['url']
        health=api(base,'/api/state');assert health['version']=='2.5.0'
        assert not health['runtime']['degraded']
        fixture=run/'原视频.mp4';out=run/'preserved-output';ffmpeg=next((stage/'runtime/main/Lib/site-packages/imageio_ffmpeg/binaries').glob('ffmpeg*.exe'))
        subprocess.run([str(ffmpeg),'-v','error','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=4',
            '-t','1','-c:v','libx264','-pix_fmt','yuv420p',str(fixture)],check=True,creationflags=CREATE)
        jid=api(base,'/api/extract-local',{'path':str(fixture),'output':str(out),
            'frames':{'mode':'seconds','value':.5,'audio':False}},health['token'])['id']
        for _ in range(200):
            history=next(j for j in api(base,'/api/state')['jobs'] if j['id']==jid)
            if history['status'] in ('done','error'):break
            time.sleep(.1)
        assert history['status']=='done' and history['frames']==2,history
        materials={str(p):digest(p) for p in out.rglob('*') if p.is_file()}
        materials[str(fixture)]=digest(fixture)
        # Query the actual Windows module handles: plain CRT names must resolve
        # to this EXE's app-local files rather than this developer PC's System32.
        code='''import ctypes,json,onnxruntime
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.GetModuleHandleW.argtypes=[ctypes.c_wchar_p];k.GetModuleHandleW.restype=ctypes.c_void_p
k.GetModuleFileNameW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_uint]
result={}
for name in ('MSVCP140.dll','MSVCP140_1.dll','VCRUNTIME140.dll','VCRUNTIME140_1.dll'):
 handle=k.GetModuleHandleW(name);assert handle,name
 buf=ctypes.create_unicode_buffer(32768);assert k.GetModuleFileNameW(handle,buf,32768)
 result[name]=buf.value
print(json.dumps(result))'''
        loaded=json.loads(subprocess.check_output([first['main_python'],'-c',code],env=env,text=True,encoding='utf-8'))
        assert all(Path(v).resolve().parent==Path(first['main_python']).parent.resolve() for v in loaded.values()),loaded
        (QA/'loaded-crt.json').write_text(json.dumps(loaded,ensure_ascii=False,indent=2),encoding='utf-8')
        cases.append('actual EXE fresh install: all nine components ready; Windows loader uses bundled CRT files')
    finally:
        stop.touch()
        if proc.poll() is None:assert proc.wait(timeout=20)==0
    # Simulate a quarantined optional engine file in this disposable installation.
    broken=Path(first['install'])/'runtime/main/Lib/site-packages/rapidocr/__init__.py'
    assert broken.resolve().is_relative_to(home.resolve())
    original=digest(broken);broken.write_text("raise ImportError('simulated quarantined component')\n")
    repaired,_,_=start('automatic-repair')
    assert repaired['install']!=first['install'] and not repaired['degraded']
    assert digest(Path(repaired['install'])/'runtime/main/Lib/site-packages/rapidocr/__init__.py')==original
    cases.append('actual EXE detects optional-engine damage and automatically restores all checked components')
    # These are deliberately harmless, synthetic records in the test app home.
    data=home/'data';fixtures={
        'settings.json':json.dumps({'output':str(run/'preserved-output')}),
        'analysis-settings.json':json.dumps({'asr':'local','api_key':'synthetic-preserve-only','language':'auto','base_url':'','model':''}),
        'credentials/preserve.txt':'synthetic credential marker',
        'models/preserve.txt':'synthetic model marker','browser-profile/preserve.txt':'synthetic profile marker'}
    for name,value in fixtures.items():
        path=data/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value,encoding='utf-8')
    existing=json.loads((data/'jobs.json').read_text()) if (data/'jobs.json').exists() else []
    assert existing and existing[0]['status']=='done'
    out=run/'preserved-output';out.mkdir(exist_ok=True);(out/'original.txt').write_text('original material')
    before={name:digest(data/name) for name in fixtures}
    before['jobs.json']=digest(data/'jobs.json')
    original_out=digest(out/'original.txt')
    manual,_,_=start('manual-repair',repair=True)
    assert manual['install']!=repaired['install'] and not manual['degraded']
    assert all(digest(data/name)==value for name,value in before.items())
    assert digest(out/'original.txt')==original_out
    assert all(digest(p)==value for p,value in materials.items())
    assert (json.loads((data/'jobs.json').read_text()) if (data/'jobs.json').exists() else [])==existing
    cases.append('actual EXE --repair preserves settings, synthetic credentials, model/profile markers, tasks and materials')
    reused,_,_=start('reuse')
    assert reused['install']==manual['install'] and not reused['degraded']
    cases.append('actual EXE reuses the repaired installation successfully on the next launch')
    return {'home':str(home),'install':manual['install'],'exe_sha256':digest(exe)}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--native-only',action='store_true');parser.add_argument('--package-only',action='store_true')
    parser.add_argument('--stage',type=Path,default=ROOT/'.local/build-exe/payload')
    parser.add_argument('--exe',type=Path,default=ROOT/'release/VideoDesk-2.5.0-Windows-x64.exe');args=parser.parse_args()
    QA.mkdir(exist_ok=True);env={**os.environ,'PATH':str(Path(os.environ['SystemRoot'])/'System32'),
        'PYTHONHOME':str(QA/'absent-python'),'PYTHONPATH':str(QA/'absent-modules'),
        'PSModulePath':str(QA/'absent-powershell-modules'),'PYTHONIOENCODING':'utf-8','PYTHONUTF8':'1'}
    cases=[];record={}
    if not args.package_only:native(args.stage.resolve(),env,cases)
    if not args.native_only:record=packaged(args.exe.resolve(),env,cases)
    result={'passed':True,'version':'2.5.0','isolation':'same-host isolated processes, app home and PATH; not a clean Windows VM',
            'cases':cases,**record}
    target=QA/('native-verification.json' if args.native_only else 'packaged-verification.json' if args.package_only else 'verification.json')
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
