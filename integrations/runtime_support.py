"""Read startup capability metadata without loading any optional native engine."""
import json
import os
from pathlib import Path

LABELS = {'base':'基础工作台','media':'音视频处理','ocr':'画面文字识别', 'asr':'本地语音识别', 'dedup':'相似图片去重',
          'node':'分镜与 YouTube 脚本', 'crawler':'MediaCrawler 平台采集',
          'download':'链接下载', 'browser':'登录浏览器'}


def available(name):
    return name not in set(os.environ.get('VIDEO_DESK_DISABLED_COMPONENTS','').split(','))


def require(name):
    if not available(name):
        raise ValueError(LABELS.get(name,name)+'组件暂不可用，请从系统托盘打开“检测并修复”；账号和素材会保留')


def status():
    components=[]
    path=os.environ.get('VIDEO_DESK_RUNTIME_REPORT','')
    if path:
        try:
            report=json.loads(Path(path).read_text('utf-8-sig'))
            components=[{'id':v['id'],'name':v['name'],'ready':bool(v['ready']),
                         'message':v.get('message','')} for v in report.get('components',[])]
        except (OSError,ValueError,KeyError,TypeError):pass
    known={v['id'] for v in components}
    for name,label in LABELS.items():
        if not available(name) and name not in known:
            components.append({'id':name,'name':label,'ready':False,'message':'组件暂不可用，请检测并修复'})
    return {'managed':os.environ.get('VIDEO_DESK_LAUNCHER')=='1',
            'degraded':any(not v['ready'] for v in components), 'components':components,
            'repair_hint':'从系统托盘打开“检测并修复”。修复保留账号、设置和素材。'}
