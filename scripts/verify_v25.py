"""Real local sample acceptance; only writes beneath .local/v25-qa."""
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import Request, urlopen
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
QA=ROOT/'.local/v25-qa'
BASE='http://127.0.0.1:18976'
TOKEN=json.load(urlopen(BASE+'/api/state'))['token']


def api(path,body=None):
    with urlopen(Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json','X-App-Token':TOKEN}),timeout=60) as response:return json.load(response)


class Entry(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('usage',wintypes.DWORD),('pid',wintypes.DWORD),('heap',ctypes.c_void_p),
        ('module',wintypes.DWORD),('threads',wintypes.DWORD),('parent',wintypes.DWORD),('priority',ctypes.c_long),
        ('flags',wintypes.DWORD),('name',wintypes.WCHAR*260)]


def tree_memory(root):
    k=ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    k.OpenProcess.restype=wintypes.HANDLE
    k.CloseHandle.argtypes=[wintypes.HANDLE]
    snapshot=k.CreateToolhelp32Snapshot(2,0)
    e=Entry();e.size=ctypes.sizeof(e);parents={}
    ok=k.Process32FirstW(wintypes.HANDLE(snapshot),ctypes.byref(e))
    while ok:
        parents[e.pid]=e.parent;ok=k.Process32NextW(wintypes.HANDLE(snapshot),ctypes.byref(e))
    k.CloseHandle(snapshot)
    ids={root}
    for _ in range(8):ids.update(p for p,parent in parents.items() if parent in ids)
    total=0
    class Memory(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('faults',wintypes.DWORD),*[(n,ctypes.c_size_t) for n in ('peak','working','paged_peak','paged','nonpaged_peak','nonpaged','page','page_peak')]]
    for pid in ids:
        handle=k.OpenProcess(0x410,False,pid)
        if handle:
            counter=Memory();counter.size=ctypes.sizeof(counter)
            if ctypes.windll.psapi.GetProcessMemoryInfo(wintypes.HANDLE(handle),ctypes.byref(counter),counter.size):total+=counter.working
            k.CloseHandle(handle)
    return total


def wait(parent,timeout=1800):
    start=time.monotonic();peak=0;previous='';root=int((QA/'server.pid').read_text('utf-8-sig'))
    while time.monotonic()-start<timeout:
        result=api('/api/batches/'+parent['id']);job=result['job']
        peak=max(peak,tree_memory(root))
        children=result['children'];message=children[0].get('message','') if children else job['message']
        # Keep reports compact; show a changing processing stage every 20 sec.
        phase=message.split(' · ')[0].split(' / ')[0]
        if time.monotonic()-start>20 and (not previous or int(time.monotonic()-start)%30==0):
            print(json.dumps({'elapsed':round(time.monotonic()-start),'stage':phase,'peak_mib':round(peak/1048576)},ensure_ascii=False),flush=True);previous=phase
        if job['status'] not in ('processing','queued','running'):
            assert job['status']=='done',result
            return result,{'seconds':round(time.monotonic()-start,3),'peak_tree_mib':round(peak/1048576,2)}
        time.sleep(1)
    raise AssertionError('Task timeout '+parent['id'])


def create(sid,kind='rules',profile='auto',**extra):
    return api('/api/batches',{'sources':[sid],'output':str(QA/'output'),'kind':kind,'workflow':kind,
        'outputs':{'frames':kind=='rules','puzzles':kind!='rules','transcript':kind=='board','ai':kind=='board'},
        'performance':profile,'frames':{'mode':'seconds','value':3},'request_id':uuid.uuid4().hex,**extra})


def main():
    report={'sample_sha256':hashlib.sha256((QA/'sample.mp4').read_bytes()).hexdigest(),'cases':[],'performance':[]}
    assert report['sample_sha256']=='6d7f450619c9534c65a7bb74583f8e2bb39ceabacfbdb63dc24933dd16418b67'
    sample=api('/api/sources/register',{'paths':[str(QA/'sample.mp4')]})['sources'][0]
    for _ in range(100):
        sample=next(s for s in api('/api/sources')['sources'] if s['id']==sample['id'])
        if sample['status']!='validating':break
        time.sleep(.2)
    assert sample['status']=='ready',sample
    report['probe']=sample
    for profile in ('auto','low','balanced','high'):
        result,stats=wait(create(sample['id'],profile=profile));child=result['children'][0]
        names=[f['name'] for f in child['files']]
        assert len(names)==7 and all(n.endswith('.jpg') for n in names),names
        work=QA/'data/batch-work'/child['id']
        assert not (work/'part-0001/.speech').exists() and not (work/'part-0001/board').exists() and not (work/'ai-screen-scan').exists()
        report['cases'].append({'case':'sample rules '+profile,'job':child['id'],'images':len(names),'resources':child['resources'],**stats})
    subtitle,_=wait(create(sample['id'],'subtitles',analysis={'transcript':'off','ocr':False,'strip_step':3,'strip_count':24}))
    assert all('subtitle-strip.jpg' in f['name'] for f in subtitle['children'][0]['files'])
    report['cases'].append({'case':'subtitle region screenshots without ASR/OCR','job':subtitle['children'][0]['id']})
    (QA/'acceptance25.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    expected=None
    # Measure one uncached full pass, then compare all four profiles against
    # the same warm OCR cache. Do not misattribute cache reuse to extra threads.
    for trial,profile in enumerate(('low','low','balanced','high','auto')):
        cache=QA/'data/ocr-cache-v1'
        if trial==0 and cache.exists():
            destination=QA/'data'/('ocr-cache-before-'+profile+'-'+uuid.uuid4().hex[:6])
            assert cache.resolve().is_relative_to(QA.resolve()) and destination.resolve().is_relative_to(QA.resolve())
            cache.rename(destination)
        result,stats=wait(create(sample['id'],'board',profile,analysis={'strategy':'compact','pick':'mid','transcript':'auto','max_cells_per_sheet':4},ai_package={'enabled':True,'screen_text':True,'scan_seconds':1}))
        child=result['children'][0];base=Path(child['directory']);upload=base/'一起上传给AI'
        reading=api('/api/analysis/'+child['id'])
        assert reading['transcript'],reading['transcript_info']
        package=json.loads((base/'ai-package.json').read_text('utf-8')) if (base/'ai-package.json').exists() else child['ai_package']
        signature={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in upload.iterdir() if p.is_file()}
        if expected is None:expected=signature
        identical=signature==expected
        item={'profile':profile,'cache':'cold' if trial==0 else 'warm','job':child['id'],'resources':child['resources'],'required_files':len(signature),'upload_sha256':signature,
            'identical_to_low':identical,'transcript_rows':len(reading['transcript']),'package':package,**stats}
        report['performance'].append(item)
        (QA/'acceptance25.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
        print(json.dumps({k:v for k,v in item.items() if k not in ('upload_sha256','package')},ensure_ascii=False),flush=True)
        assert identical,'Profile content differs; inspect required file hashes'
    report['passed']=True
    (QA/'acceptance25.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')


if __name__=='__main__':main()
