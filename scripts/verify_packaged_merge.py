"""Exercise storyboard, ASR, OCR and exports through the isolated EXE runtime."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
def main():
    verified=json.loads((ROOT/'.local/build-exe/exe-verification.json').read_text('utf-8'))
    home=Path(verified['test_home'])/'app home';run=uuid.uuid4().hex[:8]
    report=home.parent/f'merge-runtime-{run}.json';stop=home.parent/f'merge-runtime-{run}.stop'
    env={**os.environ,'PATH':str(Path(os.environ['SystemRoot'])/'System32')}
    proc=subprocess.Popen([str(ROOT/'release/VideoDesk-2.3.0-Windows-x64.exe'),'--self-test','--home',str(home),'--report',str(report),'--stop-file',str(stop)],env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        for _ in range(1200):
            if report.exists():break
            if proc.poll() is not None:raise RuntimeError('Packaged runtime exited early')
            time.sleep(.2)
        state=json.loads(report.read_text('utf-8'));assert state['success'],state
        subprocess.run([sys.executable,str(ROOT/'scripts/verify_merge.py'),'--url',state['url'],'--data',str(home/'data')],check=True)
        subprocess.run([sys.executable,str(ROOT/'scripts/verify_batch.py'),'--url',state['url'],'--data',str(home/'data'),'--report',str(ROOT/'.local/build-exe/packaged-batch-verification.json')],check=True)
        env.update(VIDEO_DESK_URL=state['url'],VIDEO_DESK_TEST_VIDEO=str(ROOT/'.local/batch-qa/fixtures/中文 商品, 空格.mp4'),VIDEO_DESK_TEST_OUTPUT=str(ROOT/'.local/batch-qa/packaged-dsh-output'))
        subprocess.run([state['node'],'--test',str(ROOT/'tests/test_dsh_adapter.mjs')],env=env,check=True)
        details=json.loads((ROOT/'.local/merge-qa/verification.json').read_text('utf-8'))
        (ROOT/'.local/build-exe/packaged-merge-verification.json').write_text(json.dumps({'success':True,'runtime':state['install'],'node':state['node'],'isolated_path':env['PATH'],'checks':details},ensure_ascii=False,indent=2),encoding='utf-8')
        print('PACKAGED MERGE CHECKS PASSED',flush=True)
    finally:
        stop.write_text('stop');proc.wait(timeout=30)

if __name__=='__main__':main()
