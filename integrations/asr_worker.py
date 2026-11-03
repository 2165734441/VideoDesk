"""One-shot local SenseVoice ASR. Loads its model only while a job needs it."""
import json
from pathlib import Path
import re
import sys
import wave


def transcribe(req):
    if req.get('service'):return transcribe_service(req)
    import numpy as np
    import sherpa_onnx
    folder = Path(req['model'])
    recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(folder/'model.int8.onnx'), tokens=str(folder/'tokens.txt'),
        num_threads=max(1,min(4,int(req.get('threads',2)))), use_itn=True, language=req.get('language', 'auto'), debug=False)
    with wave.open(req['audio'], 'rb') as wav:
        if wav.getsampwidth()!=2 or wav.getnchannels()!=1 or wav.getframerate()!=16000:
            raise ValueError('ASR requires 16kHz mono PCM16')
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16).astype(np.float32)/32768
    # Energy gating avoids transcribing silence/music-only padding. Keep short
    # contextual tails and bound chunks so one transcript cannot span minutes.
    step = 480
    energy = [float(np.sqrt(np.mean(samples[i:i+step]**2))) for i in range(0,len(samples),step)]
    floor = max(.003, min(.02, float(np.percentile(energy,20))*2.5)) if energy else .003
    spans, start, last = [], None, 0
    for i,value in enumerate(energy):
        if value>floor:
            if start is None: start=max(0,i-5)
            last=i
        if start is not None and (i-last>16 or i-start>=500 or i==len(energy)-1):
            end=min(len(energy),last+6)
            if end-start>=5: spans.append((start*step,end*step))
            start=None
    result=[]
    for left,right in spans:
        stream=recognizer.create_stream()
        stream.accept_waveform(16000,samples[left:right])
        recognizer.decode_stream(stream)
        text=re.sub(r'<\|[^|]*\|>','',stream.result.text).strip()
        for wrong,correct in req.get('terms',{}).items():
            if wrong: text=text.replace(wrong,str(correct))
        if text:
            result.append({'from':round(left/16000+req.get('offset',0),3),
                           'to':round(min(right,len(samples))/16000+req.get('offset',0),3),'text':text})
        print(json.dumps({'progress':len(result),'chunks':len(spans)}),flush=True)
    return {'segments':result,'source':'asr','model':'SenseVoice-int8',
            'timing_method':'energy-bounded utterance windows; approximate utterance timing'}


def transcribe_service(req):
    import requests
    settings=req['service']
    with open(req['audio'],'rb') as f:
        response=requests.post(settings['base_url'].rstrip('/')+'/audio/transcriptions',
            headers={'Authorization':'Bearer '+settings['api_key']} if settings.get('api_key') else {},
            files={'file':('speech.wav',f,'audio/wav')},
            data={'model':settings.get('model',''),'response_format':'verbose_json','prompt':req.get('hotwords','')},timeout=(20,180))
    if not response.ok:raise ValueError(f'ASR 服务返回 HTTP {response.status_code}，请检查服务配置')
    payload=response.json()
    if not payload.get('segments') and payload.get('text'):raise ValueError('ASR 服务未返回分段时间码，无法可靠对齐画面；请使用支持 verbose_json 的服务')
    segments=[]
    for s in payload.get('segments',[]):
        text=str(s['text'])
        for wrong,correct in req.get('terms',{}).items():
            if wrong:text=text.replace(wrong,correct)
        segments.append({'from':float(s['start'])+req['offset'],'to':float(s['end'])+req['offset'],'text':text})
    return {'source':'asr','model':settings.get('model'),'segments':segments}


if __name__=='__main__':
    try:
        request=json.loads(sys.stdin.readline())
        print(json.dumps({'result':transcribe(request)},ensure_ascii=False),flush=True)
    except Exception as exc:
        print(json.dumps({'error':str(exc)[:1000]},ensure_ascii=False),flush=True)
        sys.exit(1)
