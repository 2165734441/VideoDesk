"""Persistent manual-login browser; reads only the requested ordinary player."""
import concurrent.futures
import os
from pathlib import Path
import time
from urllib.parse import urlparse

from .video_sources import douyin_video_id


def detail_media(payload, video_id):
    detail = payload.get('aweme_detail') or {}
    if str(detail.get('aweme_id', '')) != video_id:
        return None
    video = detail.get('video') or {}
    address = video.get('play_addr') or {}
    urls = address.get('url_list') or []
    url = next((v for v in urls if isinstance(v,str) and urlparse(v).scheme in ('http','https')), None)
    if not url:
        return None
    return {'url':url,'title':detail.get('desc') or '抖音视频',
            'duration':video.get('duration',0)/1000 or None,
            'width':video.get('width'),'height':video.get('height'),'video_id':video_id}


class LoginBrowser:
    def __init__(self, data):
        self.data=Path(data)
        self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix='login')
        self.context=None
        self.engine=None
        self.details={}
        self.observed=set()

    def observe(self,page):
        if page in self.observed:return
        self.observed.add(page)
        def response(value):
            # These are normal responses to the user's browser navigation.
            # Do not construct signed API requests or solve challenges.
            if value.status != 200:return
            if any(s in value.url for s in ('/user/following/', '/following/list/')):
                try:
                    from .live_browser import following_data
                    payload=value.json()
                    # First page starts a new import snapshot, preventing account switches
                    # from merging the previous account's following list.
                    from urllib.parse import parse_qs
                    query=parse_qs(urlparse(value.url).query)
                    first=query.get('max_time',[''])[0] in ('0','') and query.get('cursor',['0'])[0]=='0'
                    previous=getattr(self,'live_following',{})
                    users={} if first or time.monotonic()-previous.get('time',0)>300 else dict(previous.get('users',{}))
                    for item in following_data(payload):users[item['url']]=item
                    self.live_following={'users':users,'has_more':payload.get('has_more'),'time':time.monotonic()}
                except Exception:pass
                return
            if '/aweme/v1/web/aweme/detail/' not in value.url:return
            try:
                payload=value.json();detail=payload.get('aweme_detail') or {}
                vid=str(detail.get('aweme_id',''));media=detail_media(payload,vid)
                if media:self.details[(page,vid)]=media
            except Exception:pass
        page.on('response',response)

    def ensure(self):
        from .runtime_support import require
        require('browser')
        if self.context:
            try:self.context.cookies();return self.context
            except Exception:self.context=None
        from playwright.sync_api import sync_playwright
        if not self.engine:self.engine=sync_playwright().start()
        bundled=os.environ.get('VIDEO_DESK_BROWSER_EXE')
        choices=[{'executable_path':bundled}] if bundled else []
        choices += [{'channel':c} for c in ('msedge','chrome')]
        for options in choices:
            try:
                self.context=self.engine.chromium.launch_persistent_context(
                    str(self.data/'browser-profile'),**options,headless=False,viewport=None,accept_downloads=False)
                self.details.clear();self.observed.clear()
                for page in self.context.pages:self.observe(page)
                self.context.on('page',self.observe)
                return self.context
            except Exception:pass
        raise RuntimeError('登录浏览器未能打开。请关闭上次映格的登录窗口后重试，或安装 Edge / Chrome。')

    def page_for(self,url,navigate=False):
        context=self.ensure();vid=douyin_video_id(url)
        page=next((p for p in reversed(context.pages) if not p.is_closed() and
                   (douyin_video_id(p.url)==vid if vid else p.url==url)),None)
        if page is None:
            page=context.new_page();self.observe(page)
            if navigate:
                try:page.goto(url,wait_until='domcontentloaded',timeout=30000)
                except Exception:pass
        return page

    def open(self,url):
        def action():
            page=self.page_for(url,navigate=True);page.bring_to_front()
            return {'message':'请在映格登录浏览器中确认视频可以播放，完成验证后返回继续。'}
        return self.pool.submit(action)

    def cookies(self,url):
        # Reopen the saved profile after service restart, instead of returning
        # an empty jar simply because this process has not opened a page yet.
        return self.pool.submit(lambda:self.ensure().cookies([url])).result(timeout=45)

    def close(self):
        def action():
            if self.context:self.context.close();self.context=None
            if self.engine:self.engine.stop();self.engine=None
        try:self.pool.submit(action).result(timeout=15)
        finally:self.pool.shutdown(wait=False)

    def playable_media(self,url):
        if not douyin_video_id(url):return None
        def action():
            vid=douyin_video_id(url);page=self.page_for(url,navigate=True)
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                if page.is_closed():return None
                # Navigation to a different video must never export it under
                # the requested video's id.
                if douyin_video_id(page.url)!=vid:return None
                exact=self.details.get((page,vid))
                if exact:return {**exact,'user_agent':page.evaluate('navigator.userAgent')}
                media=page.evaluate("""() => {
                    const videos=[...document.querySelectorAll('video')].filter(v=>{
                        const r=v.getBoundingClientRect();const s=getComputedStyle(v);
                        return r.width>80&&r.height>80&&r.bottom>0&&r.top<innerHeight&&
                            s.visibility!=='hidden'&&s.display!=='none'&&v.readyState>=2;
                    }).sort((a,b)=>Number(a.paused)-Number(b.paused)||
                        b.getBoundingClientRect().width*b.getBoundingClientRect().height-
                        a.getBoundingClientRect().width*a.getBoundingClientRect().height);
                    const v=videos[0];if(!v)return null;
                    return {url:v.currentSrc||v.src,duration:Number.isFinite(v.duration)?v.duration:null,
                        width:v.videoWidth,height:v.videoHeight,title:document.title,user_agent:navigator.userAgent};
                }""")
                if media and urlparse(media.get('url','')).scheme in ('http','https'):return media
                page.wait_for_timeout(400)
            page.bring_to_front()
            return None
        return self.pool.submit(action).result(timeout=80)
