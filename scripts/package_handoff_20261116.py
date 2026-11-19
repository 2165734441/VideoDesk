"""Create the 2026-11-16 source handoff; never copy runtime/user directories."""
from __future__ import annotations

import ast
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'handoff/2026-11-16'
DEST = ROOT / 'release/VideoDesk-2.8.2-AI-Handoff-20261116.zip'
SUFFIXES = {'.py','.java','.mjs','.js','.ts','.html','.css','.svg','.json','.txt','.md',
            '.cs','.manifest','.xml','.ps1','.cmd','.bat','.lock','.toml','.ini','.cfg','.yml','.yaml','.csv'}
FORBIDDEN = {'.local','.venv','.git','node_modules','__pycache__','.pytest_cache',
             'output','repro','screenshots','credentials','browser-profile','models'}
ROOT_FILES = ['.gitignore','.gitattributes','LICENSE','NOTICE','THIRD_PARTY_NOTICES.md',
              'README.md','SHARE-README.txt','EXE-README.txt','app.py','version.json',
              'requirements.txt','requirements-lock.txt','setup.ps1','install.cmd',
              'start.ps1','start.cmd','stop.ps1','stop.cmd','AI-HANDOFF-20261116.md',
              'BUGFIX282-TEST-REPORT.md']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load(path, fallback=None):
    try:
        return json.loads(path.read_text('utf-8-sig'))
    except (OSError, ValueError):
        return fallback


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode('utf-8')


def snapshot():
    formal = Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'AppData/Local'))) / 'VideoDesk/data'
    result = {'collected_at':datetime.now().astimezone().isoformat(),
              'source_version':load(ROOT/'version.json'),
              'scope':'Read-only on-disk aggregates; no live phone/recording verification',
              'contains_names_urls_ids_tokens_or_file_paths':False,
              'locations':{}}
    for label, folder in [('installed_user_data', formal),('source_default_data', ROOT/'.local')]:
        data = {'exists':folder.is_dir()}
        live = load(folder/'live-settings.json')
        if isinstance(live,dict):
            anchors = [a for a in live.get('anchors',[]) if isinstance(a,dict)]
            data['anchors'] = {'total':len(anchors),
                'enabled':sum(a.get('enabled') is True for a in anchors),
                'monitor_device':dict(Counter(str(a.get('monitor_device','missing')) for a in anchors)),
                'record_target':dict(Counter(str(a.get('record_target','missing')) for a in anchors)),
                'with_time_windows':sum(bool(a.get('windows')) for a in anchors)}
            settings=live.get('settings',{})
            if isinstance(settings,dict):
                data['live_settings']={k:settings[k] for k in ('concurrency','segment_minutes','follow_global') if k in settings}
        jobs = load(folder/'jobs.json')
        if isinstance(jobs,dict): jobs = list(jobs.values())
        if isinstance(jobs,list):
            jobs=[j for j in jobs if isinstance(j,dict)]
            data['jobs']={'total':len(jobs),
                'kind_and_status':dict(Counter(str(j.get('kind','missing'))+' / '+str(j.get('status','missing')) for j in jobs)),
                'live_with_replay':sum(j.get('kind')=='live-recording' and bool(j.get('replay')) for j in jobs)}
        result['locations'][label]=data
    (DOCS/'04_本机状态摘要.json').write_bytes(encode(result))
    return result


def private_values():
    values=set()
    names={'key','token','secret','password','api_key','apikey','pairing_key','access_token','refresh_token','session_token'}
    def visit(obj):
        if isinstance(obj,dict):
            for key,value in obj.items():
                if key.lower() in names and isinstance(value,str) and len(value)>=20:
                    values.add(value.encode())
                visit(value)
        elif isinstance(obj,list):
            for value in obj:visit(value)
    formal=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'VideoDesk/data'
    for folder in [ROOT/'.local/credentials',formal/'credentials']:
        if folder.is_dir():
            for path in folder.glob('*.json'):visit(load(path))
    password=ROOT/'.local/android-keypass.txt'
    if password.is_file():
        value=password.read_text('utf-8').strip()
        if len(value)>=20:values.add(value.encode())
    return values


