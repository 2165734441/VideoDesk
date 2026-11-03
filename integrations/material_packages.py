"""Read-only package discovery and lossless file-level AI package composition."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import threading
import uuid
from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from . import ai_package
from .material_history import title_name, linked, digest, ACTIVE
from .local_sources import parse_paths

UPLOAD = ai_package.UPLOAD
EXTRA = ai_package.EXTRA
CONTAINER = '可合并资料.json'


def read_json(path, default=None):
    if not path.is_file(): return default
    try: return json.loads(path.read_text('utf-8-sig'))
    except (ValueError, OSError) as exc: raise ValueError('资料索引无法读取：'+path.name) from exc


def read_container(base):
    value=read_json(base/EXTRA/CONTAINER,{}) or {}
    if not isinstance(value,dict):raise ValueError('合并资料索引格式不正确')
    if not value:return value
    if value.get('version')!=2:raise ValueError('此资料包格式较新，请更新映格后导入')
    nodes=value.get('nodes')
    if not isinstance(nodes,list) or not nodes:raise ValueError('合并资料没有来源记录')
    for node in nodes:
        if not isinstance(node,dict) or not isinstance(node.get('title'),str) or not isinstance(node.get('files'),list) or not node['files']:
            raise ValueError('合并资料的来源记录不完整')
        if node.get('duration') is not None and (finite(node['duration']) is None or finite(node['duration'])<0):raise ValueError('合并资料的时间信息不正确')
        for f in node['files']:
            if not isinstance(f,dict) or not isinstance(f.get('name'),str) or not isinstance(f.get('path'),str):raise ValueError('合并资料的文件记录不完整')
    return value


def safe(root, name):
    name = str(name)
    if not name or Path(name).is_absolute() or '..' in Path(name).parts:
        raise ValueError('资料索引包含无效文件路径')
    path = root/name
    if not path.resolve().is_relative_to(root.resolve()) or any(linked(p) for p in (path,*path.parents)):
        raise ValueError('素材路径包含链接或超出所选文件夹')
    return path


def files_in(root):
    if not root.is_dir(): return []
    found=[]
    for current, folders, names in os.walk(root, followlinks=False):
        folders[:] = sorted(n for n in folders if not n.startswith('.') and not linked(Path(current)/n))
        for name in sorted(names):
            if not name.startswith('.'):
                path=Path(current)/name
                safe(root,path.relative_to(root).as_posix())
                if path.is_file(): found.append(path.relative_to(root).as_posix())
    return found


def issue(warning):
    text=str(warning)
    missing=any(s in text for s in ('有音轨但没有可用台词','未生成可用 AI 资料','OCR 未完成','读取失败'))
    frame=re.search(r'F\d{5,}',text)
    return {'severity':'missing_content' if missing else 'review','message':text,
            'frame':frame.group() if frame else None}


def health(folder, fallback=None):
    folder=Path(folder)
    upload=folder if folder.name==UPLOAD else folder/UPLOAD
    base=upload.parent
    report=read_json(base/EXTRA/'资料索引.json',json.loads(json.dumps(fallback or {}))) or {}
    if not isinstance(report,dict) or not isinstance(report.get('files',[]),list) or not all(isinstance(n,str) for n in report.get('files',[])):
        raise ValueError('资料索引格式不正确')
    actual=files_in(upload)
    names=list(dict.fromkeys([*report.get('files',[]),*actual]))
    container=read_container(base)
    if container:
        names=list(dict.fromkeys([*names,*container.get('generated',[]),*(f['path'] for n in container.get('nodes',[]) for f in n['files'])]))
    # A portable upload-only folder still carries its original required-file list.
    if not report.get('files') and (upload/'00_阅读说明.txt').is_file():
        reading=(upload/'00_阅读说明.txt').read_text('utf-8-sig',errors='replace')
        for line in reading.splitlines():
            if re.fullmatch(r'[^<>:"|?*]+\.(?:png|jpe?g|webp|txt|json|srt|vtt|pdf|m4a|mp3|wav)',line.strip(),re.I):
                safe(upload,line.strip());names.append(line.strip())
        names=list(dict.fromkeys(names))
    missing=[n for n in names if not safe(upload,n).is_file()]
    warnings=report.get('warnings',[])
    issues=report.get('issues') or [issue(w) for w in warnings]
    counts={'拼图':0,'原尺寸细节':0,'文本':0,'其他':0}
    for name in names:
        key='拼图' if '编号拼图' in name else '原尺寸细节' if '必要细节' in name else '文本' if name.lower().endswith(('.txt','.srt','.vtt')) else '其他'
        counts[key]+=1
    for item in issues:
        if item.get('frame'):
            matches=[n for n in actual if item['frame'] in n and '必要细节' in n]
            item['detail']=matches[0] if matches else None
    return {'files':names,'missing':missing,'count':len(names),'breakdown':counts,'issues':issues,
            'review_count':sum(i['severity']=='review' for i in issues),
            'content_missing':any(i['severity']=='missing_content' for i in issues),
            'available':bool(actual) and not missing,'file_status':'上传文件齐全' if actual and not missing else '上传文件缺失',
            'title':report.get('title',base.name),'upload':str(upload),'base':str(base)}


def finite(value):
    try:
        n=float(value)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError):return None


def nodes_from(folder, title=None):
    state=health(folder)
    if not state['available']: raise ValueError('资料文件不齐全：'+'、'.join(state['missing'][:10]) if state['missing'] else '文件夹中没有可合并的 AI 资料')
    base=Path(state['base']);upload=Path(state['upload'])
    container=read_container(base)
    if container:
        if container.get('version')!=2:raise ValueError('此资料包格式较新，请更新映格后导入')
        nodes=container['nodes']
        claimed=set()
        for node in nodes:
            for f in node['files']:
                if not safe(upload,f['path']).is_file():raise ValueError('合并来源缺文件：'+f['path'])
                claimed.add(f['path'])
        # Preserve files users added after generation as an additional opaque source.
        extras=[n for n in state['files'] if n not in claimed and n not in container.get('generated',[])]
        if extras:nodes=[*nodes,{'title':'后来添加的素材','files':[{'name':n,'path':n} for n in extras],'duration':None,'issues':[]}]
        return nodes,upload
    source=read_json(base/EXTRA/'联合内容源.json',{}) or {}
    segments=read_json(base/EXTRA/'视频顺序与时间轴.json',[]) or []
    left=finite(source.get('from'));right=finite(source.get('to'))
    duration=right-left if left is not None and right is not None and right>=left else None
    if segments:
        end=[finite(s.get('to')) for s in segments]
        if all(n is not None and n>=0 for n in end):duration=max(end,default=0)
    return [{'title':title or state['title'],'files':[{'name':n,'path':n} for n in state['files']],
             'duration':duration,'source_from':left,'source_to':right,'segments':segments,
             'source':source,'issues':state['issues'],'origin':hashlib.sha256(json.dumps(source or {'files':state['files'],'title':title or state['title']},sort_keys=True,ensure_ascii=False).encode()).hexdigest()}],upload


def merge_packages(base, entries, check=lambda:None, progress=lambda *args:None, title='联合AI分析资料'):
    """Every input upload file is copied unchanged; joint indexes never summarize it."""
    base=Path(base);upload=base/UPLOAD;upload.mkdir(parents=True)
    (base/EXTRA).mkdir()
    all_nodes=[];offset=0.0;known=True;index=[];warnings=[];seen=set();total=sum(len(n['files']) for e in entries for n in e['nodes']);done=0
    lines=['联合 AI 阅读索引：先读本文件，再读取本文件夹全部文件。',
           '各来源原文和图片完整保留；原文中的局部文件名以本索引的映射为准。',
           '原阅读说明里的“全部上传”指该来源文件集合。联合分析必须提供本文件夹全部文件。']
    for e in entries:
        check()
        for raw in e['nodes']:
            check();node=json.loads(json.dumps(raw,ensure_ascii=False));tag=f'V{len(all_nodes)+1:03d}'
            episode=e.get('episode') or node.get('episode') or len(all_nodes)+1
            ep_title=e.get('episode_title') or node.get('episode_title') or node['title']
            lines += [f'\n【第{episode:03d}集：{ep_title}】{tag} · {node["title"]}']
            duration=finite(node.get('duration'));start=offset if known else None
            end=offset+duration if known and duration is not None else None
            if start is not None and end is not None:
                lines.append(f'联合时间 {start:.3f}–{end:.3f} 秒；此分界为分析排序，不表示连续拍摄。')
                if node.get('source_from') is not None:lines.append(f'原片时间起点 {node["source_from"]:.3f} 秒；联合时间 = 原片时间 - 原片起点 + {start:.3f}。')
            else:lines.append('联合时间未知；保留本集顺序和原资料内时间，不推测时长。')
            if node.get('segments'):
                lines.append('此来源自身含有以下视频 / 集数分界：')
                for s in node['segments']:
                    a=finite(s.get('from'));b=finite(s.get('to'))
                    times=f'；联合 {start+a:.3f}–{start+b:.3f} 秒' if start is not None and a is not None and b is not None else '；联合时间未知'
                    lines.append(f'{s.get("video","")} {s.get("title","")}；原包分界 {s.get("from")}–{s.get("to")}{times}；原集数 {s.get("episode","")}')
            mapped=[];used_names=set()
            for f in node['files']:
                check();source=safe(e['upload'],f['path']);name=f'{tag}/{f["name"]}'
                if name in used_names:raise ValueError('资料索引包含重复目标文件名')
                used_names.add(name)
                target=safe(upload,name);target.parent.mkdir(parents=True,exist_ok=True)
                h=hashlib.sha256()
                with source.open('rb') as inp,target.open('wb') as out:
                    before=os.fstat(inp.fileno())
                    while chunk:=inp.read(1024*1024):check();out.write(chunk);h.update(chunk)
                    after=os.fstat(inp.fileno())
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or source.stat().st_mtime_ns!=after.st_mtime_ns:
                    raise ValueError('来源文件在合并期间发生变化，请重试：'+f['name'])
                mapped.append({'name':f['name'],'path':name,'sha256':h.hexdigest()})
                lines.append(f'文件映射：{f["name"]} → {name}')
                done+=1;progress(done,total)
            origin=node.get('origin')
            if origin and origin in seen:warnings.append(f'{tag} 疑似与前面的来源重复，已完整保留，请核对')
            seen.add(origin)
            node.update(files=mapped,episode=episode,episode_title=ep_title)
            all_nodes.append(node)
            index.append({'video':tag,'title':node['title'],'episode':{'number':episode,'title':ep_title},'from':start,'to':end,
                          'source_from':node.get('source_from'),'source_to':node.get('source_to'),'segments':node.get('segments',[])})
            if duration is None:known=False;warnings.append(f'{tag} 缺少可靠时长，未推测联合时间')
            else:offset+=duration
            warnings += [f'{tag}：{i["message"]}' for i in node.get('issues',[])]
    copied=[f['path'] for n in all_nodes for f in n['files']]
    texts=sorted(p.name for p in upload.glob('01_完整内容_*.txt'))
    names=ai_package.write_reading(upload,title,lines,copied,warnings)
    texts=sorted(p.name for p in upload.glob('01_完整内容_*.txt'))
    data=ai_package.report(base,title,names,warnings,{'videos':len(all_nodes),'duration':offset if known else None,'copied_files':len(copied)})
    (base/EXTRA/CONTAINER).write_text(json.dumps({'version':2,'nodes':all_nodes,'generated':['00_阅读说明.txt',*texts]},ensure_ascii=False,indent=2),encoding='utf-8')
    (base/EXTRA/'视频顺序与时间轴.json').write_text(json.dumps(index,ensure_ascii=False,indent=2),encoding='utf-8')
    return data


class Selection(BaseModel):
    id: str
    episode: int | None = Field(default=None,ge=1,le=10000)
    episode_title: str = Field(default='',max_length=120)


class MergeRequest(BaseModel):
    items: list[Selection] = Field(min_length=1,max_length=1000)
    title: str = Field(default='联合AI分析资料',min_length=1,max_length=120)
    output: str = ''
    request_id: str = Field(min_length=8,max_length=100)


class Materials:
    def __init__(self,a):
        self.a=a;self.lock=threading.RLock();self.path=a.DATA/'material-packages.json'
        saved=a.load_json(self.path,{})
        self.roots=saved.get('roots',{});self.requests=saved.get('requests',{});self.relocations=saved.get('relocations',{})

    def save(self):self.a.atomic_json(self.path,{'roots':self.roots,'requests':self.requests,'relocations':self.relocations})

    def register(self,root):
        root=Path(root).resolve();key=self.identity(root)
        self.roots[key]=str(root);return key

    @staticmethod
    def identity(root):return hashlib.sha256(os.path.normcase(str(Path(root).resolve())).encode()).hexdigest()[:24]

    def catalog(self):
        with self.lock:
            owners={};retired=set()
            for job in list(self.a.JOBS.values()):
                root=Path(job['directory'])
                if job.get('materials_deleted') or job.get('superseded_by'):
                    retired.add(str(root));continue
                if job.get('ai_package') or (root/UPLOAD).is_dir():
                    key=self.register(root)
                    if key in self.relocations:
                        relocated=self.relocations[key]
                        self.roots.pop(key,None);key=relocated
                    old=owners.get(key)
                    if old is None or job['status']=='done':owners[key]=job
            result=[]
            for key,path in list(self.roots.items()):
                if path in retired and key not in owners:continue
                job=owners.get(key);active=bool(job and job['status'] in ACTIVE)
                unfinished=bool(job and job['kind']=='material-merge' and job['status']!='done')
                try:state=health(path,job.get('ai_package') if job else None);error=''
                except (ValueError,OSError) as exc:state={'count':0,'available':False,'missing':[]};error=str(exc)
                result.append({'id':key,'directory':path,'title':(job or {}).get('title') or state.get('title',Path(path).name),
                               'job_id':job['id'] if job else None,'available':state['available'] and not active and not unfinished,
                               'status':'处理中' if active else '处理未完成' if unfinished else '可合并' if state['available'] else error or '文件缺失 / 目录已移动',
                               'count':state['count'],'missing':state.get('missing',[]),'episode':(job or {}).get('options',{}).get('episode'),
                               'review_count':state.get('review_count',0),'content_missing':state.get('content_missing',False)})
            return result

    def get(self,key):
        if key not in self.roots:raise HTTPException(404,'素材未登记，请重新导入')
        return Path(self.roots[key])

    def check(self,key):
        root=self.get(key)
        owner=next((j for j in self.a.JOBS.values() if self.relocations.get(self.identity(j['directory']),self.identity(j['directory']))==key and j.get('ai_package')),None)
        return health(root,owner.get('ai_package') if owner else None)

    def discover(self,paths):
        found=[];skipped=[]
        for value in paths:
            root=Path(value.strip().strip('"')).expanduser()
            if not root.is_absolute() or not root.is_dir():skipped.append({'path':str(root),'reason':'文件夹不存在'});continue
            if any(linked(p) for p in (root,*root.parents)):skipped.append({'path':str(root),'reason':'不导入链接目录'});continue
            count=0
            for current,dirs,_ in os.walk(root,followlinks=False):
                count+=1
                if count>10000:skipped.append({'path':str(root),'reason':'目录过多，请选择更具体的素材文件夹'});break
                current=Path(current)
                if current.name==UPLOAD or (current/UPLOAD).is_dir():
                    found.append(current.parent if current.name==UPLOAD else current);dirs[:]=[]
                else:dirs[:]=sorted(n for n in dirs if not n.startswith('.') and not linked(current/n) and n!=EXTRA)
        return list(dict.fromkeys(found)),skipped

    def import_folders(self,req):
        paths=req.get('paths',[])
        if isinstance(paths,str):paths=parse_paths(paths)
        if not isinstance(paths,list) or not paths or len(paths)>1000:raise HTTPException(400,'请选择素材文件夹')
        roots,skipped=self.discover(paths)
        with self.lock:
            ids=[self.register(r) for r in roots]
            old=req.get('replace_id')
            if old:
                if len(ids)!=1:raise HTTPException(400,'重新定位请选择一个资料包文件夹')
                if old not in self.roots:raise HTTPException(404,'待定位素材不存在')
                if old!=ids[0]:
                    self.roots.pop(old,None);self.relocations[old]=ids[0]
                    for key,value in list(self.relocations.items()):
                        if value==old:self.relocations[key]=ids[0]
            self.save()
        return {'materials':[m for m in self.catalog() if m['id'] in ids],'skipped':skipped}

    def create(self,req):
        signature=hashlib.sha256(req.model_dump_json(exclude={'request_id'}).encode()).hexdigest()
        with self.lock:
            if req.request_id in self.requests:
                old=self.requests[req.request_id]
                if old['signature']!=signature:raise HTTPException(409,'请求标识已用于其他设置')
                return self.a.job_by_id(old['id'])
            available={m['id']:m for m in self.catalog()};selections=list({i.id:i for i in req.items}.values())
            for s in selections:
                if s.id not in available or not available[s.id]['available']:raise HTTPException(409,'请先补齐或重新定位所选资料：'+available.get(s.id,{}).get('title',s.id))
            output=Path(req.output or self.a.SETTINGS.get('output','')).expanduser().resolve()
            for s in selections:
                if output.is_relative_to(self.get(s.id).resolve()):raise HTTPException(400,'请将合集保存在输入素材文件夹之外')
            job=self.a.new_job('material-merge',req.title,req.output,options=req.model_dump())
            self.requests[req.request_id]={'id':job['id'],'signature':signature};self.save()
            self.a.WORKERS.submit(self.a.guarded,job['id'],lambda:self.run(job['id'],req,selections))
            return job

    def run(self,jid,req,selections):
        job=self.a.job_by_id(jid);empty=Path(job['directory']);stage=empty/('.merge-'+uuid.uuid4().hex)
        try:
            entries=[]
            for s in selections:
                self.a.check_cancel(jid)
                nodes,upload=nodes_from(self.get(s.id))
                entries.append({'nodes':nodes,'upload':upload,'episode':s.episode,'episode_title':s.episode_title})
            def progress(done,total):self.a.change(jid,progress=5+int(done/max(1,total)*90),message=f'合并已有素材 {done} / {total} · 不重新解析视频')
            result=merge_packages(stage,entries,lambda:self.a.check_cancel(jid),progress,req.title)
            self.a.check_cancel(jid)
            for p in list(stage.iterdir()):p.rename(empty/p.name)
            stage.rmdir()
            target=empty.parent/title_name(req.title)
            if target.exists():target=empty.parent/(title_name(req.title)+'_'+jid[:8])
            empty.rename(target);result['title']=req.title
            self.a.change(jid,directory=str(target),ai_package=result,progress=99)
            self.a.refresh_files(jid);self.a.MATERIAL_HISTORY.record(jid)
            with self.lock:self.register(target);self.save()
            return '资料合并完成；请上传“一起上传给AI”文件夹内全部文件'
        finally:
            if stage.exists():shutil.rmtree(stage)


def install(app,a):
    service=Materials(a)
    @app.get('/api/materials/catalog')
    def catalog():return {'materials':service.catalog()}
    @app.post('/api/materials/import')
    def importing(req:dict):return service.import_folders(req)
    @app.get('/api/materials/{key}/health')
    def checking(key:str):
        try:return service.check(key)
        except (ValueError,OSError) as exc:raise HTTPException(422,str(exc))
    @app.get('/api/materials/{key}/preview/{name:path}')
    def preview(key:str,name:str):
        root=service.get(key);upload=root if root.name==UPLOAD else root/UPLOAD
        try:path=safe(upload,name)
        except ValueError as exc:raise HTTPException(400,str(exc))
        if not path.is_file():raise HTTPException(404,'文件不存在')
        return FileResponse(path)
    @app.post('/api/materials/merge')
    def merge(req:MergeRequest):return service.create(req)
    @app.post('/api/materials/merge/{jid}/retry')
    def retry(jid:str,req:dict):
        old=a.job_by_id(jid)
        if old['kind']!='material-merge' or old['status'] in ACTIVE:raise HTTPException(409,'此任务当前不能重试')
        return service.create(MergeRequest(**{**old['options'],'request_id':req.get('request_id',uuid.uuid4().hex)}))
    return service
