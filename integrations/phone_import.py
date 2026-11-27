"""Resumable, authenticated phone replay import. No phone paths are trusted."""
import hashlib,json,re,shutil,subprocess,threading,time
from pathlib import Path
from fastapi import HTTPException
from .storage import atomic_json

CHUNK=4*1024*1024

def safe_folder(value):
    """A readable Windows folder name, never a path supplied by the phone."""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value or '')).strip().rstrip('. ')
    value = re.sub(r'\s+', ' ', value)
    return value[:60] or '未命名主播'

class Imports:
    def __init__(self,a):
        self.a=a;self.lock=threading.RLock();self.path=a.DATA/'phone-imports.json'
        self.items=a.load_json(self.path,{})

    def save(self):atomic_json(self.path,self.items)

    def anchor_name(self,v):
        key=str(v.get('anchor_id') or '')
        name=''
        try:
            anchors=getattr(getattr(self.a,'LIVE',None),'anchors',{}) or {}
            item=anchors.get(key) or {}
            name=str(item.get('name') or '')
        except Exception:
            name=''
        if not name:name=str(v.get('anchor_name') or v.get('name') or '')
        return key,name.strip()[:100],safe_folder(name or ('主播 '+key[:8] if key else '未命名主播'))

    def begin(self,v):
        rid=str(v.get('id',''));digest=str(v.get('sha256',''));size=v.get('size')
        if not re.fullmatch(r'[a-f0-9]{32}',rid) or not re.fullmatch(r'[a-f0-9]{64}',digest) or type(size)!=int or not 1024<size<=100*1024**3:
            raise HTTPException(400,'回放信息无效')
        with self.lock:
            old=self.items.get(rid)
            if old:
                if old['sha256']!=digest or old['size']!=size:raise HTTPException(409,'回放校验信息发生变化')
                if old.get('done'):
                    job=self.a.job_by_id(old['job']);media=Path(job['directory'])/'phone-replay.mp4'
                    if media.is_file() and media.stat().st_size==size:return {'offset':size,'done':True,'job':old['job']}
                    raise HTTPException(409,'电脑回放曾被删除，请在手机保留原文件')
                part=self.part(old);final=part.with_name('phone-replay.mp4');offset=part.stat().st_size if part.exists() else (final.stat().st_size if final.exists() else 0)
                if offset>size:raise HTTPException(409,'临时文件长度异常')
                return {'offset':offset,'done':False}
            raw_name=str(v.get('name') or '手机录制')[:100]
            anchor_id,anchor_name,folder_name=self.anchor_name(v)
            display=anchor_name or raw_name or '手机录制'
            # Every upload from one anchor lands under one readable folder. The
            # job's own timestamped directory below it keeps each replay
            # separate without mixing two creators in the same folder.
            # Follow the live-recording output when it has its own value; the
            # global library is the fallback. This keeps a phone replay beside
            # the live jobs of the same creator instead of in a separate tree.
            try:
                base_output = (self.a.LIVE.settings.get('output') or self.a.SETTINGS['output'])
            except Exception:
                base_output = self.a.SETTINGS['output']
            folder=self.a.output_dir(str(Path(base_output)/'手机录播'/folder_name))
            title=display+' · 手机回放'
            job=self.a.new_job('live-recording',title,str(folder),
                 options={'phone_import':True,'phone_recording_id':rid,'anchor_id':anchor_id,'anchor_name':display})
            old={'job':job['id'],'size':size,'sha256':digest,'name':display,'anchor_id':anchor_id,
                 'anchor_name':display,'folder':folder_name,'created':time.time(),'done':False,
                 'fps':int(v.get('fps') or 0),'quality':str(v.get('quality') or '')}
            self.items[rid]=old;self.save()
            self.a.change(job['id'],status='transferring',message='等待 USB 传输手机回放',progress=0)
            return {'offset':0,'done':False}

    def part(self,item):return Path(self.a.job_by_id(item['job'])['directory'])/'.phone-replay.part'

    def chunk(self,rid,offset,data):
        if not data or len(data)>CHUNK:raise HTTPException(400,'传输块大小无效')
        with self.lock:
            item=self.items.get(rid)
            if not item or item.get('done'):raise HTTPException(409,'请重新确认回放传输状态')
            part=self.part(item);current=part.stat().st_size if part.exists() else 0
            if offset!=current:raise HTTPException(409,'传输位置变化，请重新续传')
            if offset+len(data)>item['size']:raise HTTPException(400,'传输长度超出声明大小')
            if shutil.disk_usage(part.parent).free<len(data)+256*1024**2:raise HTTPException(507,'电脑磁盘剩余空间不足，请更换输出目录')
            with part.open('ab') as f:f.write(data);f.flush()
            end=offset+len(data)
            self.a.change(item['job'],status='transferring',message=f'手机回放传输 {end//1024//1024} / {item["size"]//1024//1024} MB',progress=round(end/item['size']*95,1))
            return {'offset':end}

    def complete(self,rid):
        with self.lock:
            item=self.items.get(rid)
            if not item:raise HTTPException(404,'回放传输不存在')
            if item.get('done'):return {'done':True,'job':item['job']}
            part=self.part(item)
            if not part.exists() and part.with_name('phone-replay.mp4').is_file():part=part.with_name('phone-replay.mp4')
            if not part.is_file() or part.stat().st_size!=item['size']:raise HTTPException(409,'回放尚未传输完整')
            digest=hashlib.sha256()
            with part.open('rb') as f:
                while block:=f.read(CHUNK):digest.update(block)
            if digest.hexdigest()!=item['sha256']:
                part.unlink();self.a.change(item['job'],message='校验失败，等待手机重新传输',progress=0)
                raise HTTPException(409,'回放 SHA256 校验失败，已重置传输位置')
            probe=self.a.ROOT/'runtime-tools/ffprobe.exe'
            result=subprocess.run([str(probe),'-v','error','-show_entries','format=duration:stream=codec_type,avg_frame_rate','-of','json',str(part)],capture_output=True,text=True,timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                info=json.loads(result.stdout);duration=float(info['format']['duration']);valid=duration>0 and any(s['codec_type']=='video' for s in info['streams'])
                source_fps=0.0
                for stream in info.get('streams',[]):
                    rate=str(stream.get('avg_frame_rate') or '')
                    if stream.get('codec_type')=='video' and '/' in rate:
                        num,den=rate.split('/',1)
                        if float(den or 0)>0:source_fps=float(num)/float(den)
            except (ValueError,KeyError,TypeError,ZeroDivisionError):valid=False;source_fps=0.0
            if result.returncode or not valid:raise HTTPException(422,'手机回放无法播放，原文件仍保留在手机')
            out=part.with_name('phone-replay.mp4')
            if part!=out:part.replace(out)
            segment={'name':out.name,'preview':out.name,'started':item['created'],'ended':item['created']+duration,'gap_before':False,'complete':True}
            atomic_json(out.parent/'replay.json',{'anchor':item.get('anchor_name') or item['name'],
                'anchor_id':item.get('anchor_id',''),'anchor_folder':item.get('folder',''),
                'segments':[segment],'phone_recording_id':rid,'sha256':item['sha256']})
            self.a.change(item['job'],status='done',message='手机回放已导入，SHA256 校验通过',progress=100,replay=[segment])
            atomic_json(out.parent/'manifest.json',{k:self.a.job_by_id(item['job'])[k] for k in ('id','kind','title','source','created','options','frames')})
            self.a.material_history.register_created(self.a,item['job'],[out,out.parent/'replay.json',out.parent/'manifest.json']);self.a.refresh_files(item['job'])
            item['done']=True;self.save()
            self.low_fps(item,out,item.get('name') or '手机回放',source_fps)
            return {'done':True,'job':item['job'],'sha256':item['sha256']}

    def low_fps(self,item,source,display,source_fps=0.0):
        """Queue an audio-preserving low-frame-rate copy after a verified import.

        The phone keeps the original upload; this second job is an explicit
        deliverable, so the source remains available if the conversion fails.
        """
        try:
            target=int(item.get('fps') or 0)
            if target<=0 or (source_fps and target>=source_fps-0.5):
                return
            from .workbench_extra import EnhanceRequest
            workbench=getattr(self.a,'WORKBENCH',None)
            if workbench is None:
                return
            workbench.create(EnhanceRequest(
                source_path=str(source),fps=float(target),drop_fps=True,crf=20,preset='veryfast',
                output=str(Path(source).parent/'低帧率'),
                title=f'{(display or "手机回放")} · {target}fps 低帧率'))
        except Exception as exc:
            # The verified import is already complete; never turn a secondary
            # conversion failure into a failed phone transfer.
            print(f'phone low-fps queue failed for {item.get("job")}: {exc}', flush=True)

    def state(self):
        with self.lock:return [{'id':rid,**{k:v[k] for k in ('job','name','anchor_id','anchor_name','folder','size','done') if k in v}} for rid,v in list(self.items.items())[-30:]]