def main():
    DOCS.mkdir(parents=True,exist_ok=True)
    snapshot()
    entries={}
    def add(path):
        relative=path.relative_to(ROOT).as_posix()
        if not path.is_file() or path.is_symlink():raise RuntimeError('Missing or linked source: '+relative)
        resolved=path.resolve()
        if not resolved.is_relative_to(ROOT.resolve()):raise RuntimeError('Source outside root: '+relative)
        if set(path.relative_to(ROOT).parts)&FORBIDDEN:raise RuntimeError('Forbidden source path: '+relative)
        entries['项目源码/'+relative]=path.read_bytes()
    for relative in ROOT_FILES:add(ROOT/relative)
    for relative in ['integrations','static','launcher','scripts','bin','dsh-plugin','docs','tests',
                     'mobile/src','mobile/assets','third_party/dsh-video-storyboard']:
        folder=ROOT/relative
        for base,dirs,files in os.walk(folder,followlinks=False):
            dirs[:]=[d for d in dirs if d not in FORBIDDEN and not d.startswith('.') and not (Path(base)/d).is_symlink()]
            for name in files:
                path=Path(base)/name
                if path.suffix.lower() in SUFFIXES or name in {'LICENSE','NOTICE','.gitignore','.gitattributes'}:
                    if name.startswith('.env') or name.lower().startswith('cookies'):raise RuntimeError('Unexpected private filename')
                    add(path)
    for path in (ROOT/'third_party').iterdir():
        if path.is_file() and (path.suffix.lower() in SUFFIXES or path.name in {'LICENSE','NOTICE'}):add(path)
    for path in (ROOT/'runtime-tools').iterdir():
        if path.is_file() and (path.suffix.lower() in {'.txt','.json','.md'} or path.name=='LICENSE'):add(path)
    for relative in ['mobile/AndroidManifest.xml','mobile/VideoDesk-Mobile.apk','static/icon.ico','static/icon.png']:
        add(ROOT/relative)
    add(ROOT/'handoff/README.md')
    for path in DOCS.iterdir():
        if path.is_file():add(path)
    for relative in ['release/VideoDesk-2.8.2-acceptance.json','release/VideoDesk-2.8.2-build.json']:
        entries['历史证据_不代表当前验收/'+Path(relative).name]=(ROOT/relative).read_bytes()

    # Check that no product source listed in the current release was missed.
    tree=ast.parse((ROOT/'scripts/build_windows_exe.py').read_text('utf-8-sig'))
    app_files=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='APP_FILES' for t in n.targets))
    for relative in app_files:
        if relative!='runtime-tools/ffprobe.exe' and '项目源码/'+relative not in entries:
            path=ROOT/relative
            if path.suffix.lower() not in SUFFIXES and path.name not in {'LICENSE','NOTICE'}:
                raise RuntimeError('Unexpected unreviewed release file: '+relative)
            add(path)
    missing=[p for p in app_files if p!='runtime-tools/ffprobe.exe' and '项目源码/'+p not in entries]
    if missing:raise RuntimeError('Missing release app files: '+str(missing))

    entries['00_先读.md']='''# VideoDesk 源码交接：当前用户验收未通过

这是 2026-11-16 的交接包，代码基线 PC 2.8.2 / Android 1.1.1，用户反馈录不了直播、漏录开播、手机按钮难用，当前尚未解决验收。

请把 `发给下一位AI.txt` 作为任务发给接手 AI，并让它从 `项目源码/AI-HANDOFF-20261116.md` 开始阅读。全部开发源码在 `项目源码/`；历史证据只供排查，不能当成最新验收。用户资料、账号和大型运行库未随包提供。

同机直接使用 E:\\VideoDesk，换机在干净环境按运行说明准备依赖。MANIFEST.json 和 SHA256SUMS.txt 可核对包内完整性。本次仅制作交接，没有新修复、实机操作或发布。
'''.encode('utf-8')
    entries['发给下一位AI.txt']=(DOCS/'03_发给下一位AI.txt').read_bytes()
    entries['HANDOFF-STATUS.json']=(DOCS/'HANDOFF-STATUS.json').read_bytes()
    for relative in ['launcher/app.manifest','launcher/Program.cs','launcher/Compatibility.cs',
                     'mobile/AndroidManifest.xml','mobile/src/org/videodesk/mobile/MainActivity.java',
                     'mobile/src/org/videodesk/mobile/StreamService.java','scripts/build_android.py',
                     'scripts/build_windows_exe.py','integrations/live.py','integrations/mobile.py']:
        assert '项目源码/'+relative in entries,relative

    secrets=private_values()
    patterns=[rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',rb'\bgh[pousr]_[A-Za-z0-9]{30,}\b',rb'\bAKIA[A-Z0-9]{16}\b']
    for name,data in entries.items():
        if any(value in data for value in secrets):raise RuntimeError('Known private value detected in '+name)
        if not name.endswith(('.apk','.ico','.png')) and any(re.search(pattern,data) for pattern in patterns):
            raise RuntimeError('Potential private credential found in '+name)

    manifest={'format':'VideoDesk developer source handoff','date':'2026-11-16','source_version':'2.8.2',
              'current_user_acceptance':'FAILED','new_product_fix_in_this_handoff':False,
              'excluded':['runtime libraries','models','user jobs/settings','cookies','browser profiles','private videos',
                          'signing key and password','raw private QA logs','old handoff documents'],
              'release_app_files_covered':len(app_files)-1,
              'intentional_release_file_omission':'runtime-tools/ffprobe.exe (fetch through source setup)',
              'privacy_check':'Allowlisted paths, known local credential strings, private-key/GitHub/AWS patterns; no claim of exhaustive secret detection',
              'files':{name:{'bytes':len(data),'sha256':digest(data)} for name,data in sorted(entries.items())}}
    entries['MANIFEST.json']=encode(manifest)
    entries['SHA256SUMS.txt']=''.join(digest(data)+'  '+name+'\n' for name,data in sorted(entries.items())).encode('utf-8')
    DEST.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(DEST,'w',zipfile.ZIP_DEFLATED,compresslevel=7) as archive:
        for name,data in sorted(entries.items()):archive.writestr(name,data)
    with zipfile.ZipFile(DEST) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist())==len(entries)==len(set(archive.namelist()))
        for name,data in entries.items():assert archive.read(name)==data,name
    sha=digest(DEST.read_bytes())
    DEST.with_suffix('.zip.sha256').write_text(sha+'  '+DEST.name+'\n',encoding='ascii')
    receipt={'archive':DEST.name,'bytes':DEST.stat().st_size,'entries':len(entries),'sha256':sha,
             'zip_crc_passed':True,'all_entry_hashes_passed':True,'release_app_files_covered':len(app_files)-1,
             'privacy_screen_passed':True,'excluded_runtime_user_data_signing_keys':True,
             'current_user_acceptance':'FAILED','scope':'handoff only; no new recording test or software fix'}
    DEST.with_suffix('.verification.json').write_bytes(encode(receipt))
    print(json.dumps(receipt,ensure_ascii=False))


if __name__=='__main__':main()
