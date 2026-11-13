"""Ownership records for generated materials; never follow moved files or links."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import threading
from fastapi import HTTPException
from .recycle import recycle

VIDEO_EXTENSIONS={'.mp4','.mkv','.mov','.webm','.avi','.flv','.ts','.m4v','.m2ts'}
ACTIVE={'queued','running','recording','processing','waiting','waiting_source'}


def title_name(value):
    name=str(value).strip()
    if Path(name).suffix.lower() in VIDEO_EXTENSIONS:name=name[:-(len(Path(name).suffix))]
    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',name).strip(' .')[:100].rstrip(' .') or '视频'
    if name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:name='_'+name
    return name


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while chunk:=f.read(1024*1024):h.update(chunk)
    return h.hexdigest()


def linked(path):
    return path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction())


class History:
    def __init__(self,a):
        self.a=a;self.lock=threading.RLock();self.path=a.DATA/'material-history.json'
        self.records=a.load_json(self.path,{})

    def safe(self,root,relative):
        path=root/relative
        if not root.is_absolute() or relative.startswith(('/', '\\')) or '..' in Path(relative).parts:return None
        if not path.resolve().is_relative_to(root.resolve()):return None
        if any(linked(p) for p in (path,*path.parents)):return None
        return path

    def protected(self,path):
        # Any registered source is protected, including an input inside an old output.
        for item in self.a.JOBS.values():
            source=item.get('source','')
            if source and not source.startswith(('http:','https:')) and Path(source).is_absolute() and Path(source).resolve()==path.resolve():return True
        sources=getattr(self.a,'LOCAL_SOURCES',None)
        if sources:
            for item in sources.items.values():
                if item.get('path') and Path(item['path']).resolve()==path.resolve():return True
        return path.suffix.lower() in VIDEO_EXTENSIONS and 'clips' not in path.parts

    def record(self,jid):
        job=self.a.job_by_id(jid);root=Path(job['directory'])
        if job.get('materials_deleted') or job.get('superseded_by'):return
        with self.lock:
            # Directory listings are display data, never proof of authorship.
            # Do not refresh hashes here: a user may have edited a generated file.
            previous=self.records.get(jid,{})
            files=dict(previous.get('files',{})) if previous.get('ownership')=='explicit-v1' else {}
            for child_id in job.get('children',[]):
                child=self.records.get(child_id,{})
                if child.get('ownership')!='explicit-v1':continue
                child_root=Path(child['root'])
                if not child_root.is_relative_to(root):continue
                for name,meta in child['files'].items():files[(child_root/name).relative_to(root).as_posix()]=dict(meta)
            self.records[jid]={'root':str(root),'files':files,'ownership':'explicit-v1'}
            self.a.atomic_json(self.path,self.records)

    def created(self,jid,paths):
        """Called by writers for exact outputs, not by a scan of the result tree.

        A mapping may supply hashes captured in private staging before publication.
        Such hashes are not replaced if a concurrent user edit changed the target.
        """
        job=self.a.job_by_id(jid);root=Path(job['directory'])
        with self.lock:
            old=self.records.get(jid,{})
            files=dict(old.get('files',{})) if old.get('ownership')=='explicit-v1' else {}
            entries=paths.items() if isinstance(paths,dict) else ((p,None) for p in paths)
            for value,expected in entries:
                path=Path(value)
                try:relative=path.relative_to(root).as_posix()
                except ValueError:continue
                path=self.safe(root,relative)
                if path and path.is_file() and not self.protected(path):
                    meta={'size':path.stat().st_size,'sha256':digest(path)}
                    if expected is None or meta==expected:files[relative]=meta
            self.records[jid]={'root':str(root),'files':files,'ownership':'explicit-v1'}
            self.a.atomic_json(self.path,self.records)

    def preview(self,jid):
        job=self.a.job_by_id(jid)
        if job['status'] in ACTIVE:raise HTTPException(409,'请等待任务完成或先取消，再清理历史素材')
        if job.get('materials_deleted') or (job.get('superseded_by') and not job.get('rollback_directory')):return {'files':[],'skipped':[],'bytes':0,'directory':job['directory']}
        with self.lock:
            if jid not in self.records or self.records[jid].get('ownership')!='explicit-v1':
                return {'files':[],'skipped':[{'name':job['directory'],'reason':'没有生成时的归属校验记录，已保留全部文件'}],'bytes':0,'directory':job['directory']}
            record=self.records[jid];root=Path(record['root']);files=[];skipped=[];size=0
            for relative,meta in record['files'].items():
                path=self.safe(root,relative)
                if path is None:skipped.append({'name':relative,'reason':'路径含链接或已变化'});continue
                if not path.exists():skipped.append({'name':relative,'reason':'已移动或删除'});continue
                if not path.is_file() or self.protected(path) or path.stat().st_size!=meta['size'] or digest(path)!=meta['sha256']:
                    skipped.append({'name':relative,'reason':'已修改或属于视频来源'});continue
                files.append(relative);size+=meta['size']
            for entry in job.get('files',[]):
                if entry['name'] not in record['files']:
                    skipped.append({'name':entry['name'],'reason':'未登记为本任务生成的文件，已保留'})
            return {'files':files,'skipped':skipped,'bytes':size,'directory':str(root)}

    def expanded(self,ids):
        if not isinstance(ids,list) or not ids or len(ids)>10000:raise HTTPException(400,'请先选择需要清理的任务')
        result=set()
        def visit(jid):
            if jid in result:return
            job=self.a.job_by_id(jid)
            if job['status'] in ACTIVE:raise HTTPException(409,'所选任务包含处理中或等待的任务，请先取消')
            result.add(jid)
            for child in job.get('children',[]):visit(child)
        for jid in ids:visit(jid)
        return result

    def shared(self,path,selected):
        target=os.path.normcase(os.path.abspath(path))
        for jid,job in self.a.JOBS.items():
            if jid in selected or job.get('materials_deleted') or job.get('superseded_by'):continue
            # Structural batch parents don't own an additional copy/reference.
            if job.get('kind')=='batch' and selected.intersection(job.get('children',[])):continue
            root=Path(job['directory'])
            for entry in job.get('files',[]):
                # Most jobs point elsewhere. Avoid filesystem/link checks for
                # every unrelated image in a large AI library.
                if os.path.normcase(os.path.abspath(root/entry['name']))!=target:continue
                candidate=self.safe(root,entry['name'])
                if candidate and candidate.resolve()==path.resolve():return True
        packages=getattr(self.a,'MATERIAL_PACKAGES',None)
        if packages:
            for value in packages.roots.values():
                root=Path(value).resolve()
                owned=any(Path(j['directory']).resolve()==root for j in self.a.JOBS.values())
                if not owned and path.resolve().is_relative_to(root):return True
        return False

    def preview_many(self,ids):
        with self.lock:
            selected=self.expanded(ids);files={};skipped=[]
            for jid in sorted(selected):
                preview=self.preview(jid);root=Path(preview['directory'])
                skipped.extend({**item,'job_id':jid} for item in preview['skipped'])
                for name in preview['files']:
                    path=self.safe(root,name)
                    if self.shared(path,selected):skipped.append({'name':str(path),'reason':'仍被其他保留任务或外部资料共同引用','job_id':jid});continue
                    if str(path) in files:files[str(path)]['job_ids'].append(jid)
                    else:files[str(path)]={'path':str(path),'job_id':jid,'job_ids':[jid],'name':name,'size':path.stat().st_size}
            return {'ids':sorted(selected),'files':list(files.values()),'skipped':skipped,'bytes':sum(f['size'] for f in files.values())}

    def clean_many(self,ids):
        with self.lock:
            preview=self.preview_many(ids);errors=[];warnings=[];deleted=0
            results={jid:{'deleted':0,'skipped':[s for s in preview['skipped'] if s.get('job_id')==jid],'errors':[],'warnings':[]} for jid in preview['ids']}
            for entry in preview['files']:
                jid=entry['job_id'];path=Path(entry['path']);meta=self.records[jid]['files'][entry['name']]
                owners=entry['job_ids']
                attempted=False
                try:
                    if self.protected(path) or self.shared(path,set(preview['ids'])) or digest(path)!=meta['sha256']:raise OSError('文件已变化或被其他任务引用，已保留')
                    attempted=True;outcome=recycle(path)
                    if path.exists():raise OSError('回收后文件仍在原位置，已保留')
                    deleted+=1
                    for owner in owners:results[owner]['deleted']+=1
                    if isinstance(outcome,dict) and outcome.get('warning'):
                        note={'name':entry['name'],'reason':outcome['warning'],'job_id':jid};warnings.append(note)
                        for owner in owners:results[owner]['warnings'].append(note)
                except OSError as exc:
                    # Some shell failures are reported after the file has moved.
                    if attempted and not path.exists():
                        deleted+=1
                        note={'name':entry['name'],'reason':'原路径已移走；系统报告异常，请核对回收站：'+str(exc),'job_id':jid};warnings.append(note)
                        for owner in owners:results[owner]['deleted']+=1;results[owner]['warnings'].append(note)
                    else:
                        error={'name':entry['name'],'reason':str(exc),'job_id':jid};errors.append(error)
                        for owner in owners:results[owner]['errors'].append(error)
            for jid in preview['ids']:
                if not self.a.job_by_id(jid).get('superseded_by'):self.a.refresh_files(jid)
                result=results[jid]
                self.a.change(jid,materials_deleted=result['deleted']>0 and not result['errors'],material_cleanup=result)
            for jid in preview['ids']:
                job=self.a.job_by_id(jid);children=job.get('children',[])
                if job.get('kind')=='batch' and children and all(self.a.job_by_id(c).get('materials_deleted') for c in children):
                    if not results[jid]['errors']:self.a.change(jid,materials_deleted=True)
            return {'deleted':deleted,'skipped':preview['skipped'],'errors':errors,'warnings':warnings}

    def clean(self,jid):
        return self.clean_many([jid])

    def replace(self,old_id,new_id):
        """Promote a finished regenerated directory only if the old tree is unchanged.
        Keep a private rollback copy until every path and job record is updated.
        No original video, user-added file, junction, or relocated result is moved.
        """
        with self.lock:
            old=self.a.job_by_id(old_id);new=self.a.job_by_id(new_id)
            if old.get('superseded_by')==new_id:return '上次已覆盖完成，已复用生成结果'
            if old.get('superseded_by') or old.get('materials_deleted'):return '旧素材已被其他任务替换或清理，本次结果另存'
            preview=self.preview(old_id);old_root=Path(preview['directory']);new_root=Path(new['directory'])
            if not old_root.exists():return '旧素材已移动，新结果另存；未追踪或删除移动后的文件'
            actual={p.relative_to(old_root).as_posix() for p in old_root.rglob('*') if p.is_file() or linked(p)}
            if preview['skipped'] or actual!=set(preview['files']):return '旧目录含修改、原片或新增文件，已保留旧目录；新结果另存'
            if new_root.resolve().is_relative_to(old_root.resolve()) or old_root.resolve().is_relative_to(new_root.resolve()):return '输出目录相互包含，已另存新结果'
            self.a.check_cancel(new_id)
            new_files={p.relative_to(new_root).as_posix():digest(p) for p in new_root.rglob('*') if p.is_file()}
            registered={}
            for record in self.records.values():
                if record.get('ownership')!='explicit-v1':continue
                record_root=Path(record['root'])
                if record_root!=new_root and not record_root.is_relative_to(new_root):continue
                for name,meta in record['files'].items():
                    path=self.safe(record_root,name)
                    if path and path.is_file() and not self.protected(path):registered[path.relative_to(new_root).as_posix()]=meta['sha256']
            if new_files!=registered:return '新目录含未登记或已修改文件，已保留全部文件；新结果另存'
            backup=self.a.DATA/'material-rollback'/f'{old_id}-{new_id}';backup.parent.mkdir(exist_ok=True)
            # Same-volume rename is atomic; a cross-volume target is copied to a
            # sibling first, so an interrupted copy cannot damage the old result.
            staged=old_root.with_name('.regenerate-'+new_id)
            if staged.exists():raise ValueError('重新生成的临时位置已存在，请保留新结果并检查目录')
            shutil.copytree(new_root,staged)
            rollback=old_root.with_name('.previous-'+new_id)
            if rollback.exists():raise ValueError('备份位置已存在，请检查目录')
            old_root.rename(rollback)
            try:staged.rename(old_root)
            except Exception:
                rollback.rename(old_root);raise
            replaced=[]
            for item in list(self.a.JOBS.values()):
                path=Path(item['directory'])
                if path==old_root or path.is_relative_to(old_root):
                    replaced.append((item['id'],path.relative_to(old_root)))
                    self.a.change(item['id'],superseded_by=new_id,files=[],frames=0)
            for item in list(self.a.JOBS.values()):
                path=Path(item['directory'])
                if path==new_root or path.is_relative_to(new_root):
                    self.a.change(item['id'],directory=str(old_root/path.relative_to(new_root)));self.a.refresh_files(item['id']);self.record(item['id'])
            # Retain the verified rollback for crash recovery; cleanup never targets it.
            shutil.move(str(rollback),str(backup))
            for jid,relative in replaced:
                if jid in self.records:
                    self.records[jid]['root']=str(backup/relative)
                    self.a.change(jid,rollback_directory=str(backup/relative))
            self.a.atomic_json(self.path,self.records)
            # Remove only the verified generated duplicate used for staging.
            # If another task now uses it as a source, leave that file in place.
            for relative,sha in new_files.items():
                path=self.safe(new_root,relative)
                if path and path.is_file() and not self.protected(path) and digest(path)==sha:path.unlink()
            for folder in sorted([p for p in new_root.rglob('*') if p.is_dir() and not linked(p)],key=lambda p:len(p.parts),reverse=True):
                try:folder.rmdir()
                except OSError:pass
            try:new_root.rmdir()
            except OSError:pass
            return '已覆盖原生成素材目录；导入的原始视频保持不变'


def install(app,a):
    history=History(a)
    @app.post('/api/materials/cleanup-preview')
    def preview_many(req:dict):return history.preview_many(req.get('ids',[]))
    @app.post('/api/materials/cleanup-batch')
    def clean_many(req:dict):return history.clean_many(req.get('ids',[]))
    @app.get('/api/materials/{jid}/cleanup')
    def preview(jid:str):return history.preview(jid)
    @app.post('/api/materials/{jid}/cleanup')
    def clean(jid:str):return history.clean(jid)
    return history


def register_created(a,jid,paths):
    history=getattr(a,'MATERIAL_HISTORY',None)
    if history is not None:history.created(jid,paths)
