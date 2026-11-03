"""Recover task metadata after an unclean machine shutdown; never change media."""
import json
from pathlib import Path
import time
from .storage import atomic_json


def valid_jobs(path):
    value=json.loads(Path(path).read_text('utf-8'))
    if not isinstance(value,list) or any(not isinstance(j,dict) or not all(k in j for k in ('id','status','directory','kind','options')) for j in value):
        raise ValueError('任务记录结构无效')
    return value


def load_jobs(data,output):
    data=Path(data);path=data/'jobs.json'
    if not path.exists():return []
    try:return valid_jobs(path)
    except (OSError,ValueError):pass
    # Keep evidence before any replacement, including a zero-filled file.
    damaged=data/('jobs.corrupt-'+str(time.time_ns())+'.json')
    damaged.write_bytes(path.read_bytes())
    try:
        jobs=valid_jobs(path.with_suffix('.json.bak'));method='从备份恢复'
    except (OSError,ValueError):
        roots={Path(output)}
        try:
            history=json.loads((data/'material-history.json').read_text('utf-8'))
            roots.update(Path(v['root']) for v in history.values())
            roots.update(Path(v['root']).parent for v in history.values())
        except (OSError,ValueError,KeyError):pass
        manifests=set()
        for root in roots:
            if not root.is_dir() or root.is_symlink() or (hasattr(root,'is_junction') and root.is_junction()):continue
            if (root/'manifest.json').is_file():manifests.add(root/'manifest.json')
            # Bounded discovery: only immediate material directories, no drive scan.
            for folder in root.iterdir():
                if folder.is_dir() and not folder.is_symlink() and not (hasattr(folder,'is_junction') and folder.is_junction()) and (folder/'manifest.json').is_file():manifests.add(folder/'manifest.json')
        found={}
        for manifest in sorted(manifests):
            try:
                j=json.loads(manifest.read_text('utf-8'));jid=j['id'];base=manifest.parent
                files=[{'name':p.relative_to(base).as_posix(),'size':p.stat().st_size} for p in base.rglob('*') if p.is_file() and not any(x.startswith('.') for x in p.relative_to(base).parts)]
                found[jid]={**j,'directory':str(base),'options':j.get('options',{}),'source':j.get('source',''),
                    'status':'interrupted','progress':0,'created':j.get('created',manifest.stat().st_mtime),'updated':time.time(),
                    'message':'系统异常关闭后从素材恢复。已有文件可查看，请重试未完成的分析。','files':files,
                    'frames':sum(p['name'].lower().endswith(('.jpg','.png')) for p in files),'recovered':True}
            except (OSError,ValueError,KeyError,TypeError):continue
        jobs=sorted(found.values(),key=lambda j:j['created']);method='从现存素材清单恢复'
    atomic_json(path,jobs,backup=True)
    atomic_json(data/'job-recovery.json',{'time':time.time(),'method':method,'count':len(jobs),'damaged':str(damaged)})
    return jobs
