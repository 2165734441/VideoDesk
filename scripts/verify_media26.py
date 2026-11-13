"""Actual local AI/FFmpeg smoke and timing evidence; no personal media or accounts."""
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument('--source', default='english.mp4'); args = parser.parse_args()
    data = ROOT/'.local/v26-qa'
    os.environ['VIDEO_DESK_DATA'] = str(data)
    import app as a
    source = data/'samples'/args.source
    original = a.translation.components.digest(source)
    results = []
    for device in ('cpu', 'auto'):
        req = a.translation.TranslateRequest(paths=[str(source)], output=str(data/'output'), device=device, review=False)
        job = a.new_job('translation', device+' · actual local AI', req.output, str(source), req.model_dump(exclude={'paths','output','source_job','relative'}))
        started = time.monotonic(); a.TRANSLATION.run(job['id'])
        current = a.job_by_id(job['id'])
        result = {k: current.get(k) for k in ('id','status','message','directory','timings','devices','encoder','encode_seconds','translated_video')}
        result['wall_seconds'] = round(time.monotonic()-started, 3)
        result['source_sha256'] = original
        if current['status'] != 'done':
            print(json.dumps(result,ensure_ascii=False),flush=True)
            raise AssertionError('Translation failed: '+current['message'])
        out = Path(current['directory'])/current['translated_video']
        info = a.translation.media_probe.inspect(out, str(a.ANALYSIS_ENGINE.probe))
        result['output'] = {k: info[k] for k in ('duration','width','height','codec','hasAudio')}
        result['output_sha256'] = a.translation.components.digest(out)
        assert abs(info['duration']-a.translation.media_probe.inspect(source, str(a.ANALYSIS_ENGINE.probe))['duration']) < .15
        assert a.translation.components.digest(source) == original
        results.append(result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    a.atomic_json(data/'performance-evidence.json', {'results':results,'note':'Synthetic Windows English speech, 640x360; startup included. Not representative of all media.'})


if __name__ == '__main__': main()
