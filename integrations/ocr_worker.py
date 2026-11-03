"""Disposable, CPU-only OCR subprocess. No native model is loaded in the server."""
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):
    os.environ[key]='1'

try:threads=max(1,min(4,int(os.environ.get('VIDEO_DESK_OCR_THREADS','1'))))
except ValueError:threads=1
PARAMS={'EngineConfig.onnxruntime.intra_op_num_threads':threads,
        'EngineConfig.onnxruntime.inter_op_num_threads':1,
        'EngineConfig.onnxruntime.use_cuda':False,
        'EngineConfig.onnxruntime.use_dml':False,
        'Global.log_level':'error'}


def main():
    import cv2
    cv2.setNumThreads(1)
    from rapidocr import RapidOCR
    engine=RapidOCR(params=PARAMS)
    from integrations.ocr_cache import install
    memo=install(engine)
    for line in sys.stdin:
        try:
            request=json.loads(line);found=engine(request['path'])
            result={'text':[str(x) for x in (found.txts if found.txts is not None else [])],
                'boxes':found.boxes.tolist() if found.boxes is not None else [],
                'confidence':[float(x) for x in (found.scores if found.scores is not None else [])]}
            print(json.dumps({'result':result,'cache':{'hits':memo.hits,'misses':memo.misses,'bytes':memo.bytes}},ensure_ascii=False),flush=True)
        except Exception:
            print(json.dumps({'error':'画面文字识别未完成，原尺寸图片仍保留'}),flush=True)
        time.sleep(.05)


if __name__=='__main__':main()
