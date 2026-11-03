"""Cancel inside full-sample AI packaging and verify prior stages survive retry."""
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from verify_v25 import QA, api, create, wait


def main():
    report=json.loads((QA/'acceptance25.json').read_text('utf-8'));sid=report['probe']['id']
    original=api('/api/analysis/'+report['performance'][0]['job'])
    transcript=QA/'resume-transcript.json';transcript.write_text(json.dumps(original['transcript'],ensure_ascii=False),'utf-8')
    parent=create(sid,'board','balanced',analysis={'strategy':'compact','pick':'mid','transcript_file':str(transcript),'max_cells_per_sheet':4},ai_package={'enabled':True,'screen_text':True,'scan_seconds':1})
    deadline=time.monotonic()+300;child=None
    while time.monotonic()<deadline:
        state=api('/api/batches/'+parent['id']);child=state['children'][0]
        if child['message'].startswith('读取画面文字') and child.get('progress_detail',{}).get('completed',0)>5:break
        assert child['status'] not in ('error','done','cancelled'),child
        time.sleep(.05)
    else:raise AssertionError('No OCR cancellation point')
    work=QA/'data/batch-work'/child['id']
    markers=[work/'ai-scan-stage.json',work/'part-0001/board-stage.json',*list((work/'part-0001').glob('.transcript-*.json'))]
    markers.extend(list((work/'ai-screen-scan').glob('*.png'))[:3])
    before={str(p):p.stat().st_mtime_ns for p in markers};assert len(before)>=6
    api('/api/jobs/'+child['id']+'/cancel',{})
    for _ in range(200):
        state=api('/api/batches/'+parent['id'])
        if state['children'][0]['status']=='cancelled':break
        time.sleep(.1)
    assert state['children'][0]['status']=='cancelled',state
    # A cancelled job must stay stopped until the explicit retry.
    time.sleep(.5);assert api('/api/batches/'+parent['id'])['children'][0]['status']=='cancelled'
    api('/api/batches/'+child['id']+'/retry',{})
    result,stats=wait(parent)
    after={str(p):p.stat().st_mtime_ns for p in markers}
    assert before==after,(before,after)
    final=result['children'][0];assert final['ai_package']['metrics']['unique_frames']>0
    (QA/'resume25.json').write_text(json.dumps({'passed':True,'job':child['id'],'reused_stage_files':len(before),'cancelled_without_auto_restart':True,'required_files':len(final['ai_package']['files']),**stats},ensure_ascii=False,indent=2),'utf-8')


if __name__=='__main__':main()
