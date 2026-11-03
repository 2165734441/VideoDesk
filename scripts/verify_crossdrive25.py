"""Run with --output on a different drive from .local/v25-qa/data."""
import argparse
import functools
import hashlib
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from urllib.parse import quote
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from verify_v25 import QA,api,wait


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    assert output.drive.lower()!=(QA/'data').drive.lower(),'Use a different output drive'
    sid=json.loads((QA/'acceptance25.json').read_text('utf-8'))['probe']['id'];sample=QA/'sample.mp4'
    original=hashlib.sha256(sample.read_bytes()).hexdigest()
    parent=api('/api/batches',{'sources':[sid],'output':str(output),'kind':'rules','workflow':'rules',
        'outputs':{'frames':True,'zip':True},'frames':{'mode':'seconds','value':3},'request_id':uuid.uuid4().hex})
    result,_=wait(parent);child=result['children'][0];names=[x['name'] for x in child['files']]
    assert len([n for n in names if n.endswith('.jpg')])==7 and '素材包.zip' in names,names
    assert (QA/'data/batch-work'/child['id']/'ffmpeg.log').is_file()
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Quiet,directory=str(QA/'formats')))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        links=[{'input':f'http://127.0.0.1:{server.server_port}/'+quote(name),'episode':1,'episode_title':'跨盘合集'} for name in ('h264.mp4','旋转 空格.mp4')]
        parent=api('/api/batches',{'sources':['online'],'online_sources':links,'output':str(output),'kind':'board','workflow':'board',
            'analysis':{'duration':1,'transcript':'off','max_cells_per_sheet':4},'ai_package':{'screen_text':False,'combined':True},'request_id':uuid.uuid4().hex})
        result,_=wait(parent);base=Path(result['job']['directory']);index=json.loads((base/'分集目录.json').read_text('utf-8'))
        assert len(index)==1 and len(index[0]['videos'])==2
        assert (base/index[0]['directory']/index[0]['package']/'一起上传给AI/00_阅读说明.txt').is_file()
        assert (base/'联合AI分析包/一起上传给AI/00_阅读说明.txt').is_file()
    finally:server.shutdown();server.server_close()
    assert hashlib.sha256(sample.read_bytes()).hexdigest()==original
    (QA/'crossdrive25.json').write_text(json.dumps({'passed':True,'data_drive':(QA/'data').drive,'output_drive':output.drive,
        'rules_images':7,'zip_published':True,'log_archived':True,'episode_videos':2,'episode_and_joint_package':True,'original_unchanged':True},indent=2),'utf-8')
    print('CROSS DRIVE RULES ZIP, LOGS AND EPISODE COMBINE PASSED',flush=True)


if __name__=='__main__':main()
