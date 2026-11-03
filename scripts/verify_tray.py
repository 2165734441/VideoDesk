"""Run the real tray form in a fresh home and verify owned lifecycle/reuse."""
import json
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--home',type=Path)
    parser.add_argument('--exe',type=Path,default=ROOT/'release/VideoDesk-2.5.0-Windows-x64.exe');args=parser.parse_args()
    folder=Path(tempfile.mkdtemp(prefix='VideoDesk tray 验证-'));home=args.home or folder/'app home';report=folder/'tray.json';stop=folder/'stop.signal'
    exe=args.exe
    env={**os.environ,'PATH':str(Path(os.environ['SystemRoot'])/'System32')}
    proc=subprocess.Popen([str(exe),'--tray-test','--home',str(home),'--report',str(report),'--stop-file',str(stop)],env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        deadline=time.monotonic()+360
        while not report.exists():
            if proc.poll() is not None:raise RuntimeError('Tray form exited early')
            if time.monotonic()>deadline:raise RuntimeError('Tray startup timeout')
            time.sleep(.25)
        state=json.loads(report.read_text('utf-8'));assert state['success'],state
        assert state['tray_visible'] and not state['window_visible'] and not state['show_in_taskbar'],state
        assert state['version']=='2.5.0' and state['menu_items']>=6
        reused=folder/'reused.json'
        duplicate=subprocess.run([str(exe),'--background','--home',str(home),'--report',str(reused)],env=env,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        assert duplicate.returncode==0 and json.loads(reused.read_text())['reused']
        # Exercise EXE -> built-in Node -> existing shared HTTP backend.
        cli=subprocess.run([str(exe),'--home',str(home),'--cli','jobs'],env=env,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        assert cli.returncode==0
        with urlopen(state['url']+'/api/state') as r:health=json.load(r)
        request=Request(state['url']+'/api/shutdown',data=b'{}',headers={'Content-Type':'application/json','X-App-Token':health['token']})
        with urlopen(request) as r:assert json.load(r)['ok']
        assert proc.wait(timeout=20)==0
        time.sleep(.2)
        try:urlopen(state['url']+'/api/state',timeout=1)
        except OSError:pass
        else:raise AssertionError('Service remained running after tray exit')
        (ROOT/'.local/build-exe/tray-verification.json').write_text(json.dumps({**state,'duplicate_reused':True,'exe_cli':'passed','web_exit_stopped_owned_service':True,'home':str(home)},ensure_ascii=False,indent=2),encoding='utf-8')
        print('TRAY CHECKS PASSED',flush=True)
    finally:
        if proc.poll() is None:
            stop.write_text('stop');proc.wait(timeout=30)

if __name__=='__main__':main()
